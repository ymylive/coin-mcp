# All tool names and parameter names below are verified against the registered tools.
"""coin-mcp prompt templates.

Each prompt expands into a tight, AI-facing instruction string that names
the exact tools to call and the order to call them in. The LLM reads the
returned string and executes — these are user-pickable workflow buttons,
not free-form chatter.
"""
from __future__ import annotations

from .core import mcp


@mcp.prompt()
def analyze_coin(coin_id: str, vs_currency: str = "usd") -> str:
    """Full briefing on a single coin: identity, price action, venues, synthesis."""
    return (
        f"Produce a complete briefing for CoinGecko coin id '{coin_id}' priced in {vs_currency}.\n"
        "Execute these steps in order, then synthesize:\n"
        f"1. Call `get_coin_details(coin_id='{coin_id}')` for identity, category, links, description.\n"
        f"2. Call `get_price(coin_ids='{coin_id}', vs_currencies='{vs_currency}', "
        "include_market_cap=true, include_24hr_vol=true, include_24hr_change=true, "
        "include_last_updated_at=true)` for the live snapshot.\n"
        f"3. Call `get_market_chart(coin_id='{coin_id}', vs_currency='{vs_currency}', days=90)` "
        "and describe the 90-day shape (uptrend / downtrend / range, volume regime, notable moves).\n"
        f"4. Call `get_coin_tickers(coin_id='{coin_id}')` and list the top 5 venues by volume.\n"
        "5. Compute 7d and 30d % change from the market chart series since `get_price` only ships 24h.\n"
        "Output sections (markdown):\n"
        "- **Identity** (name, symbol, categories, 1-line description)\n"
        "- **Price** (current, 24h / 7d / 30d % change, market cap, 24h volume)\n"
        "- **90-day chart** (one paragraph, observational)\n"
        "- **Where it trades** (top 5 venues + pair, share of volume)\n"
        "- **Synthesis** (one paragraph; what stands out, no price predictions)\n"
    )


@mcp.prompt()
def compare_coins(coin_ids: str, vs_currency: str = "usd") -> str:
    """Side-by-side comparison of multiple coins on the metrics that matter."""
    return (
        f"Compare these CoinGecko coin ids head-to-head: {coin_ids} (priced in {vs_currency}).\n"
        "Execute:\n"
        f"1. Call `get_price(coin_ids='{coin_ids}', vs_currencies='{vs_currency}', "
        "include_market_cap=true, include_24hr_vol=true, include_24hr_change=true)` "
        "for the snapshot row.\n"
        "2. For richer fields (rank, ATH, 7d / 30d change), call `get_coin_details(coin_id=...)` "
        "for each id in the list.\n"
        "3. Compute % off ATH = (current - ath) / ath * 100 from the details payload.\n"
        "Render a single markdown table with one row per coin and these columns:\n"
        "`Coin | Rank | Price | 24h % | 7d % | 30d % | Mkt Cap | 24h Vol | % off ATH`.\n"
        "Sort by market-cap rank ascending. End with a 2-3 sentence note on the biggest divergence "
        "(strongest / weakest performer, biggest ATH discount, etc.)."
    )


@mcp.prompt()
def technical_analysis(
    symbol: str,
    exchange_id: str = "binance",
    timeframe: str = "1h",
    lookback: int = 200,
) -> str:
    """Run the standard indicator bundle on an exchange's candles and summarize."""
    return (
        f"Run a technical analysis on {symbol} on {exchange_id} at {timeframe} resolution.\n"
        "Steps:\n"
        f"1. Call `get_exchange_ohlcv(exchange_id='{exchange_id}', symbol='{symbol}', "
        f"timeframe='{timeframe}', limit={lookback})` to pull the candles.\n"
        "2. Pass that OHLCV array to `compute_indicators(...)` requesting the standard bundle: "
        "RSI(14), MACD(12,26,9), Bollinger(20,2), EMA(20), EMA(50), EMA(200), SMA(50), SMA(200), ATR(14).\n"
        "3. Read the most recent value of each indicator from the returned series.\n"
        "Output sections (be observational, not prescriptive — no buy/sell calls):\n"
        "- **Trend** (price vs EMA20 / EMA50 / EMA200, alignment, slope)\n"
        "- **Momentum** (RSI level + zone, MACD line vs signal, histogram direction)\n"
        "- **Volatility** (Bollinger band width, ATR vs typical, position within bands)\n"
        "- **Key levels** (recent swing high / low from the candles, nearest EMA as dynamic S/R)\n"
        "- **Read** (one paragraph synthesizing the above)\n"
        "Do NOT recommend trades. Stick to what the indicators say."
    )


