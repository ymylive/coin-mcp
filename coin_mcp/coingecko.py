"""CoinGecko tools — aggregated market data, exchanges directory, NFTs, categories, treasuries."""
from __future__ import annotations

import re
from typing import Any, Literal

from .core import _bool_str, _cg_get, mcp

try:
    from .core import is_error
except ImportError:
    def is_error(obj: Any) -> bool:
        return isinstance(obj, dict) and bool(obj.get("error"))


# Path-injection guard: IDs interpolated into URL paths must match this.
_ID_RE = re.compile(r'^[a-z0-9][a-z0-9._-]{0,127}$')


def _validate_id(value: str, kind: str = "id") -> dict | None:
    """Return None when valid, or a JSON error dict when not.

    Used to harden tools that interpolate caller-supplied strings into URL
    paths against path traversal / query-param smuggling.
    """
    if not isinstance(value, str) or not _ID_RE.match(value):
        return {
            "error": (
                f"invalid {kind}: must match ^[a-z0-9][a-z0-9._-]{{0,127}}$, "
                f"got {value!r}"
            )
        }
    return None


@mcp.tool()
async def get_price(
    coin_ids: str,
    vs_currencies: str = "usd",
    include_market_cap: bool = False,
    include_24hr_vol: bool = False,
    include_24hr_change: bool = False,
    include_last_updated_at: bool = True,
) -> Any:
    """Get the current spot price of one or more cryptocurrencies.

    Use this for the cheapest, fastest "what is X worth right now?" lookup.
    For historical prices, use `get_market_chart` or `get_aggregated_ohlc`.
    For real-time prices on a specific venue, use `get_exchange_ticker`.

    Args:
        coin_ids: Comma-separated CoinGecko coin IDs, e.g. "bitcoin,ethereum,solana".
            IDs are NOT ticker symbols — call `search` first if unsure.
        vs_currencies: Comma-separated target currencies, e.g. "usd,eur,btc".
        include_market_cap: Include each coin's market cap in the response.
        include_24hr_vol: Include 24h trading volume.
        include_24hr_change: Include 24h price change percent.
        include_last_updated_at: Include unix timestamp of last update.

    Returns:
        Mapping of {coin_id: {currency: price, ...}}. When the optional flags
        are enabled, additional fields like "<currency>_market_cap",
        "<currency>_24h_vol", "<currency>_24h_change", and "last_updated_at"
        appear alongside the price.
    """
    return await _cg_get(
        "/simple/price",
        {
            "ids": coin_ids,
            "vs_currencies": vs_currencies,
            "include_market_cap": _bool_str(include_market_cap),
            "include_24hr_vol": _bool_str(include_24hr_vol),
            "include_24hr_change": _bool_str(include_24hr_change),
            "include_last_updated_at": _bool_str(include_last_updated_at),
        },
    )


@mcp.tool()
async def get_coin_details(
    coin_id: str,
    localization: bool = False,
    tickers: bool = False,
    market_data: bool = True,
    community_data: bool = True,
    developer_data: bool = True,
) -> Any:
    """Get rich metadata for a single coin: description, links, scores, market data, dev/community stats.

    Use this when the user wants to learn about a coin (what is it, who built
    it, links to docs/source/socials), or when you need scores like CoinGecko
    rank, sentiment up/down vote percentages, or developer activity.

    For just the price, use `get_price` (much cheaper).

    Args:
        coin_id: CoinGecko coin ID, e.g. "bitcoin".
        localization: Include localized names/descriptions for many languages.
            Usually false to keep responses small.
        tickers: Include a tickers array (large). Use `get_coin_tickers` instead
            when you specifically want exchange tickers.
        market_data: Include current price, market cap, 24h/7d/30d/1y change,
            ATH/ATL, supply, etc. Recommended.
        community_data: Twitter/Reddit/Telegram follower counts and growth.
        developer_data: GitHub stars, forks, commit counts, PR activity.

    Returns:
        A coin object with fields like `id`, `symbol`, `name`, `description`,
        `links`, `image`, `market_cap_rank`, `market_data`, `community_data`,
        `developer_data`, `categories`, `genesis_date`, etc.

    Note: `coin_id` is validated against `^[a-z0-9][a-z0-9._-]{0,127}$`.
    """
    err = _validate_id(coin_id, "coin_id")
    if err is not None:
        return err
    return await _cg_get(
        f"/coins/{coin_id}",
        {
            "localization": _bool_str(localization),
            "tickers": _bool_str(tickers),
            "market_data": _bool_str(market_data),
            "community_data": _bool_str(community_data),
            "developer_data": _bool_str(developer_data),
            "sparkline": "false",
        },
    )


