"""In-process TTL cache for outbound HTTP GETs.

Wraps the same call shape as `core._http_get` so callers don't need to know
caching exists. Per-URL-pattern TTLs are tuned to CoinGecko's data freshness
(prices change second-by-second; the exchanges directory rarely changes) so
that repeated identical requests stay well under the public ~30 req/min limit.

Also registers two MCP tools (`cache_stats`, `clear_cache`) for introspection.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import time
from collections import OrderedDict
from typing import Any
from urllib.parse import urlsplit

import httpx

from .core import DEFAULT_TIMEOUT, mcp

MAX_ENTRIES = 2000
DEFAULT_TTL = 30.0

# Path-prefix rules (matched against urlsplit(url).path with str.startswith).
# Order matters: first match wins, so put more specific prefixes earlier.
_PATH_TTL_RULES: list[tuple[str, float, str]] = [
    ("/api/v3/simple/price", 10.0, "simple/price"),
    ("/api/v3/coins/markets", 60.0, "coins/markets"),
    ("/api/v3/coins/top_gainers_losers", 60.0, "top_gainers_losers"),
    ("/api/v3/coins/categories", 300.0, "coins/categories"),
    ("/api/v3/search/trending", 60.0, "search/trending"),
    ("/api/v3/search", 300.0, "search"),
    ("/api/v3/global", 60.0, "global"),
    ("/api/v3/derivatives/exchanges", 600.0, "derivatives/exchanges"),
    ("/api/v3/exchanges/", 600.0, "exchanges (single)"),
    ("/api/v3/exchanges", 1800.0, "exchanges (list)"),
    ("/api/v3/nfts/list", 1800.0, "nfts/list"),
    ("/api/v3/nfts/", 300.0, "nfts (single)"),
    ("/api/v3/companies/public_treasury", 1800.0, "companies/public_treasury"),
    ("/api/v3/coins/", 120.0, "coins/{id}"),
]

# Path-substring rules — for endpoints whose prefix is parameterized (e.g.
# `/api/v3/coins/{id}/market_chart`) and so can't be matched as a startswith.
_PATH_CONTAINS_RULES: list[tuple[str, float, str]] = [
    ("/market_chart", 60.0, "market_chart"),
    ("/ohlc", 60.0, "ohlc"),
    ("/tickers", 30.0, "tickers"),
]

# Netloc (hostname) rules for whole-domain TTLs.
_NETLOC_TTL_RULES: list[tuple[str, float, str]] = [
    ("api.alternative.me", 600.0, "alternative.me"),
]

# Auth-bearing headers that must contribute to the cache key. All other
# headers (User-Agent, Accept, etc.) are ignored — only request identity
# matters, not transport flair.
_AUTH_HEADERS = ("x-cg-pro-api-key", "x-cg-demo-api-key", "authorization", "cookie")

_DEFAULT_LABEL = "default"


def _classify(url: str, headers: dict[str, str] | None = None) -> tuple[str, float]:
    """Return (label, ttl_seconds) for a URL.

    The optional `headers` arg is accepted for symmetry with `_make_key` but
    isn't currently used to route TTLs; classification is host+path driven.
    """
    parts = urlsplit(url)
    netloc = parts.netloc.lower()
    path = parts.path

    for needle, ttl, label in _NETLOC_TTL_RULES:
        if netloc == needle:
            return label, ttl

    # Contains-rules first: parameterized middle-path endpoints
    # (e.g. /coins/{id}/market_chart) must claim their URLs before the broader
    # /coins/ prefix rule below picks them up with a wrong TTL.
    for needle, ttl, label in _PATH_CONTAINS_RULES:
        if needle in path:
            return label, ttl

    for prefix, ttl, label in _PATH_TTL_RULES:
        if path.startswith(prefix):
            return label, ttl

    return _DEFAULT_LABEL, DEFAULT_TTL


def _hash_header_value(value: str) -> str:
    """Short stable digest of a header value, for use in the cache key."""
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]


def _auth_signature(headers: dict[str, str] | None) -> tuple:
    """Extract a (case-insensitive) tuple of digested auth headers.

    Auth-bearing headers MUST contribute to the cache key so multi-tenant
    deployments don't leak responses across users. Header *values* are
    hashed (we don't want to retain raw API keys in the cache index).
    """
    if not headers:
        return ()
    lowered = {k.lower(): v for k, v in headers.items() if isinstance(k, str)}
    sig: list[tuple[str, str]] = []
    for name in _AUTH_HEADERS:
        value = lowered.get(name)
        if value is None or value == "":
            continue
        sig.append((name, _hash_header_value(str(value))))
    return tuple(sig)


def _make_key(
    method: str,
    url: str,
    params: dict[str, Any] | None,
    headers: dict[str, str] | None = None,
) -> tuple:
    parts = urlsplit(url)
    # Drop fragment; keep scheme+netloc+path so identical paths on different
    # hosts don't collide.
    normalized = f"{parts.scheme}://{parts.netloc}{parts.path}"
    sorted_params = tuple(sorted((params or {}).items()))
    return (method.upper(), normalized, sorted_params, _auth_signature(headers))


# entry: (expires_at, value)
_store: "OrderedDict[tuple, tuple[float, Any]]" = OrderedDict()

_global_counters = {"hits": 0, "misses": 0, "sets": 0, "errors": 0}
_pattern_counters: dict[str, dict[str, int]] = {}


def _bump(label: str, field: str) -> None:
    _global_counters[field] += 1
    bucket = _pattern_counters.setdefault(
        label, {"hits": 0, "misses": 0, "sets": 0, "errors": 0}
    )
    bucket[field] += 1


def _get(key: tuple) -> Any | None:
    entry = _store.get(key)
    if entry is None:
        return None
    expires_at, value = entry
    if expires_at < time.monotonic():
        _store.pop(key, None)
        return None
    _store.move_to_end(key)
    return value


def _set(key: tuple, value: Any, ttl: float) -> None:
    _store[key] = (time.monotonic() + ttl, value)
    _store.move_to_end(key)
    while len(_store) > MAX_ENTRIES:
        _store.popitem(last=False)


# ---------- Module-level shared httpx client ----------

# Reusing a single AsyncClient amortizes TCP/TLS handshake cost across all
# outbound GETs (10-30x latency reduction for sequential calls). The client is
# created lazily on first use so importing this module doesn't open sockets.
_client: httpx.AsyncClient | None = None
_client_lock = asyncio.Lock()


async def _get_client() -> httpx.AsyncClient:
    """Return a process-wide shared `httpx.AsyncClient`, creating it on demand.

    Safe to call concurrently. Not closed explicitly — interpreter exit will
    release sockets.
    """
    global _client
    if _client is None or _client.is_closed:
        async with _client_lock:
            if _client is None or _client.is_closed:
                _client = httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, http2=False)
    return _client


async def cached_http_get(
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    """Cache-aware drop-in for `core._http_get`.

    Returns parsed JSON or `{"error": ...}`, matching the original contract.
    Error responses (envelopes that `core.is_error` recognizes) are NOT
    cached. On a cache hit, a deep copy is returned so callers that mutate
    (sort, slice, augment) the response don't corrupt other callers' views.
    """
    # Local import so tests can monkeypatch core.is_error if ever needed and
    # to keep the cache module's symbol surface minimal.
    from . import core

    label, ttl = _classify(url, headers)
    key = _make_key("GET", url, params, headers)

    cached = _get(key)
    if cached is not None:
        _bump(label, "hits")
        return copy.deepcopy(cached)

    _bump(label, "misses")

    client = await _get_client()
    r = await client.get(url, params=params, headers=headers)
    if r.status_code >= 400:
        result = {
            "error": f"HTTP {r.status_code}",
            "url": str(r.request.url),
            "body": r.text[:500],
        }
        _bump(label, "errors")
        return result
    try:
        result = r.json()
    except ValueError:
        _bump(label, "errors")
        return {"error": "non-JSON response", "body": r.text[:500]}

    if core.is_error(result):
        _bump(label, "errors")
        return result

    _set(key, result, ttl)
    _bump(label, "sets")
    # Return an independent copy so the caller mutating the result can't
    # poison the cached entry either.
    return copy.deepcopy(result)


def get_stats() -> dict[str, Any]:
    hits = _global_counters["hits"]
    misses = _global_counters["misses"]
    total = hits + misses
    hit_rate = (hits / total) if total else 0.0
    return {
        "entries": len(_store),
        "max_entries": MAX_ENTRIES,
        "hits": hits,
        "misses": misses,
        "sets": _global_counters["sets"],
        "errors": _global_counters["errors"],
        "hit_rate": round(hit_rate, 4),
        "by_pattern": {
            label: dict(counts) for label, counts in _pattern_counters.items()
        },
    }


def clear() -> int:
    n = len(_store)
    _store.clear()
    return n


@mcp.tool()
async def cache_stats() -> dict[str, Any]:
    """Return current HTTP-cache statistics.

    Reach for this when:
    - The user asks why a price/value looks stale or is identical to a previous
      query (responses may be served from cache up to the per-endpoint TTL).
    - You're debugging rate-limit (HTTP 429) errors and want to confirm the
      cache is doing its job.
    - The user explicitly asks about cache utilization or hit rate.

    Returns:
        Dict with keys:
            entries: number of live cached entries
            max_entries: LRU eviction threshold
            hits, misses, sets, errors: cumulative counters since process start
            hit_rate: hits / (hits + misses), 0.0 if no requests yet
            by_pattern: per-endpoint-pattern breakdown of the same counters
    """
    return get_stats()


@mcp.tool()
async def clear_cache() -> dict[str, int]:
    """Drop every entry from the HTTP cache.

    Reach for this when:
    - The user explicitly asks to refresh / invalidate cached data.
    - You suspect cached data is materially stale and a TTL has not yet
      expired (e.g. user reports a price that disagrees with their exchange).

    Prefer letting TTLs expire naturally; this tool resets ALL endpoints, not
    just one. Counters (hits/misses/...) are preserved.

    Returns:
        {"cleared": <number of entries dropped>}
    """
    return {"cleared": clear()}
