"""Tool-registry / instructions / prompts coherence checks."""
from __future__ import annotations

import re
from pathlib import Path

import pytest


# Snapshot of expected tool counts per source module. If the polish wave adds
# a tool (e.g. derivatives + aggregate), this fails loudly so the integrating
# agent can update the number deliberately.
EXPECTED_TOOL_COUNTS = {
    "coingecko.py": 18,
    "ccxt_tools.py": 7,
    "sentiment.py": 1,
    "indicators.py": 1,
    "defillama.py": 9,
    "dexscreener.py": 5,
    "cache.py": 2,
    "derivatives.py": 3,
    "aggregate.py": 3,
}


def _count_decorations(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    # `@mcp.tool(...)` with optional whitespace; tolerate `@mcp.tool` if ever used.
    return len(re.findall(r"@mcp\.tool\s*\(", text))


async def test_total_tool_count_matches_modules(mcp_server):
    """Sum of @mcp.tool() decorations per module file equals len(list_tools())."""
    pkg_dir = Path(__file__).resolve().parent.parent / "coin_mcp"
    actual_per_module = {
        name: _count_decorations(pkg_dir / name) for name in EXPECTED_TOOL_COUNTS
    }
    assert actual_per_module == EXPECTED_TOOL_COUNTS, (
        "Per-module tool counts drifted from snapshot — update EXPECTED_TOOL_COUNTS "
        f"in this test if the change is intentional. Got: {actual_per_module}"
    )

    tools = await mcp_server.list_tools()
    assert len(tools) == sum(EXPECTED_TOOL_COUNTS.values()), (
        f"Registered tools ({len(tools)}) != sum of per-module decorations "
        f"({sum(EXPECTED_TOOL_COUNTS.values())}). A tool may be defined but not "
        "imported by server.py."
    )


async def test_instructions_lists_every_registered_tool(mcp_server):
    """Every registered tool name must appear (case-sensitive) in mcp.instructions.

    Critical for AI discoverability — the LLM reads `instructions` to learn
    which tool to pick. A tool that exists but isn't named in instructions
    is effectively invisible.
    """
    instructions = mcp_server.instructions or ""
    tools = await mcp_server.list_tools()
    missing = [t.name for t in tools if t.name not in instructions]
    assert not missing, (
        f"Tools registered but absent from mcp.instructions: {missing}"
    )


async def test_prompts_only_reference_real_tools(mcp_server):
    """Prompt text must not invoke non-existent tool names or parameters.

    Specifically:
      - `get_dex_token` (without `_pairs`) must NOT appear (historic typo bug).
      - `min_apy` and `asset_type` must NOT appear as kwargs to list_yield_pools
        (those parameter names don't exist on that tool).
    """
    prompts_src = (
        Path(__file__).resolve().parent.parent / "coin_mcp" / "prompts.py"
    ).read_text(encoding="utf-8")

    tools = await mcp_server.list_tools()
    tool_names = {t.name for t in tools}

    # Historic bug regression checks — these strings must not appear as
    # function-call-looking tokens in the prompts source.
    assert "get_dex_token(" not in prompts_src, (
        "Prompts must not reference `get_dex_token(`; the real tool is "
        "`get_dex_token_pairs`."
    )
    assert "min_apy" not in prompts_src, (
        "Prompts must not reference parameter `min_apy` — `list_yield_pools` "
        "uses `apy_threshold_pct` (filtered client-side) and does not accept it."
    )
    assert "asset_type" not in prompts_src, (
        "Prompts must not reference parameter `asset_type` — it does not exist "
        "on `list_yield_pools`."
    )

    # Generic check: every snake_case identifier followed by `(` that LOOKS
    # like a tool name should actually be a real tool. Filter out obvious
    # python builtins/kwargs and the macro patterns we know are non-tools.
    candidate_re = re.compile(r"`([a-z][a-z0-9_]+)\(")
    candidates = set(candidate_re.findall(prompts_src))
    # We only care about identifiers that LOOK like tool names — i.e. include
    # an underscore (rules out `print`, `len`, etc.) and aren't python builtins.
    BUILTINS = {"split", "format", "join", "lower", "upper", "strip"}
    candidates = {c for c in candidates if "_" in c and c not in BUILTINS}

    unknown = candidates - tool_names
    assert not unknown, (
        f"Prompt source references identifiers that look like tools but aren't "
        f"registered: {sorted(unknown)}"
    )