@mcp.tool()
async def get_market_chart(
    coin_id: str,
    vs_currency: str = "usd",
    days: str = "30",
    interval: Literal["", "daily"] = "",
) -> Any:
    """Get historical price, market cap and total volume time series for a coin.

    Use this to draw line charts or compute returns/volatility over a window.
    For candlestick (OHLC) data use `get_aggregated_ohlc` instead.

    Granularity is auto-selected by CoinGecko based on `days`:
      - days <= 1   -> ~5-minute datapoints
      - days <= 90  -> ~hourly datapoints
      - days >  90  -> daily datapoints

    Args:
        coin_id: CoinGecko coin ID (e.g. "bitcoin").
        vs_currency: Quote currency (e.g. "usd", "eur", "btc").
        days: Window in days. Examples: "1", "7", "14", "30", "90", "180",
            "365", or "max" for the full history.
        interval: Force daily granularity by passing "daily". Leave empty for auto.

    Returns:
        Object with three arrays of [unix_ms, value] pairs:
          - prices
          - market_caps
          - total_volumes

    Note: `coin_id` is validated against `^[a-z0-9][a-z0-9._-]{0,127}$`.
    """
    err = _validate_id(coin_id, "coin_id")
    if err is not None:
        return err
    params: dict[str, Any] = {"vs_currency": vs_currency, "days": days}
    if interval:
        params["interval"] = interval
    return await _cg_get(f"/coins/{coin_id}/market_chart", params)


@mcp.tool()
async def get_aggregated_ohlc(
    coin_id: str,
    vs_currency: str = "usd",
    days: Literal["1", "7", "14", "30", "90", "180", "365", "max"] = "30",
) -> Any:
    """Get aggregated OHLC (open/high/low/close) candlestick data across all exchanges.

    Use for technical-analysis-style candlestick views of "the market" rather
    than a single venue. For per-exchange high-granularity candles (1m, 5m, etc.),
    use `get_exchange_ohlcv` (CCXT) instead.

    Candle width auto-selected by CoinGecko based on `days`:
      - days = 1                    -> 30-minute candles
      - days in {7, 14, 30}         -> 4-hour candles
      - days in {90, 180, 365, max} -> daily candles

    Args:
        coin_id: CoinGecko coin ID.
        vs_currency: Quote currency (e.g. "usd").
        days: Window in days. One of "1","7","14","30","90","180","365","max".

    Returns:
        Array of [unix_ms, open, high, low, close] tuples.

    Note: `coin_id` is validated against `^[a-z0-9][a-z0-9._-]{0,127}$`.
    """
    err = _validate_id(coin_id, "coin_id")
    if err is not None:
        return err
    return await _cg_get(
        f"/coins/{coin_id}/ohlc",
        {"vs_currency": vs_currency, "days": days},
    )


