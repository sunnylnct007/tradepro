"""CLI entry for the REMOTE tradepro MCP server (streamable HTTP).

This is the transport remote agents use — claude.ai in the browser,
another Claude Code session, anything that speaks MCP over HTTP. The
stdio entry (`tradepro-mcp`) is unchanged and still serves the FULL
tool surface to Claude Desktop on the Mac.

What this one serves is deliberately smaller. build_server(read_only=True)
strips every tool that moves a position, changes what the desk trades,
or writes a shared store, and aborts startup if one survives. See
MUTATING_TOOLS in tradepro_strategies/mcp/server.py.

Runs as the `mcp` service in docker-compose.aws.yaml, reaching the .NET
API over the docker network as api:5080. It binds plain HTTP on 0.0.0.0
and is NOT internet-facing on its own: Caddy terminates TLS and only
routes requests that carry the secret path prefix (TRADEPRO_MCP_PATH_TOKEN).

Access control is the unguessable path. claude.ai's connector UI takes
a URL and nothing else — no header, no API key field — so a shared
secret has to ride in the URL or not at all. With TRADEPRO_MCP_PATH_TOKEN
set, the server answers ONLY on /mcp/<token> and 404s everywhere else,
including bare /mcp. The token never appears in this repo (it is public);
it comes from /opt/tradepro/.env on the box, written from a GH secret.

This is a bearer secret in a URL, with the weaknesses that implies —
it lands in browser history and any proxy log in the path. That is an
accepted trade for a surface that cannot place an order; do not widen
the surface without replacing this with real auth.

Environment:
    TRADEPRO_MCP_HOST        bind address          (default 0.0.0.0)
    TRADEPRO_MCP_PORT        bind port             (default 5085)
    TRADEPRO_MCP_PATH        base endpoint path    (default /mcp)
    TRADEPRO_MCP_PATH_TOKEN  secret path segment   (no default)
    TRADEPRO_MCP_STATELESS   1/0, fresh transport per request (default 1)
    TRADEPRO_API_URL         the .NET API          (default http://api:5080)
"""
from __future__ import annotations

import logging
import os
import sys

from ..mcp.server import build_server

log = logging.getLogger("tradepro.mcp.http")


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("TRADEPRO_MCP_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )

    host = os.environ.get("TRADEPRO_MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("TRADEPRO_MCP_PORT", "5085"))
    base_path = os.environ.get("TRADEPRO_MCP_PATH", "/mcp").rstrip("/") or "/mcp"

    token = os.environ.get("TRADEPRO_MCP_PATH_TOKEN", "").strip()
    if token:
        path = f"{base_path}/{token}"
        shown = f"{base_path}/{token[:4]}...{token[-4:]}" if len(token) >= 12 else f"{base_path}/****"
    else:
        path = base_path
        shown = base_path
        # Loud, not fatal: this is the correct config behind a private
        # network, and a silent default would be worse than a warning.
        log.warning(
            "TRADEPRO_MCP_PATH_TOKEN is unset — serving %s with NO access "
            "control. Anyone who can route to this port gets the full "
            "read-only surface (positions, P&L, the book).", base_path,
        )

    # Default the API to the compose service name. Without this the
    # tools module falls back to http://localhost:5080, which inside
    # this container is nothing at all — every tool would return a
    # connection error that reads like the API being down.
    os.environ.setdefault("TRADEPRO_API_URL", "http://api:5080")

    # read_only=True raises if a mutating tool survives the strip, so a
    # bad build fails here rather than serving a surface that can trade.
    server = build_server(
        read_only=True,
        host=host,
        port=port,
        streamable_http_path=path,
        stateless_http=_env_flag("TRADEPRO_MCP_STATELESS", True),
    )

    tools = sorted(tool.name for tool in server._tool_manager.list_tools())
    log.info(
        "tradepro MCP (read-only) on http://%s:%s%s — %d tools, API=%s",
        host, port, shown, len(tools), os.environ["TRADEPRO_API_URL"],
    )
    log.info("tools: %s", ", ".join(tools))

    server.run(transport="streamable-http")


if __name__ == "__main__":
    main()
