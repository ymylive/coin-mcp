"""coin-mcp core — FastMCP instance, configuration, and shared helpers.

Every tool module imports `mcp` from here and decorates its functions with
`@mcp.tool()`. HTTP and CCXT helpers are also defined here so individual tool
modules don't reinvent transport / error handling.

Configuration (env vars):
    COINGECKO_API_KEY   Optional. CoinGecko Demo or Pro API key. With a key,
                        the server uses the Pro endpoint and authenticates
                        requests (higher rate limits + extra endpoints such
                        as top gainers/losers). Without a key, the public
                        endpoint is used (~30 req/min).
"""
from __future__ import annotations

import asyncio
import os
import threading
from collections import OrderedDict
from typing import Any

import ccxt
import httpx
from mcp.server.fastmcp import FastMCP

# ---------- Configuration ----------

COINGECKO_PUBLIC_BASE = "https://api.coingecko.com/api/v3"
COINGECKO_PRO_BASE = "https://pro-api.coingecko.com/api/v3"
ALTERNATIVE_BASE = "https://api.alternative.me"
DEFILLAMA_BASE = "https://api.llama.fi"
DEFILLAMA_COINS_BASE = "https://coins.llama.fi"
DEFILLAMA_STABLECOINS_BASE = "https://stablecoins.llama.fi"
DEFILLAMA_YIELDS_BASE = "https://yields.llama.fi"
DEXSCREENER_BASE = "https://api.dexscreener.com"

DEFAULT_TIMEOUT = 30.0
USER_AGENT = "coin-mcp/0.1"

COINGECKO_API_KEY = os.environ.get("COINGECKO_API_KEY", "").strip()


# ---------- Shared error envelope helper ----------


def is_error(obj: Any) -> bool:
    """True iff obj is the structured-error envelope returned by HTTP helpers.

    The envelope is `{"error": "<message>", ...}`. We check truthiness of the
    `"error"` value so legitimate upstream responses that contain an `"error":
    null` field are NOT treated as errors.
    """
    return isinstance(obj, dict) and bool(obj.get("error"))


# ---------- MCP server ----------