@mcp.prompt()
def scan_funding_arbitrage(
    base_symbol: str = "BTC",
    exchange_ids: str = "binance,okx,bybit,bitmex",
) -> str:
    """Compare perp funding rates across exchanges and flag the spread + outliers."""
    return (
        f"Scan perpetual funding rates for {base_symbol} across these exchanges: {exchange_ids}.\n"
        "Steps:\n"
        f"1. For each exchange in '{exchange_ids}' (split on ','), pick the canonical USDT-margined "
        f"linear perp symbol — usually '{base_symbol}/USDT:USDT'. For bitmex use the inverse "
        f"perp '{base_symbol}/USD:{base_symbol}' instead.\n"
        "2. Call `get_funding_rate(exchange_id=..., symbol=...)` for each. Skip and note any errors.\n"
        "3. Collect funding rate, predicted next rate (if present), and funding interval.\n"
        "Output:\n"
        "- A markdown table: `Exchange | Symbol | Funding Rate | Annualized | Next Funding`.\n"
        "  Annualize assuming 3 fundings/day for 8h cycles, 365 days.\n"
        "- The spread = max - min funding rate, in basis points.\n"
        "- Plain-English direction per row: positive funding -> longs pay shorts, "
        "negative -> shorts pay longs.\n"
        "- Call out any exchange that is more than 2x the median rate as an outlier."
    )


@mcp.prompt()
def market_overview() -> str:
    """Daily macro briefing: caps, dominance, sentiment, trending narratives, movers."""
    return (
        "Produce a Bloomberg-style daily crypto macro briefing. Steps:\n"
        "1. `get_global_market()` -> total market cap, 24h cap change, BTC.D, ETH.D, total volume.\n"
        "2. `get_fear_greed_index(limit=1)` -> current sentiment value + label.\n"
        "3. `get_trending()` -> top 7 trending coins / narratives on CoinGecko search.\n"
        "4. `list_top_coins(per_page=10)` -> the megacap leaderboard with 24h moves.\n"
        "5. `get_top_gainers_losers(vs_currency='usd', duration='24h', top_coins='1000')` "
        "-> biggest 24h winners and losers.\n"
        "Output exactly 5 bullets, terse and quantitative:\n"
        "- **Caps & Dominance** (total cap $X.XXT, 24h ±x.x%, BTC.D xx.x%, ETH.D xx.x%)\n"
        "- **Sentiment** (Fear & Greed = N / label, vs prior reading if obvious)\n"
        "- **Megacaps** (notable mover among top 10 with %)\n"
        "- **Movers** (top 3 gainers, top 3 losers with %)\n"
        "- **Narratives** (trending coins / themes from search)\n"
        "Keep each bullet to one line where possible. No predictions."
    )


