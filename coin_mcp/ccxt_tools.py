"""CCXT tools — real-time per-exchange data: tickers, order books, trades, OHLCV, funding rates."""
from __future__ import annotations

from typing import Any, Literal

import ccxt

from .core import _ccxt_call, _get_ccxt_exchange, mcp


@mcp.tool()
async def list_supported_exchanges() -> dict:
    """List all exchange IDs that this server can query in real time via CCXT.

    Use this when you need to know which `exchange_id` values are valid for the
    other CCXT-backed tools (`get_exchange_markets`, `get_exchange_ticker`,
    `get_orderbook`, `get_recent_trades`, `get_exchange_ohlcv`, `get_funding_rate`).

    Returns:
        Object with `count` and `exchanges` (array of lowercase exchange IDs
        such as "binance", "okx", "coinbase", "kraken", "bybit", "kucoin",
        "huobi", "bitfinex", "gateio", "mexc").
    """
    ids = sorted(ccxt.exchanges)
    return {"count": len(ids), "exchanges": ids}


@mcp.tool()
async def get_exchange_markets(exchange_id: str, active_only: bool = True) -> Any:
    """List all trading pairs (markets) supported by a specific exchange.

    Use to discover what symbols an exchange trades (e.g. "does Kraken list
    SOL/USDC?"), or to enumerate available perpetual contracts.

    Args:
        exchange_id: CCXT exchange ID (lowercase). See `list_supported_exchanges`.
        active_only: If true, exclude delisted/inactive markets.

    Returns:
        Object with `exchange`, `count`, and `markets` — each market has
        `symbol`, `base`, `quote`, `settle`, `type` (spot/swap/future/option),
        `linear`, `inverse`, `contract`, `active`.
    """
    def _do() -> Any:
        ex = _get_ccxt_exchange(exchange_id)
        markets = ex.load_markets(reload=False)
        out = []
        for sym, m in markets.items():
            if active_only and m.get("active") is False:
                continue
            out.append({
                "symbol": sym,
                "base": m.get("base"),
                "quote": m.get("quote"),
                "settle": m.get("settle"),
                "type": m.get("type"),
                "linear": m.get("linear"),
                "inverse": m.get("inverse"),
                "contract": m.get("contract"),
                "active": m.get("active"),
            })
        return {"exchange": exchange_id.lower(), "count": len(out), "markets": out}

    return await _ccxt_call(_do)


@mcp.tool()
async def get_exchange_ticker(exchange_id: str, symbol: str) -> Any:
    """Get a real-time ticker (bid/ask/last/24h stats) for one symbol on one exchange.

    Use when the user asks about price on a specific venue ("BTC on Coinbase",
    "ETH on Binance") or wants tight bid-ask spread info.

    Args:
        exchange_id: CCXT exchange ID, e.g. "binance".
        symbol: CCXT unified symbol, e.g. "BTC/USDT", "ETH/USD",
            "BTC/USDT:USDT" for a linear perp on Binance.

    Returns:
        Ticker object with `symbol`, `timestamp`, `datetime`, `bid`, `ask`,
        `last`, `high`, `low`, `open`, `close`, `vwap`, `baseVolume`,
        `quoteVolume`, `percentage`, `change`.
    """
    def _do() -> Any:
        ex = _get_ccxt_exchange(exchange_id)
        return ex.fetch_ticker(symbol)

    return await _ccxt_call(_do)


@mcp.tool()
async def get_orderbook(
    exchange_id: str,
    symbol: str,
    limit: int = 20,
) -> Any:
    """Get a Level-2 order-book snapshot (top bids and asks) from a specific exchange.

    Use to assess liquidity, spread, and short-term supply/demand on a venue.
    Note this is a snapshot, not a stream.

    Args:
        exchange_id: CCXT exchange ID.
        symbol: CCXT unified symbol, e.g. "BTC/USDT".
        limit: How many price levels per side (default 20). Some exchanges cap
            this; CCXT will return as many as the venue allows.

    Returns:
        Object with `symbol`, `timestamp`, `datetime`, `bids` (array of
        [price, amount] pairs sorted high to low), `asks` (low to high), `nonce`.
    """
    def _do() -> Any:
        ex = _get_ccxt_exchange(exchange_id)
        return ex.fetch_order_book(symbol, max(1, min(limit, 1000)))

    return await _ccxt_call(_do)