@mcp.tool()
async def get_coin_tickers(
    coin_id: str,
    exchange_ids: str = "",
    page: int = 1,
    order: Literal[
        "trust_score_desc", "trust_score_asc", "volume_desc", "volume_asc"
    ] = "trust_score_desc",
) -> Any:
    """List exchange tickers (trading pairs) for a single coin across many venues.

    Use to answer "where can I buy/sell X?" or "which exchanges have the best
    liquidity for X?" Each ticker includes price, volume, bid-ask spread,
    converted last/volume in BTC/ETH/USD, and CoinGecko's trust score.

    Args:
        coin_id: CoinGecko coin ID.
        exchange_ids: Optional comma-separated exchange IDs to filter by
            (CoinGecko exchange IDs, see `list_exchanges_directory`).
        page: Pagination page (each page is up to 100 tickers).
        order: Sort order. Default ranks by liquidity trust score.

    Returns:
        Object with `name` (coin name) and `tickers` (array of ticker objects).

    Note: `coin_id` is validated against `^[a-z0-9][a-z0-9._-]{0,127}$`.
    """
    err = _validate_id(coin_id, "coin_id")
    if err is not None:
        return err
    params: dict[str, Any] = {"page": page, "order": order, "depth": "false"}
    if exchange_ids:
        params["exchange_ids"] = exchange_ids
    return await _cg_get(f"/coins/{coin_id}/tickers", params)


@mcp.tool()
async def search(query: str) -> Any:
    """Universal CoinGecko search — resolves names/symbols to IDs across coins, exchanges, categories and NFTs.

    ALWAYS use this first when the user mentions a coin/exchange/NFT by name
    or ticker symbol and you don't already know the canonical CoinGecko ID.
    Most other tools require IDs.

    Args:
        query: Free-text query, e.g. "btc", "uniswap", "bored ape".

    Returns:
        Object with arrays:
          - coins:       [{ id, name, symbol, market_cap_rank, ... }]
          - exchanges:   [{ id, name, market_type }]
          - categories:  [{ id, name }]
          - nfts:        [{ id, name, symbol }]
    """
    return await _cg_get("/search", {"query": query})


@mcp.tool()
async def list_top_coins(
    vs_currency: str = "usd",
    order: Literal[
        "market_cap_desc", "market_cap_asc",
        "volume_desc", "volume_asc",
        "id_asc", "id_desc",
    ] = "market_cap_desc",
    per_page: int = 100,
    page: int = 1,
    category: str = "",
    price_change_percentages: str = "24h",
) -> Any:
    """List top coins with market data (price, market cap, volume, change %) — sortable and paginated.

    The workhorse for "show me the top N coins" / "top by volume" / "top in
    DeFi" style questions. Each call returns up to 250 coins; paginate for more.

    Args:
        vs_currency: Quote currency (e.g. "usd").
        order: Sort order. Default is descending market cap.
        per_page: 1..250 coins per page.
        page: Page number, 1-indexed.
        category: Optional category ID to filter by (see `list_categories`),
            e.g. "decentralized-finance-defi", "layer-1", "meme-token".
        price_change_percentages: Comma list of windows to include —
            any of "1h,24h,7d,14d,30d,200d,1y".

    Returns:
        Array of coin objects with id, symbol, name, image, current_price,
        market_cap, market_cap_rank, total_volume, high_24h, low_24h,
        price_change_*_in_currency, ath, atl, circulating_supply, etc.
    """
    params: dict[str, Any] = {
        "vs_currency": vs_currency,
        "order": order,
        "per_page": max(1, min(per_page, 250)),
        "page": max(1, page),
        "sparkline": "false",
        "price_change_percentage": price_change_percentages,
    }
    if category:
        params["category"] = category
    return await _cg_get("/coins/markets", params)


@mcp.tool()
async def get_trending() -> Any:
    """Get currently trending coins, NFT collections and categories on CoinGecko (last 24h searches).

    Use to answer "what is the market paying attention to right now?" or to
    surface narratives. This is search-driven, not volume-driven, so it
    captures emergent interest before price moves.

    Returns:
        Object with arrays `coins`, `nfts`, and `categories`. Each coin item
        includes `item.id`, `item.name`, `item.symbol`, `item.market_cap_rank`,
        and `item.data` with price/24h-change snapshot.
    """
    return await _cg_get("/search/trending")


