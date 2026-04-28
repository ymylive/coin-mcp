"""HTTP envelope semantics + cache behaviour for error responses."""
from __future__ import annotations

import pytest

from coin_mcp import cache
from coin_mcp.core import is_error


def test_is_error_truthy_only():
    """is_error() must only consider truthy `error` keys as actual errors."""
    assert is_error({"error": "x"}) is True
    assert is_error({"error": None, "data": 1}) is False
    assert is_error({}) is False
    assert is_error([1, 2, 3]) is False
    # A coin response that legitimately has `error: null` upstream should pass.
    assert is_error({"error": None}) is False


class _FakeResponse:
    def __init__(self, status_code: int, body: str = "boom", url: str = "https://x/y"):
        self.status_code = status_code
        self.text = body

        class _Req:
            def __init__(self, u):
                self.url = u

        self.request = _Req(url)

    def json(self):  # pragma: no cover - error path doesn't call json()
        raise ValueError("not json")


class _FakeClient:
    """Stub AsyncClient — counts .get() calls and returns canned 500s."""

    def __init__(self):
        self.is_closed = False
        self.calls = 0

    async def get(self, url, params=None, headers=None):
        self.calls += 1
        return _FakeResponse(500, body="server exploded", url=url)


async def test_cached_http_get_does_not_cache_errors(monkeypatch):
    """Two identical requests that yield 500 must each invoke the client.

    Caching errors would lock-in a transient failure for the full TTL.
    """
    fake = _FakeClient()

    async def _fake_get_client():
        return fake

    monkeypatch.setattr(cache, "_get_client", _fake_get_client)

    stats_before = cache.get_stats()
    misses_before = stats_before["misses"]

    url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin"
    r1 = await cache.cached_http_get(url)
    r2 = await cache.cached_http_get(url)

    assert is_error(r1) and is_error(r2), f"expected error envelopes; got {r1!r} / {r2!r}"
    assert fake.calls == 2, (
        f"error responses must NOT be cached — fake client should have been "
        f"called twice, was called {fake.calls} times"
    )

    stats_after = cache.get_stats()
    assert stats_after["misses"] - misses_before == 2, (
        f"expected miss counter to grow by 2 on two un-cached error fetches; "
        f"before={misses_before}, after={stats_after['misses']}"
    )
