"""coin-mcp transport CLI.

Adds multi-transport support so the FastMCP server can be launched over
stdio (default, for local MCP clients), SSE, or streamable-HTTP (hosted /
Cursor compatibility) from the same entrypoint.

Network transports bind to 127.0.0.1 by default. Binding to a non-loopback
host (i.e. publicly reachable) is REFUSED unless `--allow-public` is given,
because every registered tool runs unauthenticated — anyone who can reach
the port can drive every tool. With `--allow-public` we still print a
prominent warning so the operator is aware they need their own auth layer
(reverse proxy, firewall, ...) in front.
"""
from __future__ import annotations

import argparse
import sys

_SUPPORTED = ("stdio", "sse", "streamable-http")
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def run_with_cli(mcp, argv: list[str] | None = None) -> None:
    """Parse CLI args and run `mcp` on the chosen transport."""
    parser = argparse.ArgumentParser(prog="coin-mcp")
    parser.add_argument("--transport", choices=_SUPPORTED, default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--mount-path", default="/mcp")
    parser.add_argument(
        "--allow-public",
        action="store_true",
        help=(
            "Required to bind a network transport to a non-loopback host. "
            "Every tool is unauthenticated — make sure you have a reverse "
            "proxy with auth, a firewall, or another access control layer "
            "in front of this port."
        ),
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    if args.transport == "stdio":
        print("[coin-mcp] starting on transport=stdio", file=sys.stderr, flush=True)
        try:
            mcp.run(transport="stdio")
        except ValueError as e:
            _bail(e)
        return

    # Network transport: enforce loopback-only by default.
    is_loopback = args.host in _LOOPBACK_HOSTS
    if not is_loopback and not args.allow_public:
        print(
            f"[coin-mcp] refusing to bind to non-loopback host {args.host!r}: "
            "every tool is unauthenticated and would be exposed to anyone who "
            "can reach this port. Re-run with --allow-public to confirm you "
            "have an external auth layer (reverse proxy / firewall) in front, "
            f"or bind to one of {sorted(_LOOPBACK_HOSTS)}.",
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(2)
    if not is_loopback and args.allow_public:
        print(
            f"[coin-mcp] WARNING: binding to {args.host}:{args.port} with NO "
            "authentication. Anyone who can reach this port can drive every "
            "tool. Use a reverse proxy with auth in front, or restrict via "
            "firewall.",
            file=sys.stderr,
            flush=True,
        )

    # Configure host/port/mount on FastMCP settings.
    mcp.settings.host = args.host
    mcp.settings.port = args.port
    mcp.settings.mount_path = args.mount_path
    if args.transport == "streamable-http":
        mcp.settings.streamable_http_path = args.mount_path

    print(
        f"[coin-mcp] starting on transport={args.transport} "
        f"host={args.host} port={args.port}",
        file=sys.stderr,
        flush=True,
    )
    try:
        if args.transport == "sse":
            mcp.run(transport="sse", mount_path=args.mount_path)
        else:
            mcp.run(transport="streamable-http")
    except ValueError as e:
        _bail(e)


def _bail(e: Exception) -> None:
    print(
        f"[coin-mcp] transport error: {e}. "
        f"Supported transports: {', '.join(_SUPPORTED)}.",
        file=sys.stderr,
        flush=True,
    )
    raise SystemExit(2)