@mcp.prompt()
def defi_health_check(chain: str = "") -> str:
    """Snapshot DeFi state: chain TVL, top protocols, stablecoin supply."""
    scope = f"chain='{chain}'" if chain else "the entire DeFi space"
    chain_step = (
        f"1. `list_chains_tvl()` and find '{chain}' to anchor its current TVL and rank.\n"
        f"2. `get_chain_tvl_history(chain='{chain}')` and describe TVL trend over the last "
        "30 / 90 days (% change, regime).\n"
        f"3. `list_protocols(chain='{chain}')` -> top 10 protocols on this chain by TVL.\n"
        if chain else
        "1. `list_chains_tvl()` -> top 10 chains by TVL with their 1d / 7d % change.\n"
        "2. Skip per-chain history; cover the aggregate picture.\n"
        "3. `list_protocols()` -> top 10 protocols globally by TVL.\n"
    )
    return (
        f"Paint a picture of DeFi health for {scope}.\n"
        f"{chain_step}"
        "4. `list_stablecoins()` -> total stablecoin market cap, top 5 issuers, "
        "and (if a chain was given) supply on that chain.\n"
        "Output (markdown):\n"
        "- **TVL** (totals, trend, leaders)\n"
        "- **Top Protocols** (table: Protocol | Category | TVL | 1d % | 7d %)\n"
        "- **Stablecoins** (total cap, dominant issuer mix)\n"
        "- **Read** (one paragraph: is liquidity flowing in or out, what stands out)\n"
    )


@mcp.prompt()
def find_token_dex(query: str) -> str:
    """Locate a token's deepest DEX market across chains and report price + liquidity."""
    return (
        f"Find the best on-chain market for '{query}'.\n"
        "Steps:\n"
        f"1. First try `search(query='{query}')` — if there's a CoinGecko hit, "
        f"call `get_coin_details(coin_id=<id>)` and `get_price(coin_ids=<id>, vs_currencies='usd', "
        "include_24hr_vol=true, include_24hr_change=true)` to anchor the aggregated price.\n"
        f"2. Then call `dex_search(query='{query}')` for DEX-side pairs. If step 1 returned no hit, "
        "this is the primary source.\n"
        "3. If you have a contract address (from step 1's CoinGecko hit, or from a `dex_search` "
        "result), call `get_dex_token_pairs(token_address=<address>)` to enumerate every pair "
        "for that token across chains.\n"
        "4. Sort the returned pairs by USD liquidity descending.\n"
        "Output:\n"
        "- The DEX / chain / pair with the deepest liquidity.\n"
        "- That pair's price, 24h volume, 24h price change, and liquidity.\n"
        "- A short list (up to 5) of next-deepest pairs across other chains for cross-reference.\n"
        "- Whether the CoinGecko aggregated price (if any) and the deepest DEX price agree, "
        "and by how much."
    )


@mcp.prompt()
def yield_hunter(
    min_tvl_usd: float = 5_000_000,
    apy_threshold_pct: float = 5.0,
    chain: str = "",
    symbol: str = "",
) -> str:
    """Surface top DeFi yield pools matching TVL / APY / chain / symbol filters."""
    chain_filter = f", chain='{chain}'" if chain else ""
    symbol_filter = f", symbol='{symbol}'" if symbol else ""
    return (
        f"Find DeFi yield opportunities with TVL >= ${min_tvl_usd:,.0f} and APY >= {apy_threshold_pct}%"
        + (f" on chain '{chain}'" if chain else "")
        + (f" matching symbol substring '{symbol}'" if symbol else "")
        + ".\n"
        "Steps:\n"
        f"1. Call `list_yield_pools(min_tvl_usd={min_tvl_usd}{chain_filter}{symbol_filter}, "
        "sort_by='apyMean30d', limit=200)`. Note: `list_yield_pools` does not accept an APY "
        "threshold parameter — we sort server-side by 30d-mean APY and filter by APY client-side below.\n"
        f"2. Drop any pool whose `apyMean30d` (falling back to `apy` when 30d mean is null) "
        f"is below {apy_threshold_pct}.\n"
        "3. Take the top 10 of the remaining pools, ordered by `apyMean30d` desc (fallback `apy`).\n"
        "Output a markdown table: `Project | Chain | Symbol | APY (spot) | APY 30d | TVL | IL Risk`.\n"
        "For any pool whose APY (spot OR 30d) exceeds 30%, append a one-line risk caveat after "
        "the row noting that triple-digit APYs typically reflect emissions, low TVL, or "
        "impermanent-loss / depeg exposure and tend to decay fast.\n"
        "End with a one-sentence summary of what kind of strategies dominate the top of the list."
    )
