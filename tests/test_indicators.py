"""Tests for `compute_indicators` — math correctness and input-bounds safety."""
from __future__ import annotations

import pytest

from coin_mcp.core import is_error
from coin_mcp.indicators import MAX_OHLCV_ROWS, compute_indicators


# Wilder's classic 14-close test vector. The textbook reference RSI is ~70.46.
WILDER_REFERENCE_CLOSES = [
    44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10,
    45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28,
]


async def test_rsi_textbook_reference():
    ohlcv = [[i * 1000, c, c, c, c, 1000] for i, c in enumerate(WILDER_REFERENCE_CLOSES)]
    out = await compute_indicators(ohlcv=ohlcv, indicators=["rsi"])
    assert not is_error(out), f"unexpected error: {out}"
    latest = out["rsi"]["latest"]
    assert latest is not None, f"RSI latest is None; out={out}"
    assert 69.0 <= latest <= 72.0, (
        f"RSI {latest} outside textbook reference range ~70.46 (69-72)"
    )


async def test_5col_input_obv_handled():
    """5-column input (no volume) must not crash; OBV is reported as null with note."""
    rows = [[i * 1000, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i] for i in range(30)]
    out = await compute_indicators(ohlcv=rows, indicators=["rsi", "obv"])
    assert not is_error(out)
    # OBV must either be absent, null, or carry a note explaining the missing column.
    obv = out.get("obv")
    if obv is not None:
        assert obv.get("latest") is None, f"expected obv.latest=None for 5-col input; got {obv}"
        # Implementation includes an explanatory note; tolerate other equivalent shapes.
        assert "note" in obv or obv.get("series") is None


async def test_max_rows_cap():
    """Inputs over MAX_OHLCV_ROWS must be rejected with an error envelope."""
    rows = [[i, 1.0, 1.0, 1.0, 1.0, 1.0] for i in range(100_000)]
    out = await compute_indicators(ohlcv=rows)
    assert is_error(out), f"expected error envelope for 100k rows; got {out}"
    msg = out.get("error", "")
    assert "5000" in msg or out.get("max_rows") == MAX_OHLCV_ROWS, (
        f"error message should mention the 5000-row cap; got {out!r}"
    )


async def test_empty_input_error():
    out = await compute_indicators(ohlcv=[])
    assert is_error(out), f"expected error envelope for empty input; got {out}"
