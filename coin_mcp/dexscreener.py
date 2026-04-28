"""DexScreener tools — DEX-side prices, liquidity and volume across all chains.

Use these when a token is too new or too small to appear in CoinGecko's
aggregation (i.e. `get_price` returns empty for it), or when the user
specifically wants the DEX / on-chain price rather than the CEX-weighted
aggregate. DexScreener indexes pairs on every major chain (ethereum, bsc,
solana, polygon, arbitrum, base, optimism, avalanche, and many more).
"""
from __future__ import annotations

import re
from typing import Any

from .core import DEXSCREENER_BASE, _http_get, mcp

try:
    from .core import is_error
except ImportError:
    def is_error(obj: Any) -> bool:
        return isinstance(obj, dict) and bool(obj.get("error"))


# ---------- Helpers ----------


# Path-injection guards: chain ids and pair/token addresses interpolated into
# URL paths must match these. Reject anything containing /, ?, #, .., %, or
# whitespace by virtue of the strict character classes below.
_CHAIN_ID_RE = re.compile(r'^[a-z0-9-]{1,32}$')
_ADDRESS_RE = re.compile(r'^(0x[a-fA-F0-9]{40}|[1-9A-HJ-NP-Za-km-z]{32,44})$')


def _validate_chain_id(value: str) -> dict | None:
    if not isinstance(value, str) or not _CHAIN_ID_RE.match(value):
        return {
            "error": (
                f"invalid chain_id: must match ^[a-z0-9-]{{1,32}}$, got {value!r}"
            )
        }
    return None


def _validate_address(value: str, kind: str = "address") -> dict | None:
    if not isinstance(value, str) or not _ADDRESS_RE.match(value):
        return {
            "error": (
                f"invalid {kind}: must be EVM hex (0x + 40 hex) or Solana base58 "
                f"(32-44 chars), got {value!r}"
            )
        }
    return None


def _liquidity_usd(pair: dict[str, Any]) -> float:
    """Pull liquidity.usd as a float (0.0 if missing) — used for sorting."""
    liq = pair.get("liquidity") or {}
    val = liq.get("usd")
    try:
        return float(val) if val is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _trim_pair(pair: dict[str, Any]) -> dict[str, Any]:
    """Reduce a DexScreener pair object to the most useful fields.

    Drops bulky / low-signal fields like `info` (images, socials) and the full
    `txns` history. Keeps prices, liquidity, 24h volume / change, fdv / mcap,
    and identifiers needed to fetch full detail via `get_dex_pair`.
    """
    base = pair.get("baseToken") or {}
    quote = pair.get("quoteToken") or {}
    volume = pair.get("volume") or {}
    change = pair.get("priceChange") or {}
    liquidity = pair.get("liquidity") or {}
    return {
        "chainId": pair.get("chainId"),
        "dexId": pair.get("dexId"),
        "pairAddress": pair.get("pairAddress"),
        "url": pair.get("url"),
        "baseToken": {
            "address": base.get("address"),
            "name": base.get("name"),
            "symbol": base.get("symbol"),
        },
        "quoteToken": {
            "address": quote.get("address"),
            "name": quote.get("name"),
            "symbol": quote.get("symbol"),
        },
        "priceNative": pair.get("priceNative"),
        "priceUsd": pair.get("priceUsd"),
        "liquidityUsd": liquidity.get("usd"),
        "volumeH24": volume.get("h24"),
        "priceChangeH24": change.get("h24"),
        "fdv": pair.get("fdv"),
        "marketCap": pair.get("marketCap"),
        "pairCreatedAt": pair.get("pairCreatedAt"),
    }