@mcp.tool()
async def get_recent_trades(
    exchange_id: str,
    symbol: str,
    limit: int = 50,
) -> Any:
    """Get recent public trades (the tape) for a symbol on a specific exchange.

    Use to inspect order flow, identify large prints, or compute very
    short-term trade-driven metrics.

    Args:
        exchange_id: CCXT exchange ID.
        symbol: Unified symbol, e.g. "BTC/USDT".
        limit: Number of recent trades to return (max ~1000 depending on venue).

    Returns:
        Array of trades with `id`, `timestamp`, `datetime`, `symbol`, `side`,
        `price`, `amount`, `cost`, `takerOrMaker`.
    """
    def _do() -> Any:
        ex = _get_ccxt_exchange(exchange_id)
        return ex.fetch_trades(symbol, limit=max(1, min(limit, 1000)))

    return await _ccxt_call(_do)


@mcp.tool()
async def get_exchange_ohlcv(
    exchange_id: str,
    symbol: str,
    timeframe: Literal[
        "1m", "3m", "5m", "15m", "30m",
        "1h", "2h", "4h", "6h", "8h", "12h",
        "1d", "3d", "1w", "1M",
    ] = "1h",
    limit: int = 200,
    since_ms: int | None = None,
) -> Any:
    """Get OHLCV candlestick data from a specific exchange (high-granularity, including 1-minute candles).

    Prefer this over `get_aggregated_ohlc` when:
      - the user asks about a specific venue, OR
      - they need sub-hour candles (1m/5m/15m), OR
      - they need exact volume on one exchange.

    Args:
        exchange_id: CCXT exchange ID, e.g. "binance".
        symbol: Unified symbol, e.g. "BTC/USDT".
        timeframe: Candle width. Not every exchange supports every timeframe;
            common safe choices: "1m","5m","15m","1h","4h","1d".
        limit: Number of candles. Most exchanges cap at ~500-1500 per call.
        since_ms: Optional unix-millis lower bound. Most recent candles when null.

    Returns:
        Array of [timestamp_ms, open, high, low, close, volume] tuples,
        oldest first.
    """
    def _do() -> Any:
        ex = _get_ccxt_exchange(exchange_id)
        return ex.fetch_ohlcv(
            symbol,
            timeframe=timeframe,
            since=since_ms,
            limit=max(1, min(limit, 1500)),
        )

    return await _ccxt_call(_do)


@mcp.tool()
async def get_funding_rate(exchange_id: str, symbol: str) -> Any:
    """Get the current funding rate for a perpetual-futures contract on a specific exchange.

    Funding rate is a periodic payment between longs and shorts that anchors
    perp prices to spot. Positive funding means longs pay shorts (crowd is
    long); negative funding means shorts pay longs.

    Args:
        exchange_id: CCXT exchange ID that supports perps, e.g. "binance",
            "okx", "bybit", "bitmex".
        symbol: Perp symbol with settle suffix, e.g. "BTC/USDT:USDT" for the
            Binance USDT-margined linear perp, "BTC/USD:BTC" for an inverse perp.

    Returns:
        Funding info with `symbol`, `markPrice`, `indexPrice`, `fundingRate`,
        `fundingTimestamp`, `nextFundingRate`, `nextFundingTimestamp`,
        `interestRate`. Exact fields vary slightly by exchange.
    """
    def _do() -> Any:
        ex = _get_ccxt_exchange(exchange_id)
        if not ex.has.get("fetchFundingRate"):
            return {"error": f"{exchange_id} does not support fetchFundingRate via CCXT"}
        return ex.fetch_funding_rate(symbol)

    return await _ccxt_call(_do)
