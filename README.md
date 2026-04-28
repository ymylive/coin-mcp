# coin-mcp

> A comprehensive cryptocurrency market-data MCP server. Six data sources, 49 tools, 8 prompt templates, 3 resources — wired together so an LLM can answer almost any "what's the market doing?" question with a single call.

[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-1.2%2B-green.svg)](https://modelcontextprotocol.io/)
[![License](https://img.shields.io/badge/license-MIT-lightgrey.svg)](#license)
[![Tests](https://img.shields.io/badge/tests-29%20passing-brightgreen.svg)](#testing)

---

## Table of contents

- [Why coin-mcp](#why-coin-mcp)
- [What it can answer](#what-it-can-answer)
- [Quickstart](#quickstart)
- [Architecture](#architecture)
- [Tool catalog (49)](#tool-catalog-49)
- [Prompt templates (8)](#prompt-templates-8)
- [Resources (3)](#resources-3)
- [Configuration](#configuration)
- [MCP client integration](#mcp-client-integration)
- [Transports](#transports)
- [Caching layer](#caching-layer)
- [Security model](#security-model)
- [Project structure](#project-structure)
- [Testing](#testing)
- [Roadmap](#roadmap)
- [License](#license)
- [Acknowledgments](#acknowledgments)

---

## Why coin-mcp

Most crypto MCP servers are thin wrappers around a single API and hit a wall the moment a user asks anything that requires combining sources. `coin-mcp` is built around a different premise: a strong AI assistant needs **complementary** data sources behind one consistent contract.

| Source | Strength | What it covers here |
|---|---|---|
| **CoinGecko** | Aggregated, volume-weighted | Price, market cap, history, NFTs, categories, treasuries, trending, search |
| **CCXT** | Real-time, per-exchange | Tickers, order books, recent trades, OHLCV, funding rates across 100+ exchanges |
| **DefiLlama** | DeFi-native, free | Protocol/chain TVL, stablecoins, yield pools, DEX volume, fees & revenue |
| **DexScreener** | DEX-side, long-tail tokens | Pairs, liquidity, prices for tokens too small/new for CoinGecko |
| **Alternative.me** | Sentiment | Crypto Fear & Greed Index |
| **Local TA** | Offline compute | RSI, MACD, Bollinger, EMA/SMA, ATR, ADX, Stochastic, OBV |

Plus a tiered HTTP cache that keeps you safely under CoinGecko's free-tier rate limit, and a multi-transport runtime (stdio / SSE / streamable-HTTP).

## What it can answer

A non-exhaustive sample of questions a connected LLM can resolve in 1–3 tool calls:

- "What is BTC trading at right now?" → `get_price`
- "Compare BTC's price across Coinbase, Kraken, OKX and DexScreener and tell me the spread." → `compare_prices`
- "Give me a 30-day chart and run RSI, MACD and Bollinger on it." → `get_market_chart` + `compute_indicators`
- "Where is the best bid for ETH/USDT across exchanges?" → `get_consolidated_orderbook`
- "Funding rates for the BTC perp on Binance, OKX, Bybit, Bitmex — who's paying who?" → `compare_funding_rates`
- "What protocols are leading TVL on Solana right now?" → `list_protocols(chain="Solana")`
- "Find me yield pools paying > 10% APY on stablecoins with > $5M TVL." → `list_yield_pools`
- "Is the market fearful or greedy today?" → `get_fear_greed_index`
- "Is everything healthy? I'm seeing weird data." → `health_check`
- "Which public companies hold BTC?" → `get_companies_holdings`

## Quickstart

```bash
# Clone and install
git clone https://github.com/ymylive/coin-mcp.git
cd coin-mcp
uv sync                          # uv recommended; pip works too

# Run via stdio (the MCP standard for local clients like Claude Desktop)
uv run coin-mcp

# Or HTTP for hosted / remote use
uv run coin-mcp --transport streamable-http --port 8000
```

A 30-second smoke test:

```bash
uv run python -c "
import asyncio, server
async def main():
    tools = await server.mcp.list_tools()
    print(f'{len(tools)} tools registered')
asyncio.run(main())
"
# 49 tools registered
```

## Architecture

```
                        ┌──────────────────────────────┐
                        │       LLM / MCP client       │
                        │   Claude · Cursor · custom   │
                        └──────────────┬───────────────┘
                                       │ JSON-RPC over stdio / SSE / HTTP
                        ┌──────────────▼───────────────┐
                        │       FastMCP server         │
                        │   49 tools · 8 prompts · 3   │
                        │   resources · multi-transport│
                        └──────────────┬───────────────┘
                                       │
                ┌──────────────────────┼──────────────────────┐
                │                      │                      │
       ┌────────▼─────────┐   ┌────────▼────────┐   ┌────────▼────────┐
       │ HTTP layer       │   │  CCXT runtime   │   │  Local compute  │
       │ httpx + tiered   │   │  Bounded LRU    │   │  Indicators     │
       │ TTL LRU cache    │   │  + per-id RLock │   │  (no I/O)       │
       │ + auth-aware key │   │  + pre-warm     │   │                 │
       └────────┬─────────┘   └────────┬────────┘   └─────────────────┘
                │                      │
   ┌────────────┼──────────────┐       │
   ▼            ▼              ▼       ▼
CoinGecko  DefiLlama  DexScreener   100+ exchanges
                                    (Binance, OKX, Coinbase,
                                     Kraken, Bybit, …)
```

Key design choices:

- **One tool per endpoint, with rich AI-facing docstrings.** No clever endpoint factories — the docstrings ARE the API contract that the LLM reads to decide which tool to call. Forty-nine docstrings is a feature, not a bug.
- **`{"error": "..."}` envelope contract.** Every HTTP-backed tool returns either upstream JSON or an error envelope. The cache never caches errors. The LLM never raises.
- **Path-injection guards on every URL-interpolated parameter.** Coin IDs, protocol slugs, addresses are validated against strict regexes before any HTTP call.
- **Per-exchange RLock + pre-warmed `load_markets()`.** Concurrent CCXT calls don't race the lazy markets table.
- **Auth-aware cache key.** Header values for known auth headers are hashed into the cache key — multi-tenant deployments don't leak.

## Tool catalog (49)

### Aggregated market data — CoinGecko (18)

| Tool | What it does |
|---|---|
| `get_price` | Current spot price for one or more coins, multi-currency |
| `get_coin_details` | Full coin metadata: description, links, scores, market data, dev/community stats |
| `get_market_chart` | Historical price / market cap / volume time series |
| `get_aggregated_ohlc` | Aggregated OHLC candlesticks across all venues |
| `get_coin_tickers` | Exchange tickers for a coin, sorted by trust score / volume |
| `search` | Universal search across coins, exchanges, categories, NFTs |
| `list_top_coins` | Top coins with full market data, sortable + paginated |
| `get_trending` | Trending coins / NFTs / categories (last-24h searches) |
| `get_top_gainers_losers` | Biggest 24h movers (Pro endpoint; falls back to `list_top_coins`) |
| `get_global_market` | Total market cap, BTC/ETH dominance, # of active assets |
| `get_global_defi` | Global DeFi market cap and DeFi-to-ETH ratio |
| `list_categories` | All coin categories with aggregated market data |
| `list_exchanges_directory` | Centralized exchanges directory ranked by trust score |
| `get_exchange_info` | Single-exchange detail: description, links, tickers |
| `list_derivatives_exchanges` | Derivatives venues by open interest / volume |
| `list_nfts` | NFT collections sortable by floor / cap / volume |
| `get_nft_collection` | Single NFT collection detail |
| `get_companies_holdings` | Public companies holding BTC or ETH |

### Real-time per-exchange — CCXT (7)

| Tool | What it does |
|---|---|
| `list_supported_exchanges` | All 111 CCXT-supported exchange IDs |
| `get_exchange_markets` | All markets/symbols on a specific exchange |
| `get_exchange_ticker` | Real-time bid/ask/last/24h on one exchange |
| `get_orderbook` | Level-2 order book snapshot |
| `get_recent_trades` | Recent public trades (the tape) |
| `get_exchange_ohlcv` | OHLCV candles, 1m granularity, per-exchange volume |
| `get_funding_rate` | Current perpetual-futures funding rate |

### Derivatives extensions — CCXT (3)

| Tool | What it does |
|---|---|
| `get_funding_rate_history` | Time-series funding rates for a perp |
| `get_open_interest` | Current open interest with `fetch_open_interest_history` fallback |
| `compare_funding_rates` | Cross-exchange funding-rate snapshot with max/min/spread |

### DeFi-native — DefiLlama (9)

| Tool | What it does |
|---|---|
| `list_protocols` | All DeFi protocols ranked by TVL, sortable, chain-filterable |
| `get_protocol_tvl` | Single protocol detail with trimmed TVL history |
| `list_chains_tvl` | TVL per chain |
| `get_chain_tvl_history` | Historical TVL for a chain (or total DeFi) |
| `list_stablecoins` | Top stablecoins by mcap with chain breakdown |
| `list_yield_pools` | Yield pools filtered by TVL / project / chain / symbol |
| `list_dex_volumes` | DEX 24h volume rankings |
| `list_fees_revenue` | Protocols ranked by fees or revenue |
| `get_token_dex_price` | DefiLlama oracle price for `chain:address` tokens |

### DEX-side — DexScreener (5)

| Tool | What it does |
|---|---|
| `dex_search` | Search pairs across all chains by token name/symbol/address |
| `get_dex_token_pairs` | All pairs for a token contract address |
| `get_dex_pair` | Single pair detail by chain + pair address |
| `list_latest_dex_tokens` | Newly profiled DexScreener tokens |
| `list_top_boosted_tokens` | Currently top-boosted (paid promotion) tokens |

### Cross-source aggregation (3)

| Tool | What it does |
|---|---|
| `health_check` | Parallel-ping every data source with latency |
| `compare_prices` | Same coin's price across CG + multiple CEX + DEX with spread |
| `get_consolidated_orderbook` | Merged L2 across many exchanges, attributed per-level |

### Sentiment (1)

| Tool | What it does |
|---|---|
| `get_fear_greed_index` | Crypto Fear & Greed Index (0 = extreme fear, 100 = extreme greed) |

### Local technical indicators (1)

| Tool | What it does |
|---|---|
| `compute_indicators` | RSI, MACD, Bollinger, EMA, SMA, ATR, ADX, Stochastic, OBV — pure-Python on supplied OHLCV |

### Cache observability (2)

| Tool | What it does |
|---|---|
| `cache_stats` | Cache entries, hits, misses, hit rate, per-pattern breakdown |
| `clear_cache` | Drop the in-process HTTP cache |

## Prompt templates (8)

Prompts are parameterized workflows the user can pick from a UI; they expand into ready-made multi-step instructions the LLM executes using tools.

| Prompt | What it does |
|---|---|
| `analyze_coin` | Full coin briefing: identity, price, charts, where it trades |
| `compare_coins` | Side-by-side comparison across N coins |
| `technical_analysis` | Fetch OHLCV + run full indicator bundle, summarize trend/momentum/volatility |
| `scan_funding_arbitrage` | Funding rates across N exchanges, surface spread |
| `market_overview` | Macro briefing: cap, dominance, sentiment, trending, top movers |
| `defi_health_check` | TVL by chain + per-protocol view + stablecoin landscape |
| `find_token_dex` | Search → fall back to DexScreener for new/long-tail tokens |
| `yield_hunter` | Filter yield pools by TVL / chain / asset, sort by 30-day avg APY |

## Resources (3)

Static reference data the client can attach to the LLM's context:

- `coin-mcp://exchanges/ccxt` — JSON list of all CCXT-supported exchange IDs
- `coin-mcp://coins/popular-ids` — markdown table mapping common ticker symbols to CoinGecko IDs
- `coin-mcp://chains/dex-supported` — markdown list of chain IDs DexScreener accepts

## Configuration

All env vars are optional.

| Variable | Default | Effect |
|---|---|---|
| `COINGECKO_API_KEY` | _(unset)_ | When set, the server uses the Pro endpoint and authenticates requests. Pro keys (prefixed `CG-`) send `x-cg-pro-api-key`; otherwise Demo (`x-cg-demo-api-key`). |

A `.env.example` is provided. Copy to `.env` and edit if you have a key.

## MCP client integration

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "coin-mcp": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/coin-mcp",
        "run",
        "coin-mcp"
      ],
      "env": {
        "COINGECKO_API_KEY": "your-optional-key"
      }
    }
  }
}
```

Restart Claude Desktop. The 49 tools, 8 prompts and 3 resources will appear.

### Cursor

Cursor's MCP support uses the same JSON shape. Place the snippet in your Cursor settings under `mcp.servers`. For remote use, run the server with `--transport sse` or `--transport streamable-http` and point Cursor at the URL.

### Custom client

Any MCP-compatible client works. The server speaks the standard MCP JSON-RPC over your transport of choice.

## Transports

```bash
# stdio (default, for local IDE / Claude Desktop integration)
coin-mcp

# Server-Sent Events
coin-mcp --transport sse --host 127.0.0.1 --port 8000

# Streamable HTTP (for hosted / remote use)
coin-mcp --transport streamable-http --host 127.0.0.1 --port 8000

# Public bind (with no auth) — REQUIRES explicit flag and prints a stderr WARNING
coin-mcp --transport streamable-http --host 0.0.0.0 --port 8000 --allow-public
```

There is no built-in authentication. If you bind to a non-loopback host, **put a reverse proxy with auth in front** (Caddy, nginx, Traefik, Cloudflare Access, etc.) or restrict via firewall. The `--allow-public` flag is a deliberate footgun guard.

## Caching layer

A tiered TTL cache sits transparently behind every HTTP call so the LLM can fan out without burning your CoinGecko quota.

| Endpoint pattern | TTL |
|---|---|
| `/simple/price` | 10 s |
| `/coins/markets` | 60 s |
| `/market_chart`, `/ohlc` | 60 s |
| `/tickers` | 30 s |
| `/search/trending` | 60 s |
| `/search` | 5 min |
| `/global` | 60 s |
| `/coins/{id}` (details) | 2 min |
| `/coins/categories` | 5 min |
| `/exchanges/{id}` | 10 min |
| `/exchanges` (list) | 30 min |
| `/derivatives/exchanges` | 10 min |
| `/nfts/{id}` | 5 min |
| `/nfts/list` | 30 min |
| `/companies/public_treasury` | 30 min |
| `api.alternative.me` | 10 min |
| _default_ | 30 s |

LRU-bounded at 2,000 entries. Cache key includes the URL, sorted query params, and a sha-256 digest of any auth header values — so two clients with different keys never share a cache entry. Errors are never cached. Hits return a deep copy so callers can mutate without poisoning. Inspect at runtime via the `cache_stats` MCP tool.

## Security model

`coin-mcp` is data-only — there is no transaction signing, no private-key handling, no on-chain writes. The threat surface is therefore narrow but worth being honest about:

- **Path-injection guards** on every URL-interpolated parameter (coin IDs, slugs, addresses). Invalid input returns `{"error": "invalid <kind>"}` without making the HTTP call.
- **`compute_indicators` row cap** at 5,000 (`MAX_OHLCV_ROWS`) prevents memory blowups from prompt-injected upstream data.
- **CCXT exchange cache bounded** (LRU 16) so an LLM can't force allocation of all 111 exchanges.
- **Network transports refuse non-loopback bind** without `--allow-public` and emit a stderr WARNING when the flag is given.
- **Cache key is auth-aware** — auth headers contribute to the key via sha-256 digest. Multi-tenant safe.
- **No emoji of stdout in stdio mode** — every log goes to stderr, so JSON-RPC framing is never corrupted.

If you find a security issue, please open an issue rather than a PR for non-trivial cases.

## Project structure

```
coin-mcp/
├── server.py                   # Entrypoint — wires modules together
├── pyproject.toml
├── README.md
├── .env.example
├── .gitignore
├── coin_mcp/
│   ├── core.py                 # FastMCP instance, helpers, instructions block
│   ├── coingecko.py            # 18 CoinGecko tools
│   ├── ccxt_tools.py           # 7 CCXT tools
│   ├── derivatives.py          # 3 funding/OI tools
│   ├── defillama.py            # 9 DefiLlama tools
│   ├── dexscreener.py          # 5 DexScreener tools
│   ├── aggregate.py            # 3 cross-source tools
│   ├── sentiment.py            # 1 Fear & Greed tool
│   ├── indicators.py           # 1 local-compute tool
│   ├── cache.py                # 2 cache-introspection tools + the cache itself
│   ├── prompts.py              # 8 workflow templates
│   ├── resources.py            # 3 static-reference resources
│   └── transport.py            # CLI for stdio / SSE / streamable-HTTP
└── tests/
    ├── conftest.py             # Autouse cache-clear + mcp_server fixture
    ├── test_registry.py        # Tool count / instructions / prompt-ref sanity
    ├── test_cache_routing.py   # TTL routing table + no-shadowing
    ├── test_indicators.py      # Wilder RSI textbook vector, 5-col fallback, DoS cap
    ├── test_http_envelope.py   # is_error truthy semantics + error not cached
    └── test_validators.py      # Path-injection rejection (CG/DexScreener)
```

## Testing

```bash
uv sync --extra dev
uv run pytest
# 29 passed
```

The high-ROI tests cover:

- Every registered tool appears in the `instructions` table (LLM discoverability)
- Every prompt body references only real tool names and real parameters
- Cache TTL routing for 17 representative URLs
- Cache no-shadowing (`/coins/markets` vs `/coins/{id}` vs `/market_chart`)
- Wilder RSI matches the textbook 14-close reference vector (~70.46)
- 5-column OHLCV input doesn't crash OBV
- `compute_indicators` rejects > 5,000 rows
- `is_error` truthy semantics
- HTTP error responses are not cached
- Path-injection on CoinGecko / DexScreener tools is rejected before any HTTP call

## Roadmap

Tracked but not yet built:

- Whale-transaction monitoring (Whale Alert)
- Etherscan / blockchain-explorer-style read-only RPC
- News + social aggregation (CryptoPanic / Santiment)
- Aggregated open-interest history (cross-exchange)
- Optional READ-ONLY signed requests for higher-tier API keys

If you have a specific data source you want integrated, open an issue with the API spec.

## License

MIT — see [LICENSE](#) for the full text.

## Acknowledgments

This project stands on shoulders.

- [Model Context Protocol](https://modelcontextprotocol.io/) — Anthropic's open standard for AI ↔ tool wiring
- [CoinGecko API](https://www.coingecko.com/en/api) — the most generous free crypto-data tier on the internet
- [CCXT](https://github.com/ccxt/ccxt) — the unified exchange library that makes 100+ venues feel like one
- [DefiLlama](https://defillama.com/) — DeFi data infrastructure as a public good
- [DexScreener](https://dexscreener.com/) — DEX-side market data with no auth required
- [Alternative.me](https://alternative.me/crypto/fear-and-greed-index/) — the Crypto Fear & Greed Index

And all the prior MCP-server projects whose design choices we studied:
[doggybee/mcp-server-ccxt](https://github.com/doggybee/mcp-server-ccxt) for the tiered cache pattern,
[QuantGeekDev/coincap-mcp](https://github.com/QuantGeekDev/coincap-mcp) for showing prompts matter,
[heurist-network/heurist-mesh-mcp-server](https://github.com/heurist-network/heurist-mesh-mcp-server) for multi-transport precedent,
the official [CoinGecko MCP](https://docs.coingecko.com/docs/mcp-server) for dynamic tool discovery as an idea worth pursuing later.
