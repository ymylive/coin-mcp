"""Cross-source aggregation tools — health checks, price comparisons, consolidated order books.

These tools fan out concurrently to multiple data sources (CoinGecko, CCXT
exchanges, DefiLlama, DexScreener, Alternative.me) and merge the results.
Partial failures are preserved so the LLM can attribute who answered and who
didn't — never raise; always return a structured envelope.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from .core import (
    ALTERNATIVE_BASE,
    COINGECKO_PUBLIC_BASE,
    DEFILLAMA_BASE,
    DEXSCREENER_BASE,
    _ccxt_call,
    _cg_get,
    _get_ccxt_exchange,
    _http_get,
    is_error,
    mcp,
)


# ---------- helpers ----------


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _short(text: Any, n: int = 100) -> str:
    s = str(text)
    return s if len(s) <= n else s[:n]


async def _prewarm_exchange(exchange_id: str) -> dict | None:
    """Construct/cache the CCXT exchange OUTSIDE the `_ccxt_call` per-id lock.

    `_ccxt_call(..., exchange_id=...)` takes a non-reentrant per-id lock that
    `_get_ccxt_exchange` ALSO takes during construction; building a fresh
    instance inside the locked call deadlocks. Pre-warming on a separate
    executor task makes the subsequent `_get_ccxt_exchange` call hit the LRU
    cache fast-path which skips the lock.

    Returns None on success, `{"error": ...}` envelope on construction failure.
    """
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, _get_ccxt_exchange, exchange_id)
        return None
    except Exception as e:
        return {"error": f"{type(e).__name__}: {_short(e)}"}


async def _timed(
    source: str,
    coro_fn: Callable[[], Awaitable[Any]],
    success_detail: Callable[[Any], str],
) -> dict[str, Any]:
    """Run `coro_fn()`, time it, and shape the result into a health row.

    On success: ok=True with `success_detail(resp)` as the detail string.
    On `is_error` envelope or raised exception: ok=False with diagnostic detail.
    """
    t0 = time.perf_counter()
    try:
        resp = await coro_fn()
    except Exception as e:
        return {
            "source": source, "ok": False,
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "detail": f"{type(e).__name__}: {_short(e)}",
        }
    elapsed = int((time.perf_counter() - t0) * 1000)
    if is_error(resp):
        return {
            "source": source, "ok": False, "latency_ms": elapsed,
            "detail": _short(resp.get("error", "")),
        }
    try:
        detail = _short(success_detail(resp))
    except Exception as e:
        detail = f"detail-extract-failed: {type(e).__name__}: {_short(e)}"
    return {"source": source, "ok": True, "latency_ms": elapsed, "detail": detail}


async def _safe_ccxt(
    exchange_id: str,
    do_fn: Callable[[], Any],
    *,
    warm_timeout: float = 20.0,
    call_timeout: float = 15.0,
) -> dict[str, Any]:
    """Pre-warm exchange + run `do_fn` via `_ccxt_call` with outer timeouts.

    Returns the CCXT call's response on success, or `{"error": "..."}` on
    timeout / construction failure / CCXT-level error.
    """
    try:
        warm_err = await asyncio.wait_for(
            _prewarm_exchange(exchange_id), timeout=warm_timeout
        )
    except Exception as e:
        return {"error": f"{type(e).__name__}: {_short(e)}"}
    if warm_err is not None:
        return warm_err
    try:
        return await asyncio.wait_for(
            _ccxt_call(do_fn, exchange_id=exchange_id), timeout=call_timeout
        )
    except Exception as e:
        return {"error": f"{type(e).__name__}: {_short(e)}"}


# CoinGecko coin_id -> CCXT base symbol. Small mapping for top-cap coins;
# unknown ids return an error hint pointing at `get_exchange_ticker`.
_CG_TO_CCXT_BASE = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "solana": "SOL",
    "ripple": "XRP",
    "cardano": "ADA",
    "dogecoin": "DOGE",
    "tron": "TRX",
    "polkadot": "DOT",
    "chainlink": "LINK",
    "avalanche-2": "AVAX",
    "matic-network": "MATIC",
}


def _ccxt_quote_for(vs_currency: str) -> str:
    """`usd` -> `USDT` (typical CEX quote); otherwise uppercase the input."""
    vs = (vs_currency or "").strip().lower()
    return "USDT" if vs == "usd" else vs.upper()


# ---------- 1. health_check ----------


def _cg_detail(resp: Any) -> str:
    return (resp or {}).get("gecko_says") or "ok"


def _llama_detail(resp: Any) -> str:
    return f"{len(resp) if isinstance(resp, list) else 0} protocols"


def _dex_detail(resp: Any) -> str:
    return f"{len((resp or {}).get('pairs') or [])} pairs"


def _fng_detail(resp: Any) -> str:
    data = (resp or {}).get("data") or []
    if not data:
        return "no data"
    d = data[0]
    return f"FnG {d.get('value', '?')} ({d.get('value_classification', '?')})"


async def _ping_ccxt_binance_inner() -> Any:
    """Pre-warm binance, then call fetch_status (or fetch_ticker fallback)."""
    def _do() -> Any:
        ex = _get_ccxt_exchange("binance")
        if ex.has.get("fetchStatus"):
            return {"kind": "status", "value": ex.fetch_status()}
        return {"kind": "ticker", "value": ex.fetch_ticker("BTC/USDT")}

    return await _safe_ccxt("binance", _do, warm_timeout=15.0, call_timeout=15.0)


def _binance_detail(resp: Any) -> str:
    if not isinstance(resp, dict):
        return "ok"
    if resp.get("kind") == "status":
        return f"status={(resp.get('value') or {}).get('status', '?')}"
    if resp.get("kind") == "ticker":
        return f"BTC/USDT last={(resp.get('value') or {}).get('last')}"
    return "ok"


@mcp.tool()
async def health_check() -> dict[str, Any]:
    """Parallel-ping every upstream data source and report status + latency.

    DEBUGGING tool. Call only when the user reports something is broken
    ("X is down", "data looks stale") or when you suspect an upstream is
    degraded. Do NOT call on every request — issues fresh network calls.

    Pings in parallel:
      - CoinGecko: /ping
      - DefiLlama: /protocols?limit=1
      - DexScreener: /latest/dex/search?q=BTC
      - Alternative.me: /fng/?limit=1
      - CCXT/Binance: fetch_status() (fetch_ticker fallback)

    Returns:
        Object with `checked_at` (ISO8601 UTC), `all_ok` (bool — true iff every
        source succeeded), and `sources` — array sorted by latency asc. Each
        entry has `source`, `ok`, `latency_ms`, and `detail` (short success
        identifier or `<ExceptionType>: <first 100 chars>` on failure).
    """
    pings: list[Awaitable[dict[str, Any]]] = [
        _timed(
            "CoinGecko",
            lambda: _http_get(f"{COINGECKO_PUBLIC_BASE}/ping"),
            _cg_detail,
        ),
        _timed(
            "DefiLlama",
            lambda: _http_get(f"{DEFILLAMA_BASE}/protocols", params={"limit": 1}),
            _llama_detail,
        ),
        _timed(
            "DexScreener",
            lambda: _http_get(
                f"{DEXSCREENER_BASE}/latest/dex/search", params={"q": "BTC"}
            ),
            _dex_detail,
        ),
        _timed(
            "Alternative.me",
            lambda: _http_get(f"{ALTERNATIVE_BASE}/fng/", params={"limit": 1}),
            _fng_detail,
        ),
        _timed("CCXT (binance)", _ping_ccxt_binance_inner, _binance_detail),
    ]
    results = await asyncio.gather(*pings, return_exceptions=True)

    sources: list[dict[str, Any]] = []
    for r in results:
        if isinstance(r, Exception):
            sources.append({
                "source": "unknown",
                "ok": False,
                "latency_ms": 0,
                "detail": f"{type(r).__name__}: {_short(r)}",
            })
        else:
            sources.append(r)
    sources.sort(key=lambda s: s.get("latency_ms", 0))
    return {
        "checked_at": _now_iso_utc(),
        "all_ok": all(s.get("ok") for s in sources),
        "sources": sources,
    }


# ---------- 2. compare_prices ----------


async def _cg_aggregated_price(coin_id: str, vs_currency: str) -> dict[str, Any]:
    try:
        resp = await _cg_get(
            "/simple/price", {"ids": coin_id, "vs_currencies": vs_currency}
        )
    except Exception as e:
        return {
            "source": "CoinGecko (aggregated)",
            "ok": False,
            "error": f"{type(e).__name__}: {_short(e)}",
        }
    if is_error(resp):
        return {
            "source": "CoinGecko (aggregated)",
            "ok": False,
            "error": _short(resp.get("error", "")),
        }
    price = ((resp or {}).get(coin_id) or {}).get(vs_currency.lower())
    if price is None:
        return {
            "source": "CoinGecko (aggregated)",
            "ok": False,
            "error": f"no price for {coin_id}/{vs_currency}",
        }
    return {"source": "CoinGecko (aggregated)", "ok": True, "price": float(price)}


async def _ccxt_exchange_price(
    exchange_id: str, base: str, quote: str
) -> dict[str, Any]:
    symbol = f"{base}/{quote}"

    def _do() -> Any:
        ex = _get_ccxt_exchange(exchange_id)
        return ex.fetch_ticker(symbol)

    resp = await _safe_ccxt(exchange_id, _do)
    if is_error(resp):
        return {"source": exchange_id, "ok": False, "error": _short(resp.get("error", ""))}
    last = resp.get("last") if isinstance(resp, dict) else None
    if last is None and isinstance(resp, dict):
        bid, ask = resp.get("bid"), resp.get("ask")
        if bid is not None and ask is not None:
            last = (float(bid) + float(ask)) / 2.0
    if last is None:
        return {
            "source": exchange_id, "ok": False,
            "error": f"no last price in ticker for {symbol}",
        }
    return {"source": exchange_id, "ok": True, "price": float(last), "symbol": symbol}


async def _dexscreener_price(symbol_upper: str) -> dict[str, Any]:
    try:
        resp = await _http_get(
            f"{DEXSCREENER_BASE}/latest/dex/search", params={"q": symbol_upper}
        )
    except Exception as e:
        return {
            "source": "DexScreener",
            "ok": False,
            "error": f"{type(e).__name__}: {_short(e)}",
        }
    if is_error(resp):
        return {
            "source": "DexScreener",
            "ok": False,
            "error": _short(resp.get("error", "")),
        }
    pairs = (resp or {}).get("pairs") or []
    if not pairs:
        return {
            "source": "DexScreener",
            "ok": False,
            "error": f"no pairs for {symbol_upper}",
        }

    def _liq(p: dict) -> float:
        try:
            return float(((p.get("liquidity") or {}).get("usd")) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    top = max(pairs, key=_liq)
    price_usd = top.get("priceUsd")
    if price_usd is None:
        return {"source": "DexScreener", "ok": False, "error": "top pair missing priceUsd"}
    return {
        "source": "DexScreener",
        "ok": True,
        "price": float(price_usd),
        "detail": f"{top.get('dexId', '?')}/{top.get('chainId', '?')}",
    }


@mcp.tool()
async def compare_prices(
    coin_id: str = "bitcoin",
    vs_currency: str = "usd",
    exchange_ids: str = "binance,okx,coinbase,kraken",
) -> dict[str, Any]:
    """Concurrently fetch the price of one coin from many sources and compare.

    Use for cross-source sanity checks, finding venue-vs-aggregator
    divergences, or simple arbitrage spotting. Contrast with:
      - `get_price`: single source (CoinGecko aggregated).
      - `compare_funding_rates`: perp funding across venues.

    Sources fanned out in parallel:
      - CoinGecko aggregated `/simple/price`
      - For each `exchange_id`: CCXT `fetch_ticker(base/quote)` where base is
        derived from `coin_id` via a small mapping (bitcoin->BTC, ethereum->ETH,
        solana->SOL, ripple->XRP, cardano->ADA, dogecoin->DOGE, tron->TRX,
        polkadot->DOT, chainlink->LINK, avalanche-2->AVAX, matic-network->MATIC)
        and quote is `USDT` for `vs_currency=usd`, else `vs_currency.upper()`.
      - DexScreener (only when `vs_currency` is "usd"): top-liquidity pair.

    Args:
        coin_id: CoinGecko coin ID (e.g. "bitcoin").
        vs_currency: Quote currency. "usd" works on all sources; others skip
            DexScreener.
        exchange_ids: Comma-separated CCXT exchange IDs.

    Returns:
        Object with `coin_id`, `vs_currency`, `prices` (per-source array with
        `source`, `ok`, and either `price` or `error`), `max`, `min`,
        `spread_bps`, `n_ok`, `n_error`. Unknown coin_ids still return
        per-source error envelopes pointing at `get_exchange_ticker`.
    """
    vs_lower = (vs_currency or "usd").strip().lower()
    base = _CG_TO_CCXT_BASE.get(coin_id.strip().lower())
    quote = _ccxt_quote_for(vs_lower)
    ex_list = [e.strip() for e in (exchange_ids or "").split(",") if e.strip()]

    tasks: list[Awaitable[dict[str, Any]]] = [_cg_aggregated_price(coin_id, vs_lower)]

    if base is None:
        async def _unknown(exid: str) -> dict[str, Any]:
            return {
                "source": exid,
                "ok": False,
                "error": (
                    f"unknown coin_id->CCXT mapping for {coin_id!r}; pass the "
                    f"CCXT symbol directly via get_exchange_ticker"
                ),
            }
        tasks.extend(_unknown(exid) for exid in ex_list)
    else:
        tasks.extend(_ccxt_exchange_price(exid, base, quote) for exid in ex_list)

    if vs_lower == "usd" and base is not None:
        tasks.append(_dexscreener_price(base))

    raw = await asyncio.gather(*tasks, return_exceptions=True)
    prices: list[dict[str, Any]] = []
    for r in raw:
        if isinstance(r, Exception):
            prices.append({
                "source": "unknown",
                "ok": False,
                "error": f"{type(r).__name__}: {_short(r)}",
            })
        else:
            prices.append(r)

    ok_prices = [p for p in prices if p.get("ok") and isinstance(p.get("price"), (int, float))]
    n_ok = len(ok_prices)

    out: dict[str, Any] = {
        "coin_id": coin_id,
        "vs_currency": vs_lower,
        "prices": prices,
        "n_ok": n_ok,
        "n_error": len(prices) - n_ok,
    }
    if ok_prices:
        max_e = max(ok_prices, key=lambda p: p["price"])
        min_e = min(ok_prices, key=lambda p: p["price"])
        out["max"] = {"source": max_e["source"], "price": max_e["price"]}
        out["min"] = {"source": min_e["source"], "price": min_e["price"]}
        mid = (max_e["price"] + min_e["price"]) / 2.0
        if mid > 0 and len(ok_prices) >= 2:
            out["spread_bps"] = round((max_e["price"] - min_e["price"]) / mid * 10000.0, 2)
        else:
            out["spread_bps"] = 0.0
    return out


# ---------- 3. get_consolidated_orderbook ----------


async def _fetch_one_book(
    exchange_id: str, symbol: str, depth: int
) -> dict[str, Any]:
    def _do() -> Any:
        ex = _get_ccxt_exchange(exchange_id)
        return ex.fetch_order_book(symbol, depth)

    resp = await _safe_ccxt(exchange_id, _do)
    if is_error(resp):
        return {"exchange": exchange_id, "ok": False, "error": _short(resp.get("error", ""))}
    bids = resp.get("bids") or [] if isinstance(resp, dict) else []
    asks = resp.get("asks") or [] if isinstance(resp, dict) else []
    return {"exchange": exchange_id, "ok": True, "bids": bids[:depth], "asks": asks[:depth]}


@mcp.tool()
async def get_consolidated_orderbook(
    symbol: str = "BTC/USDT",
    exchange_ids: str = "binance,okx,bybit,kraken,bitstamp",
    depth: int = 10,
) -> dict[str, Any]:
    """Fetch L2 order books from many exchanges in parallel and merge into one virtual book.

    Answers "where is the best bid/ask across the whole market?" — more useful
    than per-venue `get_orderbook` for execution analysis. The merged book does
    NOT aggregate by price level; each level retains its source exchange so the
    LLM can attribute liquidity per venue.

    Args:
        symbol: CCXT unified symbol (e.g. "BTC/USDT"). Some exchanges may
            reject the symbol (BadSymbol) — those go into `exchanges_error`.
        exchange_ids: Comma-separated CCXT exchange IDs (capped at 10).
        depth: Levels per side per exchange, clamped to [1, 50].

    Returns:
        Object with:
          - symbol, depth_per_exchange
          - exchanges_ok: list of exchange ids that responded
          - exchanges_error: list of {exchange, error} for failures
          - bids: merged bids sorted by price desc, capped at depth*len(ok),
            each entry {price, amount, exchange}
          - asks: merged asks sorted by price asc, capped likewise
          - best_bid, best_ask: top of merged book with attribution
          - spread_bps: (best_ask - best_bid) / mid * 10000
    """
    depth = max(1, min(int(depth or 1), 50))
    ex_list = [e.strip() for e in (exchange_ids or "").split(",") if e.strip()][:10]

    if not ex_list:
        return {
            "symbol": symbol,
            "depth_per_exchange": depth,
            "exchanges_ok": [],
            "exchanges_error": [],
            "bids": [],
            "asks": [],
        }

    raw = await asyncio.gather(
        *[_fetch_one_book(exid, symbol, depth) for exid in ex_list],
        return_exceptions=True,
    )

    exchanges_ok: list[str] = []
    exchanges_error: list[dict[str, Any]] = []
    merged_bids: list[dict[str, Any]] = []
    merged_asks: list[dict[str, Any]] = []

    for exid, r in zip(ex_list, raw):
        if isinstance(r, Exception):
            exchanges_error.append({
                "exchange": exid,
                "error": f"{type(r).__name__}: {_short(r)}",
            })
            continue
        if not r.get("ok"):
            exchanges_error.append({
                "exchange": r.get("exchange", exid),
                "error": r.get("error", "unknown"),
            })
            continue
        exchanges_ok.append(exid)
        for lvl in r.get("bids", []):
            try:
                merged_bids.append({
                    "price": float(lvl[0]),
                    "amount": float(lvl[1]),
                    "exchange": exid,
                })
            except (TypeError, ValueError, IndexError):
                continue
        for lvl in r.get("asks", []):
            try:
                merged_asks.append({
                    "price": float(lvl[0]),
                    "amount": float(lvl[1]),
                    "exchange": exid,
                })
            except (TypeError, ValueError, IndexError):
                continue

    merged_bids.sort(key=lambda x: x["price"], reverse=True)
    merged_asks.sort(key=lambda x: x["price"])

    cap = depth * max(1, len(exchanges_ok))
    merged_bids = merged_bids[:cap]
    merged_asks = merged_asks[:cap]

    out: dict[str, Any] = {
        "symbol": symbol,
        "depth_per_exchange": depth,
        "exchanges_ok": exchanges_ok,
        "exchanges_error": exchanges_error,
        "bids": merged_bids,
        "asks": merged_asks,
    }
    if merged_bids:
        out["best_bid"] = {
            "price": merged_bids[0]["price"],
            "exchange": merged_bids[0]["exchange"],
        }
    if merged_asks:
        out["best_ask"] = {
            "price": merged_asks[0]["price"],
            "exchange": merged_asks[0]["exchange"],
        }
    if merged_bids and merged_asks:
        bb, ba = merged_bids[0]["price"], merged_asks[0]["price"]
        mid = (bb + ba) / 2.0
        out["spread_bps"] = round((ba - bb) / mid * 10000.0, 2) if mid > 0 else 0.0
    return out
