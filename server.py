"""coin-mcp entrypoint.

Imports every tool/prompt/resource module so their decorators register on the
shared FastMCP instance defined in coin_mcp.core, then runs the server with
the CLI-selected transport.

Run:
    python server.py                                  # stdio (default)
    python server.py --transport sse --port 8000
    python server.py --transport streamable-http --port 8000
"""
from __future__ import annotations

from coin_mcp.core import mcp

# Importing each module triggers @mcp.tool() / @mcp.prompt() / @mcp.resource()
# decorators and registers them on the shared `mcp` instance.
from coin_mcp import coingecko  # noqa: F401
from coin_mcp import ccxt_tools  # noqa: F401
from coin_mcp import sentiment  # noqa: F401
from coin_mcp import indicators  # noqa: F401
from coin_mcp import defillama  # noqa: F401
from coin_mcp import dexscreener  # noqa: F401
from coin_mcp import derivatives  # noqa: F401
from coin_mcp import aggregate  # noqa: F401
from coin_mcp import prompts  # noqa: F401
from coin_mcp import resources  # noqa: F401

from coin_mcp.transport import run_with_cli


def main() -> None:
    run_with_cli(mcp)


if __name__ == "__main__":
    main()
