"""Sentiment indicators — Crypto Fear & Greed Index from Alternative.me."""
from __future__ import annotations

from typing import Any

from .core import ALTERNATIVE_BASE, USER_AGENT, _http_get, mcp


@mcp.tool()
async def get_fear_greed_index(limit: int = 1) -> Any:
    """Get the Crypto Fear & Greed Index (0=extreme fear, 100=extreme greed).

    A widely-quoted contrarian sentiment indicator that combines volatility,
    momentum, social media, surveys, BTC dominance and trend volume into a
    single 0-100 score. Useful for "are people fearful or greedy right now?"
    questions.

    Args:
        limit: How many days of history to return (default 1 = today only,
            0 = all history).

    Returns:
        Object with `name`, `data` (array of `{ value, value_classification,
        timestamp, time_until_update }`), and `metadata`. Higher = greedier.
    """
    return await _http_get(
        f"{ALTERNATIVE_BASE}/fng/",
        params={"limit": str(max(0, limit)), "format": "json"},
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
