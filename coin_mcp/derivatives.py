"""Derivatives tools — funding-rate history, open interest, cross-exchange funding comparison.

These wrap CCXT's perp-specific endpoints. For the *current* funding-rate
snapshot on a single venue, use `get_funding_rate` from `ccxt_tools` instead.
"""
from __future__ import annotations

import asyncio
from typing import Any

from .core import _ccxt_call, _get_ccxt_exchange, is_error, mcp


async def _prewarm_exchange(exchange_id: str) -> dict | None:
    """Build/cache the CCXT exchange instance OUTSIDE of `_ccxt_call`'s per-id
    lock. The per-id lock used by `_ccxt_call` is a non-reentrant
    `threading.Lock`; if a fresh exchange's `_get_ccxt_exchange` (which itself
    takes that same lock during construction) ran inside `_ccxt_call`, we'd
    deadlock. After this warm-up the LRU cache hit path skips the lock
    entirely.

    Returns `None` on success, or an `{"error": ...}` envelope to surface to
    the caller.
    """
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, _get_ccxt_exchange, exchange_id)
        return None
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


@mcp.tool()
async def get_funding_rate_history(
    exchange_id: str,
    symbol: str,
    since_ms: int | None = None,
    limit: int = 100,
) -> Any:
    """Get historical funding-rate time-series for a perpetual contract on one exchange.

    Use this for trend analysis on funding — "is funding turning positive?",
    "how long has BTC funding been negative?", spotting funding cycles, or
    feeding a series into a quant signal. For the single most-recent snapshot
    use `get_funding_rate`. For a cross-exchange comparison of the *current*
    rate use `compare_funding_rates`.

    The funding interval varies by venue: Binance and OKX charge funding every
    8 hours, Bybit every 1 hour for some perps, BitMEX every 8 hours, etc.
    Returned timestamps reflect each charge. Don't assume a uniform cadence
    when comparing series across exchanges.

    Args:
        exchange_id: CCXT exchange ID supporting perps, e.g. "binance", "okx",
            "bybit", "bitmex", "bitget", "bingx", "gate", "mexc",
            "kucoinfutures", "hyperliquid".
        symbol: Perp symbol with settle suffix. Linear (USDT-margined):
            "BTC/USDT:USDT". Inverse (coin-margined): "BTC/USD:BTC".
        since_ms: Optional unix-millis lower bound. If null, the exchange
            returns its default window (typically the most-recent N rows).
        limit: Max number of rows to return; clamped to [1, 1000].

    Returns:
        Array of rows oldest-first, each with `timestamp` (unix millis),
        `datetime` (ISO 8601), `symbol`, `fundingRate` (e.g. 0.0001 = 1 bp),
        and exchange-specific extras under `info`. On unsupported exchanges
        returns `{"error": "..."}`.
    """
    capped_limit = max(1, min(int(limit), 1000))
    err = await _prewarm_exchange(exchange_id)
    if err is not None:
        return err

    def _do() -> Any:
        ex = _get_ccxt_exchange(exchange_id)
        if not ex.has.get("fetchFundingRateHistory"):
            return {
                "error": f"{exchange_id} does not support fetchFundingRateHistory via CCXT"
            }
        return ex.fetch_funding_rate_history(
            symbol, since=since_ms, limit=capped_limit
        )

    return await _ccxt_call(_do, exchange_id=exchange_id)


@mcp.tool()
async def get_open_interest(exchange_id: str, symbol: str) -> Any:
    """Get the current open interest (OI) for a perpetual contract on one exchange.

    Open interest is the total notional/contract count of outstanding positions
    on a venue. Reading OI alongside price:
      - OI rising with price rising  -> new longs entering, trend has fuel
      - OI rising with price falling -> new shorts loading up
      - OI falling with price rising -> short squeeze / covering rally
      - OI falling with price falling -> longs capitulating
    This is descriptive, not advice. For funding-rate context use
    `get_funding_rate` (snapshot) or `get_funding_rate_history` (series).

    Falls back to `fetch_open_interest_history(timeframe="1h", limit=1)` when
    the exchange exposes only the historical endpoint.

    Args:
        exchange_id: CCXT exchange ID supporting perps.
        symbol: Perp symbol with settle suffix, e.g. "BTC/USDT:USDT" or
            "BTC/USD:BTC" for inverse.

    Returns:
        Object with `symbol`, `openInterestAmount` (in base units / contracts),
        `openInterestValue` (notional in quote), `timestamp`, `datetime`, and
        exchange-specific `info`. On unsupported exchanges returns
        `{"error": "..."}`.
    """
    err = await _prewarm_exchange(exchange_id)
    if err is not None:
        return err

    def _do() -> Any:
        ex = _get_ccxt_exchange(exchange_id)
        if ex.has.get("fetchOpenInterest"):
            return ex.fetch_open_interest(symbol)
        if ex.has.get("fetchOpenInterestHistory"):
            rows = ex.fetch_open_interest_history(symbol, timeframe="1h", limit=1)
            if isinstance(rows, list) and rows:
                return rows[-1]
            return {
                "error": f"{exchange_id} fetchOpenInterestHistory returned no rows for {symbol}"
            }
        return {
            "error": (
                f"{exchange_id} supports neither fetchOpenInterest nor "
                f"fetchOpenInterestHistory via CCXT"
            )
        }

    return await _ccxt_call(_do, exchange_id=exchange_id)


