"""Per-module input validators reject path-injection / malformed identifiers."""
from __future__ import annotations

import pytest

from coin_mcp import coingecko, dexscreener
from coin_mcp.core import is_error


async def test_coingecko_path_injection_rejected():
    """Path traversal and query-smuggle attempts on coin_id must short-circuit synchronously."""
    bad_traversal = await coingecko.get_coin_details("../../../etc/passwd")
    bad_query = await coingecko.get_market_chart("aave?secret=x")

    assert is_error(bad_traversal), f"expected error envelope; got {bad_traversal!r}"
    assert is_error(bad_query), f"expected error envelope; got {bad_query!r}"

    msg1 = bad_traversal["error"].lower()
    msg2 = bad_query["error"].lower()
    assert "invalid coin_id" in msg1, msg1
    assert "invalid coin_id" in msg2, msg2


async def test_dexscreener_address_validation():
    """Bad pair-address / token-address must be rejected; a real EVM address must pass the validator."""
    bad_chain = await dexscreener.get_dex_pair("eth/admin", "0xdeadbeef")
    bad_addr = await dexscreener.get_dex_token_pairs("not-a-real-address")

    assert is_error(bad_chain), f"expected error envelope; got {bad_chain!r}"
    assert is_error(bad_addr), f"expected error envelope; got {bad_addr!r}"

    # Real USDC-on-Ethereum address — validator must NOT reject it. The actual
    # network call may succeed or fail (e.g. offline CI), but the failure mode
    # must NOT be the synchronous validator-error envelope ("invalid token_address").
    real_addr = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
    out = await dexscreener.get_dex_token_pairs(real_addr, limit=1, chain="ethereum")
    if is_error(out):
        # Network failure / DNS / etc are fine; only assert the validator
        # didn't fire.
        assert "invalid token_address" not in out["error"].lower(), (
            f"validator wrongly rejected a real EVM address: {out!r}"
        )