mcp = FastMCP(
    name="coin-mcp",
    instructions="""\
This MCP server provides comprehensive cryptocurrency market data from
multiple complementary sources:

- **CoinGecko** — aggregated market data (volume-weighted prices, market cap,
  OHLC, history, exchange directory, NFTs, categories, derivatives directory,
  public-company crypto treasuries, trending searches, search index).
- **CCXT** — real-time per-exchange data via a unified API for 100+ centralized
  exchanges (order books, recent trades, tickers, OHLCV candles, market lists,
  perpetual-futures funding rates).
- **DefiLlama** — protocol-level TVL, chain TVL, stablecoin caps, yield pools,
  DEX volumes, fees & revenue, oracle token prices.
- **DexScreener** — DEX-side prices and liquidity for tokens too small or new
  for CoinGecko aggregation, across all major chains.
- **Alternative.me** — Crypto Fear & Greed Index sentiment indicator.
- **Local technical indicators** — RSI, MACD, Bollinger, EMA/SMA, ATR
  computed in-process from any OHLCV input.

================================================================
HOW TO PICK THE RIGHT TOOL  (43 tools total)
================================================================

| Question | Tool |
|----------|------|
| What's BTC's price right now? | get_price |
| Tell me about Solana | get_coin_details |
| 30-day price/volume/market-cap chart | get_market_chart |
| Daily candlesticks for ETH (aggregated) | get_aggregated_ohlc |
| Which exchanges support a coin? | get_coin_tickers |
| Coin name -> CoinGecko ID (also exchanges/categories/NFTs) | search |
| Top 100 coins by market cap | list_top_coins |
| What's hot/trending today? | get_trending |
| Biggest 24h gainers and losers | get_top_gainers_losers |
| Total market cap, BTC dominance | get_global_market |
| DeFi TVL totals (high level, CoinGecko view) | get_global_defi |
| Categories (Layer 1, DeFi, Meme...) | list_categories |
| Browse all exchanges (CoinGecko directory) | list_exchanges_directory |
| Single-exchange metadata (CoinGecko directory) | get_exchange_info |
| Derivatives platforms | list_derivatives_exchanges |
| NFT collections list | list_nfts |
| Single NFT collection detail | get_nft_collection |
| Public companies holding BTC/ETH | get_companies_holdings |
| What exchanges can I query in real time? | list_supported_exchanges |
| All trading pairs on Binance/etc. | get_exchange_markets |
| Best bid/ask/last on a specific exchange | get_exchange_ticker |
| Real-time order book on a specific exchange | get_orderbook |
| Recent public trades on a specific exchange | get_recent_trades |
| 1-minute candles on Binance for BTC/USDT | get_exchange_ohlcv |
| Funding rate for a perp | get_funding_rate |
| Sentiment: fearful or greedy? | get_fear_greed_index |
| Compute RSI/MACD/Bollinger/ATR/etc on OHLCV | compute_indicators |
| Per-protocol TVL / TVL history | get_protocol_tvl |
| Browse DefiLlama protocols by TVL | list_protocols |
| Chain-level TVL ranking (Ethereum, Solana, ...) | list_chains_tvl |
| Historical TVL for one chain or all of DeFi | get_chain_tvl_history |
| Stablecoin caps and chain breakdown | list_stablecoins |
| Yield-pool APYs | list_yield_pools |
| DEX 24h volume rankings | list_dex_volumes |
| Per-protocol fees and revenue | list_fees_revenue |
| DefiLlama oracle price for `chain:address` tokens | get_token_dex_price |
| DEX price for a small/new token (any chain) | dex_search |
| All DEX pairs for a given token address | get_dex_token_pairs |
| Single DEX pair detail | get_dex_pair |
| Newly profiled DEX tokens | list_latest_dex_tokens |
| Currently boosted (paid) DEX tokens | list_top_boosted_tokens |
| What's currently cached (rate-limit relief) | cache_stats |
| Drop the HTTP cache | clear_cache |
| Funding-rate time series for a perp | get_funding_rate_history |
| Current open interest for a perp | get_open_interest |
| Compare funding rates across exchanges | compare_funding_rates |
| Are all data sources healthy / fast? | health_check |
| Same coin's price across CG + multiple CEX + DEX | compare_prices |
| Best bid/ask merged across many exchanges | get_consolidated_orderbook |

================================================================
KEY THINGS TO REMEMBER
================================================================

1. **CoinGecko uses coin IDs**, not ticker symbols. IDs look like "bitcoin",
   "ethereum", "solana". Resolve unknown names via `search` first.

2. **CCXT uses unified symbols + exchange IDs.** Symbols: "BTC/USDT", "ETH/USD".
   Linear perps use settle suffix: "BTC/USDT:USDT". Exchange IDs are lowercase
   ("binance", "okx", "coinbase", "kraken", "bybit", "kucoin").

3. **Aggregated vs per-exchange.** CoinGecko = volume-weighted across all
   venues. CCXT = one specific exchange. Use CoinGecko for "the market";
   use CCXT for venue-specific or sub-hour granularity.

4. **DEX vs CEX prices.** For tokens listed on CoinGecko, prefer CoinGecko/
   CCXT. For new/long-tail tokens, use DexScreener (`dex_search`,
   `get_dex_token_pairs`, `get_dex_pair`) or DefiLlama (`get_token_dex_price`).

5. **Default vs_currency is "usd"**. CCXT symbols already encode quote currency.

6. **Rate limits.** CoinGecko public allows ~30 req/min — but this server
   caches responses with TTLs tuned per endpoint. Repeated identical calls
   come from cache; check `cache_stats` if surprised by stale data, and
   `clear_cache` to force-refresh everything.

7. **Time ranges.** `get_market_chart` and `get_aggregated_ohlc` accept a
   `days` parameter. CoinGecko auto-selects granularity: minute when days<=1,
   hourly when days<=90, daily otherwise.

8. **Presentation.** Tools return raw numbers. Format prices/percentages/
   timestamps appropriately when answering the user.

================================================================
NAMING & VALIDATION CONVENTIONS
================================================================

- Coin / exchange / NFT IDs must match `^[a-z0-9._-]+$` (lowercase slugs).
- Token addresses are either EVM (`0x` followed by 40 hex chars) or Solana
  base58 (32-44 chars). Tools validate the shape before sending requests.
- Tools never run network requests until validation passes — invalid input
  returns a `{"error": ...}` envelope synchronously.
- Tools may return `{"error": ...}` envelopes on upstream failure. Callers
  should check the result with the structured-error helper before assuming
  success and indexing into other fields.
""",
)


# ---------- HTTP helpers ----------


def _coingecko_base() -> str:
    return COINGECKO_PRO_BASE if COINGECKO_API_KEY else COINGECKO_PUBLIC_BASE


def _coingecko_headers() -> dict[str, str]:
    """Build the CoinGecko auth header dict.

    CoinGecko Pro keys are issued with a `CG-` prefix; Demo (free) keys are
    not. Sending both headers simultaneously confuses the gateway, so pick
    exactly one based on that prefix.
    """
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if COINGECKO_API_KEY:
        if COINGECKO_API_KEY.startswith("CG-"):
            headers["x-cg-pro-api-key"] = COINGECKO_API_KEY
        else:
            headers["x-cg-demo-api-key"] = COINGECKO_API_KEY
    return headers