@mcp.tool()
async def get_top_gainers_losers(
    vs_currency: str = "usd",
    duration: Literal["1h", "24h", "7d", "14d", "30d", "60d", "1y"] = "24h",
    top_coins: Literal["300", "500", "1000", "all"] = "1000",
) -> Any:
    """Get the biggest price movers (gainers and losers) over a time window.

    NOTE: This endpoint typically requires a CoinGecko API key (Demo tier is
    fine). Without a key it may return an error — fall back to `list_top_coins`
    and sort client-side if so.

    Args:
        vs_currency: Quote currency.
        duration: Time window for price change.
        top_coins: Universe to scan — "300", "500", "1000", or "all".

    Returns:
        Object with `top_gainers` and `top_losers` arrays of coin objects
        including `usd_24h_change` (or whichever duration you chose).
    """
    return await _cg_get(
        "/coins/top_gainers_losers",
        {"vs_currency": vs_currency, "duration": duration, "top_coins": top_coins},
    )


@mcp.tool()
async def get_global_market() -> Any:
    """Get global cryptocurrency market stats: total market cap, total 24h volume, BTC/ETH dominance.

    Use for macro questions like "what's the total crypto market cap?",
    "is BTC dominance rising?", "how many coins exist?".

    Returns:
        Object with `data` containing:
          - active_cryptocurrencies, upcoming_icos, ongoing_icos, ended_icos, markets
          - total_market_cap (mapping of currency -> amount)
          - total_volume     (mapping of currency -> amount)
          - market_cap_percentage (per-coin share of total cap, e.g. {"btc": 52.1})
          - market_cap_change_percentage_24h_usd
          - updated_at
    """
    return await _cg_get("/global")


@mcp.tool()
async def get_global_defi() -> Any:
    """Get global DeFi market stats: total DeFi market cap, DeFi-to-Eth ratio, top DeFi coin by share.

    For protocol-level TVL or chain-level breakdowns, use `get_protocol_tvl`
    or `list_chains_tvl` (DefiLlama) instead — they're much more granular.

    Returns:
        Object with `data` containing `defi_market_cap`, `eth_market_cap`,
        `defi_to_eth_ratio`, `trading_volume_24h`, `defi_dominance`,
        `top_coin_name`, `top_coin_defi_dominance`.
    """
    return await _cg_get("/global/decentralized_finance_defi")


@mcp.tool()
async def list_categories(order: Literal[
    "market_cap_desc", "market_cap_asc",
    "name_desc", "name_asc",
    "market_cap_change_24h_desc", "market_cap_change_24h_asc",
] = "market_cap_desc") -> Any:
    """List all coin categories with aggregated market data (market cap, 24h volume, 24h change).

    Use to discover narratives ("Layer 1", "DeFi", "Meme", "AI", "RWA",
    "Liquid Staking", ...) and to find category IDs you can pass to
    `list_top_coins(category=...)`.

    Args:
        order: Sort order.

    Returns:
        Array of categories with `id`, `name`, `market_cap`,
        `market_cap_change_24h`, `volume_24h`, `top_3_coins`, `updated_at`.
    """
    return await _cg_get("/coins/categories", {"order": order})


@mcp.tool()
async def list_exchanges_directory(per_page: int = 100, page: int = 1) -> Any:
    """List centralized exchanges from CoinGecko's directory, ranked by trust score / volume.

    This is CoinGecko's curated directory with metadata (year established,
    country, trust scores, 24h BTC-equivalent volume). For exchanges you can
    actually query in real time via this MCP, see `list_supported_exchanges`.

    Args:
        per_page: 1..250 exchanges per page.
        page: Page number.

    Returns:
        Array of exchanges with `id`, `name`, `year_established`, `country`,
        `url`, `image`, `trust_score`, `trust_score_rank`, `trade_volume_24h_btc`,
        `trade_volume_24h_btc_normalized`.
    """
    return await _cg_get(
        "/exchanges",
        {"per_page": max(1, min(per_page, 250)), "page": max(1, page)},
    )


