"""Shared pytest fixtures for the coin-mcp test suite.

With `asyncio_mode = "auto"` configured in pyproject.toml, async test
functions don't need a `@pytest.mark.asyncio` decorator.
"""
from __future__ import annotations

import pytest

from coin_mcp import cache as _cache


@pytest.fixture(autouse=True)
def clear_cache_each_test():
    """Drop all cache entries before every test so state can't leak between tests."""
    _cache.clear()
    yield
    _cache.clear()


@pytest.fixture
def mcp_server():
    """Import server.py (which wires every tool/prompt module) and return the FastMCP instance."""
    import server  # noqa: F401  -- side effects register tools
    return server.mcp