@mcp.tool()
async def compare_funding_rates(
    symbol: str = "BTC/USDT:USDT",
    exchange_ids: str = "binance,okx,bybit,bitmex",
) -> dict:
    """Compare the *current* funding rate for one perp across multiple exchanges, in parallel.

    Use this to find funding-rate arbitrage opportunities (long the venue
    paying you, short the venue charging you) or to gauge how lopsided
    positioning is across the market. The `spread_bps` field is
    `(max - min) * 10000` and tells you how big the dispersion is in basis
    points. For a single exchange snapshot use `get_funding_rate`; for
    historical trend on one venue use `get_funding_rate_history`.

    Symbol convention: linear USDT-margined perps use "BTC/USDT:USDT" on
    most venues. BitMEX's flagship is the inverse contract "BTC/USD:BTC", so
    if you pass a USDT linear symbol the BitMEX branch will return an error
    inline — that's expected. Branches that fail (unsupported symbol, geo-
    block, rate limit) return `{"exchange": ..., "error": ...}` rather than
    sinking the whole call.

    Args:
        symbol: CCXT unified perp symbol. Default "BTC/USDT:USDT".
        exchange_ids: Comma-separated CCXT exchange IDs. Default
            "binance,okx,bybit,bitmex".

    Returns:
        `{"symbol", "rates": [...], "max", "min", "spread_bps", "n_ok",
        "n_error"}`. Each rate row is either
        `{"exchange", "fundingRate", "nextFundingTimestamp", "markPrice"}`
        on success or `{"exchange", "error"}` on failure. `max`/`min`/
        `spread_bps` are populated only when at least two branches returned
        numeric `fundingRate` values.
    """
    ex_ids = [s.strip().lower() for s in exchange_ids.split(",") if s.strip()]

    async def _fetch_one(ex_id: str) -> dict:
        err = await _prewarm_exchange(ex_id)
        if err is not None:
            return {"exchange": ex_id, "error": err["error"]}

        def _do() -> Any:
            ex = _get_ccxt_exchange(ex_id)
            if not ex.has.get("fetchFundingRate"):
                return {
                    "error": f"{ex_id} does not support fetchFundingRate via CCXT"
                }
            return ex.fetch_funding_rate(symbol)

        try:
            res = await _ccxt_call(_do, exchange_id=ex_id)
        except Exception as e:  # safety net; _ccxt_call already catches CCXT errors
            return {"exchange": ex_id, "error": f"{type(e).__name__}: {e}"}

        if is_error(res):
            return {"exchange": ex_id, "error": res.get("error")}
        if not isinstance(res, dict):
            return {"exchange": ex_id, "error": f"unexpected response type: {type(res).__name__}"}
        return {
            "exchange": ex_id,
            "fundingRate": res.get("fundingRate"),
            "nextFundingTimestamp": res.get("nextFundingTimestamp"),
            "markPrice": res.get("markPrice"),
        }

    coros = [_fetch_one(ex_id) for ex_id in ex_ids]
    gathered = await asyncio.gather(*coros, return_exceptions=True)

    rates: list[dict] = []
    for ex_id, r in zip(ex_ids, gathered):
        if isinstance(r, BaseException):
            rates.append({"exchange": ex_id, "error": f"{type(r).__name__}: {r}"})
        else:
            rates.append(r)

    numeric = [r for r in rates if isinstance(r.get("fundingRate"), (int, float))]
    n_ok = len(numeric)
    n_error = len(rates) - n_ok

    out: dict[str, Any] = {
        "symbol": symbol,
        "rates": rates,
        "n_ok": n_ok,
        "n_error": n_error,
    }

    if numeric:
        max_row = max(numeric, key=lambda r: r["fundingRate"])
        min_row = min(numeric, key=lambda r: r["fundingRate"])
        out["max"] = {"exchange": max_row["exchange"], "fundingRate": max_row["fundingRate"]}
        out["min"] = {"exchange": min_row["exchange"], "fundingRate": min_row["fundingRate"]}
        if n_ok >= 2:
            out["spread_bps"] = (max_row["fundingRate"] - min_row["fundingRate"]) * 10000

    return out