@mcp.tool()
async def get_exchange_info(exchange_id: str) -> Any:
    """Get detailed info on a single exchange (CoinGecko directory): description, links, volume, top tickers.

    Args:
        exchange_id: CoinGecko exchange ID (e.g. "binance", "gdax", "kraken").
            See `list_exchanges_directory` to discover IDs. NOTE: CoinGecko
            exchange IDs sometimes differ from CCXT IDs (e.g. CoinGecko uses
            "gdax" for Coinbase Pro).

    Returns:
        Exchange object with name, year_established, country, description,
        url, image, trust score, volume metrics, and a tickers array.

    Note: `exchange_id` is validated against `^[a-z0-9][a-z0-9._-]{0,127}$`.
    """
    err = _validate_id(exchange_id, "exchange_id")
    if err is not None:
        return err
    return await _cg_get(f"/exchanges/{exchange_id}")


@mcp.tool()
async def list_derivatives_exchanges(
    order: Literal[
        "name_asc", "name_desc",
        "open_interest_btc_asc", "open_interest_btc_desc",
        "trade_volume_24h_btc_asc", "trade_volume_24h_btc_desc",
    ] = "open_interest_btc_desc",
    per_page: int = 50,
    page: int = 1,
) -> Any:
    """List derivatives (futures/perp/options) exchanges ranked by open interest or volume.

    Use to compare derivatives venues (Binance Futures, Bybit, OKX, dYdX, etc.)
    by size.

    Args:
        order: Sort order.
        per_page: 1..250 per page.
        page: Page number.

    Returns:
        Array of derivatives exchanges with `id`, `name`, `open_interest_btc`,
        `trade_volume_24h_btc`, `number_of_perpetual_pairs`,
        `number_of_futures_pairs`, `year_established`, `country`, `url`.
    """
    return await _cg_get(
        "/derivatives/exchanges",
        {"order": order, "per_page": max(1, min(per_page, 250)), "page": max(1, page)},
    )


@mcp.tool()
async def list_nfts(
    order: Literal[
        "h24_volume_native_asc", "h24_volume_native_desc",
        "floor_price_native_asc", "floor_price_native_desc",
        "market_cap_native_asc", "market_cap_native_desc",
        "market_cap_usd_asc", "market_cap_usd_desc",
    ] = "market_cap_usd_desc",
    per_page: int = 100,
    page: int = 1,
) -> Any:
    """List NFT collections sortable by floor price, market cap, or 24h volume.

    Args:
        order: Sort order.
        per_page: 1..250.
        page: Page number.

    Returns:
        Array of NFT collections with `id`, `name`, `symbol`, `asset_platform_id`,
        `contract_address`. Use `get_nft_collection` for details on one.
    """
    return await _cg_get(
        "/nfts/list",
        {"order": order, "per_page": max(1, min(per_page, 250)), "page": max(1, page)},
    )


@mcp.tool()
async def get_nft_collection(nft_id: str) -> Any:
    """Get detailed data for a single NFT collection: floor price, market cap, volume, holders, links.

    Args:
        nft_id: CoinGecko NFT collection ID, e.g. "bored-ape-yacht-club",
            "cryptopunks". Use `search` or `list_nfts` to find IDs.

    Returns:
        NFT collection object with `floor_price`, `market_cap`, `volume_24h`,
        `floor_price_in_usd_24h_percentage_change`, `number_of_unique_addresses`,
        `total_supply`, `links`, `image`, etc.

    Note: `nft_id` is validated against `^[a-z0-9][a-z0-9._-]{0,127}$`.
    """
    err = _validate_id(nft_id, "nft_id")
    if err is not None:
        return err
    return await _cg_get(f"/nfts/{nft_id}")


@mcp.tool()
async def get_companies_holdings(
    coin_id: Literal["bitcoin", "ethereum"] = "bitcoin",
) -> Any:
    """Get public companies' BTC or ETH treasury holdings.

    Useful for "which companies own BTC?" or "what's MicroStrategy's stack?"
    style questions, and for tracking institutional adoption.

    Args:
        coin_id: Either "bitcoin" or "ethereum" — those are the only assets
            CoinGecko tracks for public-treasury data.

    Returns:
        Object with `total_holdings`, `total_value_usd`, `market_cap_dominance`,
        and `companies` — an array of `{ name, symbol, country, total_holdings,
        total_entry_value_usd, total_current_value_usd, percentage_of_total_supply }`.
    """
    return await _cg_get(f"/companies/public_treasury/{coin_id}")
