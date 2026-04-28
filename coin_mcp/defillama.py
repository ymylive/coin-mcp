"""DefiLlama tools — protocol/chain TVL, stablecoins, yield pools, DEX volumes, fees & revenue.

DefiLlama is the canonical free source for DeFi-specific metrics. Use these
tools whenever the question is about TVL, stablecoin supply, yield-pool APYs,
DEX/protocol volumes, or fees/revenue — they're far more granular than
CoinGecko's `get_global_defi` (which only exposes a handful of aggregate
numbers). DefiLlama has no auth and no rate limit worth mentioning, but its
raw responses are huge, so every tool here trims, filters, and slices
client-side before returning.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from .core import (
    DEFILLAMA_BASE,
    DEFILLAMA_COINS_BASE,
    DEFILLAMA_STABLECOINS_BASE,
    DEFILLAMA_YIELDS_BASE,
    _http_get,
    mcp,
)

try:
    from .core import is_error
except ImportError:
    def is_error(obj: Any) -> bool:
        return isinstance(obj, dict) and bool(obj.get("error"))


# ---------- helpers ----------


# Path-injection guards: anything interpolated into a URL path must match.
_ID_RE = re.compile(r'^[a-z0-9][a-z0-9._-]{0,127}$')
_SYMBOL_RE = re.compile(r'^[A-Za-z0-9._-]{0,63}$')
_COINS_SEG = re.compile(
    r'^[a-z0-9-]{1,40}:'
    r'(0x[a-fA-F0-9]{40}|[1-9A-HJ-NP-Za-km-z]{32,44}|[A-Za-z0-9._-]{1,128})$'
)


def _validate_id(value: str, kind: str = "id") -> dict | None:
    """Return None when valid, else a JSON error dict."""
    if not isinstance(value, str) or not _ID_RE.match(value):
        return {
            "error": (
                f"invalid {kind}: must match ^[a-z0-9][a-z0-9._-]{{0,127}}$, "
                f"got {value!r}"
            )
        }
    return None


def _validate_symbol(value: str, kind: str = "symbol") -> dict | None:
    """Return None when valid, else a JSON error dict (allows mixed case)."""
    if not isinstance(value, str) or not _SYMBOL_RE.match(value):
        return {
            "error": (
                f"invalid {kind}: must match ^[A-Za-z0-9._-]{{0,63}}$, "
                f"got {value!r}"
            )
        }
    return None


def _validate_coins(value: str) -> dict | None:
    """Validate `chain:address[,chain:address...]` segments for `get_token_dex_price`."""
    if not isinstance(value, str) or not value:
        return {"error": f"invalid coins: must be non-empty string, got {value!r}"}
    for seg in value.split(","):
        if not _COINS_SEG.match(seg):
            return {
                "error": (
                    "invalid coins segment: each must be 'chain:address' with "
                    f"chain matching ^[a-z0-9-]{{1,40}}$ and a valid EVM/Solana/"
                    f"DefiLlama address, got {seg!r}"
                )
            }
    return None


def _sort_key(value: Any) -> tuple[int, float]:
    """Sort helper: push None/non-numeric to the end of a desc sort."""
    if value is None:
        return (1, 0.0)
    try:
        return (0, -float(value))
    except (TypeError, ValueError):
        return (1, 0.0)


def _trim_protocol(p: dict) -> dict:
    """Compact a /protocols entry down to fields useful for ranking."""
    return {
        "name": p.get("name"),
        "slug": p.get("slug"),
        "symbol": p.get("symbol"),
        "category": p.get("category"),
        "chain": p.get("chain"),
        "chains": p.get("chains"),
        "tvl": p.get("tvl"),
        "change_1h": p.get("change_1h"),
        "change_1d": p.get("change_1d"),
        "change_7d": p.get("change_7d"),
        "mcap": p.get("mcap"),
        "url": p.get("url"),
    }


def _tail(arr: Any, n: int) -> Any:
    """Return the last n items of arr if arr is a list, else arr unchanged."""
    if isinstance(arr, list) and n > 0:
        return arr[-n:]
    return arr


# ---------- tools ----------


@mcp.tool()
async def list_protocols(
    limit: int = 100,
    sort_by: Literal["tvl", "change_1d", "change_7d", "mcap"] = "tvl",
    chain: str = "",
) -> Any:
    """List DeFi protocols ranked by TVL (or 1d/7d change, or market cap).

    Use this for "what are the biggest DeFi protocols?", "which protocols had
    the largest TVL inflows/outflows today?", or to find a protocol's slug
    before calling `get_protocol_tvl`. Far more granular than CoinGecko's
    `get_global_defi`, which only returns aggregate DeFi market cap.

    Args:
        limit: Number of protocols to return after sorting (1..500).
        sort_by: Metric to rank by, descending. "tvl" = current TVL,
            "change_1d"/"change_7d" = TVL change %, "mcap" = token market cap.
        chain: Optional chain name filter (e.g. "Ethereum", "Solana", "Base",
            "Arbitrum"). Matches against each protocol's `chains` array,
            case-insensitive. Empty string disables the filter.

    Returns:
        Array of protocol summaries with `name`, `slug`, `symbol`, `category`,
        `chain`, `chains`, `tvl`, `change_1h`, `change_1d`, `change_7d`,
        `mcap`, `url`.

    Note: `chain` (when non-empty) is validated against `^[a-z0-9][a-z0-9._-]{0,127}$`.
    """
    if chain:
        err = _validate_id(chain, "chain")
        if err is not None:
            return err

    data = await _http_get(f"{DEFILLAMA_BASE}/protocols")
    if is_error(data):
        return data
    if not isinstance(data, list):
        return {"error": "unexpected response", "type": type(data).__name__}

    if chain:
        needle = chain.lower()
        data = [
            p for p in data
            if any(c and c.lower() == needle for c in (p.get("chains") or []))
        ]

    data = sorted(data, key=lambda p: _sort_key(p.get(sort_by)))
    limit = max(1, min(limit, 500))
    return [_trim_protocol(p) for p in data[:limit]]


@mcp.tool()
async def get_protocol_tvl(slug: str, history_days: int = 90) -> Any:
    """Get a single protocol's metadata, current TVL, and recent TVL history.

    Use after `list_protocols` to drill into one protocol — e.g. "what is
    Aave's TVL on each chain?" or "show me Lido's last 90 days of TVL."
    The raw DefiLlama response is enormous (multi-year daily series for the
    overall protocol AND every chain it touches), so this tool trims each
    history series to the last `history_days` daily points and drops the
    per-token breakdown arrays (`tokens`, `tokensInUsd`).

    Args:
        slug: Protocol slug from `list_protocols` (e.g. "aave-v3", "lido",
            "uniswap-v3"). NOT a CoinGecko coin ID.
        history_days: Number of trailing daily points to keep for each TVL
            series. Default 90; use a smaller value for compactness or a
            larger value (up to ~1500) for long-range analysis.

    Returns:
        Protocol object with `id`, `name`, `symbol`, `category`, `chains`,
        `chain`, `description`, `url`, `mcap`, `currentChainTvls` (snapshot
        per chain), `tvl` (recent series of `{date, totalLiquidityUSD}`), and
        `chainTvls` (per-chain trimmed series).

    Note: `slug` is validated against `^[a-z0-9][a-z0-9._-]{0,127}$`.
    """
    err = _validate_id(slug, "slug")
    if err is not None:
        return err

    data = await _http_get(f"{DEFILLAMA_BASE}/protocol/{slug}")
    if is_error(data):
        return data
    if not isinstance(data, dict):
        return {"error": "unexpected response", "type": type(data).__name__}

    n = max(1, history_days)

    # Trim main TVL history; drop heavy token breakdown arrays.
    out: dict[str, Any] = {
        k: v for k, v in data.items()
        if k not in ("tokens", "tokensInUsd", "chainTvls")
    }
    if isinstance(data.get("tvl"), list):
        out["tvl"] = _tail(data["tvl"], n)

    # Trim per-chain histories the same way.
    chain_tvls = data.get("chainTvls") or {}
    trimmed_chains: dict[str, Any] = {}
    if isinstance(chain_tvls, dict):
        for chain_name, series in chain_tvls.items():
            if not isinstance(series, dict):
                trimmed_chains[chain_name] = series
                continue
            trimmed_chains[chain_name] = {
                "tvl": _tail(series.get("tvl"), n),
            }
    out["chainTvls"] = trimmed_chains
    return out


@mcp.tool()
async def list_chains_tvl(limit: int = 50) -> Any:
    """List blockchain networks ranked by current total DeFi TVL.

    Use for "which chains have the most DeFi activity?", "how does Solana's
    TVL compare to Ethereum's?", or to discover chain names you can pass to
    `get_chain_tvl_history` or `list_protocols(chain=...)`.

    Args:
        limit: Number of chains to return after sorting by TVL desc (1..200).

    Returns:
        Array of chain summaries with `name`, `tvl`, `tokenSymbol`, `chainId`,
        `gecko_id`, `cmcId`.
    """
    data = await _http_get(f"{DEFILLAMA_BASE}/v2/chains")
    if is_error(data):
        return data
    if not isinstance(data, list):
        return {"error": "unexpected response", "type": type(data).__name__}

    data = sorted(data, key=lambda c: _sort_key(c.get("tvl")))
    limit = max(1, min(limit, 200))
    return [
        {
            "name": c.get("name"),
            "tvl": c.get("tvl"),
            "tokenSymbol": c.get("tokenSymbol"),
            "chainId": c.get("chainId"),
            "gecko_id": c.get("gecko_id"),
            "cmcId": c.get("cmcId"),
        }
        for c in data[:limit]
    ]


@mcp.tool()
async def get_chain_tvl_history(chain: str = "", days: int = 90) -> Any:
    """Get historical total DeFi TVL for one chain, or for all of DeFi combined.

    Use to chart "Ethereum TVL over the last year" or "how has total DeFi TVL
    evolved?" Complements CoinGecko's `get_global_defi`, which only gives a
    single current number with no history.

    Args:
        chain: Chain name from `list_chains_tvl` (e.g. "Ethereum", "Solana",
            "Base", "Arbitrum"). Empty string returns total TVL across ALL of
            DeFi (i.e. all chains combined).
        days: Number of trailing daily points to return (1..3650). Default 90.

    Returns:
        Array of `{date, tvl}` points, where `date` is a unix epoch seconds
        timestamp at UTC midnight and `tvl` is the chain's total DeFi TVL in
        USD on that day.

    Note: `chain` (when non-empty) is validated against
    `^[a-z0-9][a-z0-9._-]{0,127}$`.
    """
    if chain:
        err = _validate_id(chain, "chain")
        if err is not None:
            return err
        url = f"{DEFILLAMA_BASE}/v2/historicalChainTvl/{chain}"
    else:
        url = f"{DEFILLAMA_BASE}/v2/historicalChainTvl"
    data = await _http_get(url)
    if is_error(data):
        return data
    if not isinstance(data, list):
        return {"error": "unexpected response", "type": type(data).__name__}
    return _tail(data, max(1, days))


@mcp.tool()
async def list_stablecoins(
    limit: int = 30,
    include_chain_breakdown: bool = True,
) -> Any:
    """List stablecoins ranked by current circulating market cap.

    Use for "what are the biggest stablecoins?", "is USDT or USDC bigger?",
    "where is USDC issued (which chains)?", or to track peg health
    (`price` field shows the current oracle price).

    Args:
        limit: Number of stablecoins to return (1..200).
        include_chain_breakdown: If true, includes a `chainCirculating` map
            of supply per chain. If false, drops it to keep the response
            small (the breakdown for the top stablecoins is verbose).

    Returns:
        Array of stablecoin summaries with `id`, `name`, `symbol`, `pegType`,
        `pegMechanism`, `price`, `circulating`, `circulatingPrevDay`,
        `circulatingPrevWeek`, `circulatingPrevMonth`, `chains`, and
        (optionally) `chainCirculating`. The `id` field is what
        `get_stablecoin_detail` would accept (DefiLlama internal id).
    """
    data = await _http_get(
        f"{DEFILLAMA_STABLECOINS_BASE}/stablecoins",
        params={"includePrices": "true"},
    )
    if is_error(data):
        return data
    if not isinstance(data, dict):
        return {"error": "unexpected response", "type": type(data).__name__}

    pegged = data.get("peggedAssets") or []
    if not isinstance(pegged, list):
        return {"error": "unexpected peggedAssets shape"}

    def _circ_usd(p: dict) -> float:
        c = p.get("circulating") or {}
        if not isinstance(c, dict) or not c:
            return 0.0
        # peggedAssets dicts hold one peg-typed key like "peggedUSD".
        try:
            return float(next(iter(c.values())))
        except (StopIteration, TypeError, ValueError):
            return 0.0

    pegged = sorted(pegged, key=lambda p: -_circ_usd(p))
    limit = max(1, min(limit, 200))

    out = []
    for p in pegged[:limit]:
        item = {
            "id": p.get("id"),
            "name": p.get("name"),
            "symbol": p.get("symbol"),
            "pegType": p.get("pegType"),
            "pegMechanism": p.get("pegMechanism"),
            "price": p.get("price"),
            "circulating": p.get("circulating"),
            "circulatingPrevDay": p.get("circulatingPrevDay"),
            "circulatingPrevWeek": p.get("circulatingPrevWeek"),
            "circulatingPrevMonth": p.get("circulatingPrevMonth"),
            "chains": p.get("chains"),
        }
        if include_chain_breakdown:
            item["chainCirculating"] = p.get("chainCirculating")
        out.append(item)
    return out


@mcp.tool()
async def list_yield_pools(
    min_tvl_usd: float = 1_000_000,
    project: str = "",
    chain: str = "",
    symbol: str = "",
    limit: int = 50,
    sort_by: Literal["apy", "tvlUsd", "apyMean30d"] = "apy",
) -> Any:
    """List DeFi yield-bearing pools (lending, staking, LPs) filtered & ranked by APY or TVL.

    The DefiLlama yields endpoint returns ~20k pools, so this tool aggressively
    filters and slices client-side. Use for "best stablecoin yields right now",
    "highest APY on Aave", "Lido vs Rocket Pool TVL", etc. Pair with
    `list_protocols` if you want protocol-level TVL rather than per-pool APY.

    Args:
        min_tvl_usd: Minimum pool TVL in USD. Default 1M filters out tiny
            pools. Set to 0 to disable.
        project: Optional project filter, e.g. "aave-v3", "lido", "compound-v3".
            Matches the `project` field, case-insensitive.
        chain: Optional chain filter, e.g. "Ethereum", "Solana", "Arbitrum".
            Case-insensitive exact match against `chain`.
        symbol: Optional symbol/token substring filter, e.g. "USDC", "ETH",
            "STETH". Case-insensitive substring match against `symbol`.
        limit: Number of pools to return after filtering & sorting (1..500).
        sort_by: Metric to rank by, descending. "apy" = current APY,
            "tvlUsd" = pool size, "apyMean30d" = 30-day mean APY.

    Returns:
        Array of pool summaries with `pool` (DefiLlama pool id), `chain`,
        `project`, `symbol`, `tvlUsd`, `apy`, `apyBase`, `apyReward`,
        `apyMean30d`, `apyPct1D`, `apyPct7D`, `apyPct30D`, `stablecoin`,
        `ilRisk`, `exposure`, `predictions`, `rewardTokens`, `underlyingTokens`.

    Note: `project`/`chain`/`symbol` (when non-empty) are validated. `project`
    and `chain` use `^[a-z0-9][a-z0-9._-]{0,127}$`; `symbol` allows mixed case
    via `^[A-Za-z0-9._-]{0,63}$`.
    """
    if project:
        err = _validate_id(project, "project")
        if err is not None:
            return err
    if chain:
        err = _validate_id(chain, "chain")
        if err is not None:
            return err
    if symbol:
        err = _validate_symbol(symbol, "symbol")
        if err is not None:
            return err

    data = await _http_get(f"{DEFILLAMA_YIELDS_BASE}/pools")
    if is_error(data):
        return data
    if not isinstance(data, dict):
        return {"error": "unexpected response", "type": type(data).__name__}

    pools = data.get("data") or []
    if not isinstance(pools, list):
        return {"error": "unexpected pools shape"}

    proj = project.lower() if project else ""
    chn = chain.lower() if chain else ""
    sym = symbol.lower() if symbol else ""

    def _ok(p: dict) -> bool:
        if min_tvl_usd and (p.get("tvlUsd") or 0) < min_tvl_usd:
            return False
        if proj and (p.get("project") or "").lower() != proj:
            return False
        if chn and (p.get("chain") or "").lower() != chn:
            return False
        if sym and sym not in (p.get("symbol") or "").lower():
            return False
        return True

    filtered = [p for p in pools if _ok(p)]
    filtered = sorted(filtered, key=lambda p: _sort_key(p.get(sort_by)))
    limit = max(1, min(limit, 500))

    keep = (
        "pool", "chain", "project", "symbol", "tvlUsd",
        "apy", "apyBase", "apyReward", "apyMean30d",
        "apyPct1D", "apyPct7D", "apyPct30D",
        "stablecoin", "ilRisk", "exposure",
        "predictions", "rewardTokens", "underlyingTokens",
    )
    return [{k: p.get(k) for k in keep} for p in filtered[:limit]]


@mcp.tool()
async def list_dex_volumes(limit: int = 30) -> Any:
    """List DEXes ranked by 24-hour trading volume.

    Use for "biggest DEXes by volume", "Uniswap vs PancakeSwap volume", or to
    see momentum (each entry includes 1d/7d/30d change percentages). Pair with
    CCXT's `get_exchange_ticker` for centralized-exchange volumes.

    Args:
        limit: Number of DEXes to return (1..200).

    Returns:
        Object with a `summary` (totals across all DEXes: `total24h`,
        `total7d`, `total30d`, `change_1d`, `change_7d`, `change_1m`) and
        `protocols` — an array of `name`, `displayName`, `slug`, `category`,
        `chains`, `total24h`, `total7d`, `total30d`, `total1y`, `totalAllTime`,
        `change_1d`, `change_7d`, `change_1m`.
    """
    data = await _http_get(
        f"{DEFILLAMA_BASE}/overview/dexs",
        params={
            "excludeTotalDataChart": "true",
            "excludeTotalDataChartBreakdown": "true",
        },
    )
    if is_error(data):
        return data
    if not isinstance(data, dict):
        return {"error": "unexpected response", "type": type(data).__name__}

    protocols = data.get("protocols") or []
    if not isinstance(protocols, list):
        return {"error": "unexpected protocols shape"}

    protocols = sorted(protocols, key=lambda p: _sort_key(p.get("total24h")))
    limit = max(1, min(limit, 200))

    keep = (
        "name", "displayName", "slug", "category", "chains",
        "total24h", "total7d", "total30d", "total1y", "totalAllTime",
        "change_1d", "change_7d", "change_1m",
    )
    return {
        "summary": {
            "total24h": data.get("total24h"),
            "total7d": data.get("total7d"),
            "total30d": data.get("total30d"),
            "change_1d": data.get("change_1d"),
            "change_7d": data.get("change_7d"),
            "change_1m": data.get("change_1m"),
        },
        "protocols": [{k: p.get(k) for k in keep} for p in protocols[:limit]],
    }


@mcp.tool()
async def list_fees_revenue(
    limit: int = 30,
    data_type: Literal[
        "dailyFees", "dailyRevenue", "totalFees", "totalRevenue"
    ] = "dailyFees",
) -> Any:
    """List protocols ranked by fees or revenue (daily or all-time).

    Useful for "which protocols make the most money?", "Tron vs Ethereum
    fees", or "Aave's revenue this month". DefiLlama distinguishes fees
    (gross paid by users) from revenue (kept by the protocol/token holders),
    and offers daily and all-time aggregations — pick via `data_type`.

    Args:
        limit: Number of protocols to return (1..200).
        data_type: Which metric series to pull. "dailyFees" = fees on a daily
            basis (typical for "what's hot today?"), "dailyRevenue" = revenue
            on a daily basis, "totalFees"/"totalRevenue" = cumulative all-time.
            Note: regardless of data_type, each protocol's per-window fields
            are still named `total24h`, `total7d`, `total30d`, etc., but they
            now refer to the chosen metric.

    Returns:
        Object with a `summary` (totals across all protocols: `total24h`,
        `total7d`, `total30d`, `change_1d`, `change_7d`, `change_1m`) and
        `protocols` — an array of `name`, `displayName`, `slug`, `category`,
        `chains`, `total24h`, `total7d`, `total30d`, `total1y`, `totalAllTime`,
        `change_7dover7d`, `change_30dover30d`.
    """
    data = await _http_get(
        f"{DEFILLAMA_BASE}/overview/fees",
        params={
            "excludeTotalDataChart": "true",
            "excludeTotalDataChartBreakdown": "true",
            "dataType": data_type,
        },
    )
    if is_error(data):
        return data
    if not isinstance(data, dict):
        return {"error": "unexpected response", "type": type(data).__name__}

    protocols = data.get("protocols") or []
    if not isinstance(protocols, list):
        return {"error": "unexpected protocols shape"}

    protocols = sorted(protocols, key=lambda p: _sort_key(p.get("total24h")))
    limit = max(1, min(limit, 200))

    keep = (
        "name", "displayName", "slug", "category", "chains",
        "total24h", "total7d", "total30d", "total1y", "totalAllTime",
        "change_7dover7d", "change_30dover30d",
    )
    return {
        "data_type": data_type,
        "summary": {
            "total24h": data.get("total24h"),
            "total7d": data.get("total7d"),
            "total30d": data.get("total30d"),
            "change_1d": data.get("change_1d"),
            "change_7d": data.get("change_7d"),
            "change_1m": data.get("change_1m"),
        },
        "protocols": [{k: p.get(k) for k in keep} for p in protocols[:limit]],
    }


@mcp.tool()
async def get_token_dex_price(coins: str) -> Any:
    """Get DefiLlama oracle spot prices for tokens identified by `chain:address`.

    Use when you have a token's contract address and want a price without
    going through CoinGecko/DexScreener — DefiLlama aggregates DEX prices
    across many sources. For human-friendly token discovery (search by
    symbol/name), use `dex_search` (DexScreener) or CoinGecko's `search`.

    Args:
        coins: Comma-separated `chain:address` identifiers, e.g.
            "ethereum:0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48,bsc:0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c".
            Chain identifiers follow DefiLlama's naming (e.g. "ethereum",
            "bsc", "polygon", "arbitrum", "base", "solana"). Solana uses
            "solana:<mint-address>".

    Returns:
        Object with a `coins` map keyed by `chain:address`, where each value
        has `decimals`, `price`, `symbol`, `timestamp`, `confidence`.

    Note: Each `chain:address` segment of `coins` is validated. Chain matches
    `^[a-z0-9-]{1,40}$` and address is EVM hex, Solana base58, or DefiLlama
    token id.
    """
    err = _validate_coins(coins)
    if err is not None:
        return err
    return await _http_get(f"{DEFILLAMA_COINS_BASE}/prices/current/{coins}")
