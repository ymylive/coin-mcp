"""Verify that `cache._classify` returns the correct TTL per URL pattern.

The label is an internal naming choice we don't pin; only the TTL matters
for behavior.
"""
from __future__ import annotations

import pytest

from coin_mcp import cache


# (url, expected_label_substring, expected_ttl)
ROUTES: list[tuple[str, str, float]] = [
    ("https://api.coingecko.com/api/v3/simple/price?ids=btc", "simple/price", 10.0),
    ("https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?days=7", "market_chart", 60.0),
    ("https://api.coingecko.com/api/v3/coins/bitcoin/ohlc?days=30", "ohlc", 60.0),
    ("https://api.coingecko.com/api/v3/coins/bitcoin/tickers", "tickers", 30.0),
    ("https://api.coingecko.com/api/v3/search/trending", "trending", 60.0),
    ("https://api.coingecko.com/api/v3/search?query=eth", "search", 300.0),
    ("https://api.coingecko.com/api/v3/global", "global", 60.0),
    ("https://api.coingecko.com/api/v3/coins/categories", "categories", 300.0),
    ("https://api.coingecko.com/api/v3/exchanges/binance", "exchanges", 600.0),
    ("https://api.coingecko.com/api/v3/exchanges?per_page=100", "exchanges", 1800.0),
    ("https://api.coingecko.com/api/v3/derivatives/exchanges", "derivatives", 600.0),
    ("https://api.coingecko.com/api/v3/nfts/list", "nfts", 1800.0),
    ("https://api.coingecko.com/api/v3/nfts/cryptopunks", "nfts", 300.0),
    ("https://api.coingecko.com/api/v3/companies/public_treasury/bitcoin", "companies", 1800.0),
    ("https://api.coingecko.com/api/v3/coins/top_gainers_losers", "top_gainers_losers", 60.0),
    ("https://api.coingecko.com/api/v3/coins/bitcoin", "coins", 120.0),
    ("https://api.alternative.me/fng/?limit=1", "alternative.me", 600.0),
]


@pytest.mark.parametrize("url,label_hint,expected_ttl", ROUTES)
def test_classify_routing_table(url: str, label_hint: str, expected_ttl: float):
    label, ttl = cache._classify(url)
    assert ttl == expected_ttl, (
        f"TTL mismatch for {url}: got ttl={ttl} (label={label!r}), expected {expected_ttl}"
    )


def test_classify_no_shadowing():
    """Specific CoinGecko `/coins/...` endpoints must NOT be shadowed by `/coins/{id}`.

    Regression test for the substring-shadowing bug where the broad
    `/api/v3/coins/` prefix rule absorbs the more specific
    `/coins/markets`, `/coins/categories`, and `/coins/top_gainers_losers`
    rules. The fix is rule ordering (specific before general) — these four
    endpoints must each resolve to their own TTL, NOT to `/coins/{id}`'s 120s.
    """
    _, ttl_markets = cache._classify("https://api.coingecko.com/api/v3/coins/markets")
    _, ttl_categories = cache._classify("https://api.coingecko.com/api/v3/coins/categories")
    _, ttl_gainers = cache._classify(
        "https://api.coingecko.com/api/v3/coins/top_gainers_losers"
    )
    _, ttl_coin_id = cache._classify("https://api.coingecko.com/api/v3/coins/bitcoin")

    # `/coins/{id}` is the bare-coin route at TTL 120; the others must each
    # have their own distinct TTL (i.e. NOT 120, which would mean shadowing).
    assert ttl_coin_id == 120.0, f"baseline /coins/{{id}} TTL changed: {ttl_coin_id}"
    assert ttl_markets != ttl_coin_id, (
        f"/coins/markets TTL ({ttl_markets}) shadowed by /coins/{{id}} ({ttl_coin_id})"
    )
    assert ttl_categories != ttl_coin_id, (
        f"/coins/categories TTL ({ttl_categories}) shadowed by /coins/{{id}} ({ttl_coin_id})"
    )
    assert ttl_gainers != ttl_coin_id, (
        f"/coins/top_gainers_losers TTL ({ttl_gainers}) shadowed by /coins/{{id}} ({ttl_coin_id})"
    )