async def _http_get(
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    """GET a URL and return JSON, or a structured error dict on failure.

    Delegates to the in-process TTL cache so repeated identical requests
    don't re-hit the network. See `coin_mcp.cache` for routing rules.
    """
    from . import cache  # local import to avoid circular dependency at module load
    return await cache.cached_http_get(url, params=params, headers=headers)


async def _cg_get(path: str, params: dict[str, Any] | None = None) -> Any:
    return await _http_get(
        f"{_coingecko_base()}{path}",
        params=params,
        headers=_coingecko_headers(),
    )


def _bool_str(v: bool) -> str:
    return "true" if v else "false"


# ---------- CCXT helpers ----------

_CCXT_CACHE_MAX = 16
_exchange_cache: "OrderedDict[str, ccxt.Exchange]" = OrderedDict()
_exchange_cache_lock = threading.Lock()
_per_exchange_locks: dict[str, threading.RLock] = {}


def _per_lock(exchange_id: str) -> threading.RLock:
    """Return (creating if needed) the per-exchange RLock used to serialize
    construction and method calls on a single CCXT instance.

    Re-entrant: a coroutine that holds the lock for an exchange and calls
    `_get_ccxt_exchange` (which also takes the lock during construction) does
    not deadlock. This matters for `_ccxt_call(fn, exchange_id=ex)` whose `fn`
    body invokes `_get_ccxt_exchange(ex)`.
    """
    with _exchange_cache_lock:
        lock = _per_exchange_locks.get(exchange_id)
        if lock is None:
            lock = threading.RLock()
            _per_exchange_locks[exchange_id] = lock
        return lock


def _get_ccxt_exchange(exchange_id: str) -> ccxt.Exchange:
    """Get (or build & cache) a CCXT exchange instance by id.

    The cache is bounded (LRU, max `_CCXT_CACHE_MAX` entries) and thread-safe.
    On first construction `load_markets()` is pre-called so concurrent method
    calls don't race on the lazy-initialized markets table.
    """
    exchange_id = exchange_id.lower().strip()
    cls = getattr(ccxt, exchange_id, None)
    if cls is None or not isinstance(cls, type) or not issubclass(cls, ccxt.Exchange):
        raise ValueError(
            f"Unknown CCXT exchange id: {exchange_id!r}. "
            f"Use list_supported_exchanges to see valid ids."
        )

    with _exchange_cache_lock:
        if exchange_id in _exchange_cache:
            _exchange_cache.move_to_end(exchange_id)
            return _exchange_cache[exchange_id]

    # Build outside the cache lock; serialize construction per-id.
    lock = _per_lock(exchange_id)
    with lock:
        # Re-check after acquiring per-id lock — another thread may have built
        # and cached it while we waited.
        with _exchange_cache_lock:
            if exchange_id in _exchange_cache:
                _exchange_cache.move_to_end(exchange_id)
                return _exchange_cache[exchange_id]

        inst = cls({"enableRateLimit": True, "timeout": int(DEFAULT_TIMEOUT * 1000)})
        # Pre-load markets so the first per-method call doesn't race on lazy
        # initialization. Failures (geo-block, no markets endpoint) are
        # ignored here; the actual call site will surface a clean error.
        try:
            inst.load_markets()
        except Exception:
            pass

        with _exchange_cache_lock:
            _exchange_cache[exchange_id] = inst
            _exchange_cache.move_to_end(exchange_id)
            while len(_exchange_cache) > _CCXT_CACHE_MAX:
                evicted_id, evicted_inst = _exchange_cache.popitem(last=False)
                try:
                    if hasattr(evicted_inst, "close"):
                        evicted_inst.close()
                except Exception:
                    pass
            return inst


async def _ccxt_call(fn, *args, exchange_id: str | None = None, **kwargs):
    """Run a blocking CCXT call in a thread; return structured error on failure.

    When `exchange_id` is supplied, the call is serialized through the
    per-exchange lock so concurrent fetches against the same instance don't
    corrupt CCXT's internal session/rate-limit state. When omitted (legacy
    callers), no lock is taken — preserving prior behavior.
    """
    loop = asyncio.get_running_loop()

    def _runner():
        if exchange_id:
            with _per_lock(exchange_id):
                return fn(*args, **kwargs)
        return fn(*args, **kwargs)

    try:
        return await loop.run_in_executor(None, _runner)
    except ccxt.BaseError as e:
        return {"error": f"{type(e).__name__}: {e}"}
    except Exception as e:  # pragma: no cover - safety net
        return {"error": f"unexpected error: {type(e).__name__}: {e}"}


# Import last so the cache module's @mcp.tool() registrations run at module
# load. cache imports `mcp` and `DEFAULT_TIMEOUT` from this module, so this
# must come AFTER those names exist.
from . import cache  # noqa: E402,F401
