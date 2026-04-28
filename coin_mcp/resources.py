"""coin-mcp passive resources.

Lightweight reference data attachable as MCP resources: CCXT-supported
exchanges (live from the installed ccxt build), a CoinGecko ID cheat-sheet
for popular tickers, and the chain IDs DexScreener accepts.
"""
from __future__ import annotations

import json

import ccxt

from .core import mcp


@mcp.resource("coin-mcp://exchanges/ccxt", mime_type="application/json")
def ccxt_exchanges() -> str:
    """JSON array of every exchange ID the installed CCXT build supports.

    Use these IDs with `get_exchange_ohlcv`, `get_orderbook`, `get_funding_rate`,
    etc. Sourced live from `ccxt.exchanges`, sorted alphabetically.
    """
    return json.dumps(sorted(ccxt.exchanges))


@mcp.resource("coin-mcp://coins/popular-ids", mime_type="text/markdown")
def popular_coin_ids() -> str:
    """Markdown lookup table from common ticker symbols to CoinGecko coin IDs.

    CoinGecko's API keys off coin IDs ("bitcoin"), not tickers ("BTC"). Use
    this table to skip a `search` round-trip for the most-asked coins.
    """
    rows = [
        ("BTC", "bitcoin"),
        ("ETH", "ethereum"),
        ("SOL", "solana"),
        ("BNB", "binancecoin"),
        ("XRP", "ripple"),
        ("ADA", "cardano"),
        ("DOGE", "dogecoin"),
        ("TRX", "tron"),
        ("DOT", "polkadot"),
        ("MATIC", "matic-network"),
        ("LINK", "chainlink"),
        ("AVAX", "avalanche-2"),
        ("LTC", "litecoin"),
        ("BCH", "bitcoin-cash"),
        ("NEAR", "near"),
        ("ATOM", "cosmos"),
        ("UNI", "uniswap"),
        ("ARB", "arbitrum"),
        ("OP", "optimism"),
        ("SUI", "sui"),
        ("APT", "aptos"),
        ("TON", "the-open-network"),
        ("SHIB", "shiba-inu"),
        ("PEPE", "pepe"),
    ]
    lines = [
        "# Popular ticker -> CoinGecko ID",
        "",
        "| Ticker | CoinGecko ID |",
        "| ------ | ------------ |",
    ]
    lines.extend(f"| {ticker} | {coin_id} |" for ticker, coin_id in rows)
    return "\n".join(lines) + "\n"


@mcp.resource("coin-mcp://chains/dex-supported", mime_type="text/markdown")
def dex_supported_chains() -> str:
    """Markdown list of chain IDs accepted by DexScreener tools.

    Pass any of these as the `chain` argument to `dex_search` / `get_dex_token`
    to scope a query to a specific chain.
    """
    chains = [
        "ethereum", "bsc", "solana", "polygon", "arbitrum", "base",
        "optimism", "avalanche", "fantom", "cronos", "linea", "blast",
        "zksync", "scroll", "mantle", "sui", "tron", "ton", "aptos",
    ]
    lines = ["# DexScreener-supported chain IDs", ""]
    lines.extend(f"- `{c}`" for c in chains)
    return "\n".join(lines) + "\n"