def _sorted_trimmed(pairs: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    pairs_sorted = sorted(pairs, key=_liquidity_usd, reverse=True)
    return [_trim_pair(p) for p in pairs_sorted[: max(0, limit)]]


# ---------- Tools ----------


@mcp.tool()
async def dex_search(query: str, limit: int = 10) -> Any:
    """Search DEX pairs across all chains by token name, symbol, or address.

    Use this when the token is too new or small for CoinGecko (`get_price`
    returns empty), or when the user wants DEX-side / on-chain prices
    specifically. Returns the most-liquid matching pairs first so the top
    result is usually the "real" market for the token.

    Args:
        query: Free-text query — token name ("BasedPepe"), ticker ("PEPE"),
            or contract address. DexScreener returns up to 30 pairs per call.
        limit: Max pairs to return after sorting by USD liquidity desc (1..30).

    Returns:
        Array of trimmed pair objects with fields:
          chainId, dexId, pairAddress, url, baseToken{address,name,symbol},
          quoteToken{...}, priceNative, priceUsd, liquidityUsd, volumeH24,
          priceChangeH24, fdv, marketCap, pairCreatedAt.
        On API failure returns `{"error": "..."}` from the HTTP layer.
    """
    resp = await _http_get(
        f"{DEXSCREENER_BASE}/latest/dex/search",
        params={"q": query},
    )
    if is_error(resp):
        return resp
    pairs = (resp or {}).get("pairs") or []
    return _sorted_trimmed(pairs, limit)


@mcp.tool()
async def get_dex_token_pairs(
    token_address: str,
    limit: int = 10,
    chain: str = "",
) -> Any:
    """Get all DEX pairs trading a given token contract, across every chain.

    Use this when you have an EVM/Solana contract address and want to see
    every venue where it's traded — useful for "which DEX has the deepest
    liquidity for this token?" and for finding the canonical pair on a
    specific chain. Pairs are returned sorted by USD liquidity desc.

    Prefer this over `dex_search` when you already have the contract address;
    prefer `get_price` (CoinGecko) when the token is large and listed on CEXes.

    Args:
        token_address: Token contract address. EVM addresses like
            "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48" (USDC on Ethereum)
            and Solana mint addresses both work.
        limit: Max pairs to return (1..30 typical).
        chain: Optional chain filter — only return pairs on this chain.
            Common values: "ethereum", "bsc", "solana", "polygon", "arbitrum",
            "base", "optimism", "avalanche". Leave empty for all chains.

    Returns:
        Array of trimmed pair objects (same shape as `dex_search`).
        On API failure returns `{"error": "..."}`.

    Note: `token_address` is validated against EVM hex or Solana base58
    (rejects anything containing `/`, `?`, `#`, `..`, `%`, or whitespace).
    """
    err = _validate_address(token_address, "token_address")
    if err is not None:
        return err
    resp = await _http_get(f"{DEXSCREENER_BASE}/latest/dex/tokens/{token_address}")
    if is_error(resp):
        return resp
    pairs = (resp or {}).get("pairs") or []
    if chain:
        chain_lc = chain.lower().strip()
        pairs = [p for p in pairs if (p.get("chainId") or "").lower() == chain_lc]
    return _sorted_trimmed(pairs, limit)


@mcp.tool()
async def get_dex_pair(chain_id: str, pair_address: str) -> Any:
    """Get full detail for a single DEX pair on a specific chain.

    Use after `dex_search` / `get_dex_token_pairs` when the user wants the
    full picture for one specific market — including buy/sell tx counts at
    multiple windows (m5/h1/h6/h24), price changes, native-token price,
    base/quote liquidity sides, and links/socials when present.

    Args:
        chain_id: Chain identifier as DexScreener returns it. Typical values:
            "ethereum", "bsc", "solana", "polygon", "arbitrum", "base",
            "optimism", "avalanche", "pulsechain", "fantom", "cronos", ...
        pair_address: The pair's contract address (case-insensitive on EVM).

    Returns:
        The first matching pair object as returned by DexScreener (with the
        full `txns`, `info`, `volume`, `priceChange`, `liquidity` sub-objects),
        or `{"error": "pair not found"}` if no pair matches, or the structured
        HTTP error dict on transport failure.

    Note: `chain_id` is validated against `^[a-z0-9-]{1,32}$` and
    `pair_address` against EVM hex / Solana base58.
    """
    err = _validate_chain_id(chain_id)
    if err is not None:
        return err
    err = _validate_address(pair_address, "pair_address")
    if err is not None:
        return err
    resp = await _http_get(
        f"{DEXSCREENER_BASE}/latest/dex/pairs/{chain_id}/{pair_address}"
    )
    if is_error(resp):
        return resp
    pairs = (resp or {}).get("pairs") or []
    if not pairs:
        return {"error": "pair not found"}
    return pairs[0]


@mcp.tool()
async def list_latest_dex_tokens(limit: int = 20) -> Any:
    """List the latest tokens that have set up a profile on DexScreener.

    A profile means the project has filled in description / website / socials
    on DexScreener — typically a sign of a newly-launched but at least somewhat
    promoted token. Useful as an early-signal feed for "what's new today?"

    NOTE: a profile does not imply legitimacy or liquidity. Cross-check with
    `get_dex_token_pairs(token_address, ...)` before quoting prices.

    Args:
        limit: Max tokens to return (the API itself returns up to ~30).

    Returns:
        Array of token-profile objects with `chainId`, `tokenAddress`, `url`,
        `description`, `icon`, and a `links` array of websites/socials.
        On API failure returns `{"error": "..."}`.
    """
    resp = await _http_get(f"{DEXSCREENER_BASE}/token-profiles/latest/v1")
    if is_error(resp):
        return resp
    items = resp if isinstance(resp, list) else []
    return items[: max(0, limit)]


@mcp.tool()
async def list_top_boosted_tokens(limit: int = 20) -> Any:
    """List currently top-boosted tokens on DexScreener (paid promotion).

    "Boosts" are paid promotion slots — projects pay to surface their token.
    Read these results with skepticism: high boost spend does NOT imply
    quality, traction or safety. This list is mostly useful as a signal of
    "what is being actively pushed right now" rather than "what is good".

    For an unbiased early-tokens view prefer `list_latest_dex_tokens`; for
    actual price/liquidity always confirm via `get_dex_token_pairs`.

    Args:
        limit: Max tokens to return.

    Returns:
        Array of boosted-token objects with `chainId`, `tokenAddress`, `url`,
        `description`, `icon`, `links`, and `totalAmount` (boost spend).
        On API failure returns `{"error": "..."}`.
    """
    resp = await _http_get(f"{DEXSCREENER_BASE}/token-boosts/top/v1")
    if is_error(resp):
        return resp
    items = resp if isinstance(resp, list) else []
    return items[: max(0, limit)]
