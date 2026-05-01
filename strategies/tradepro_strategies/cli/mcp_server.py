"""CLI entry for the tradepro MCP server.

Default transport is stdio — what Claude Desktop expects. Launch it
from the Claude Desktop config:

    {
      "mcpServers": {
        "tradepro": {
          "command": "uv",
          "args": ["run", "--project",
                   "/path/to/tradepro/strategies", "tradepro-mcp"],
          "env": {
            "TRADEPRO_API_URL": "http://localhost:5080"
          }
        }
      }
    }
"""
from __future__ import annotations

from ..mcp.server import build_server


def main() -> None:
    server = build_server()
    server.run()


if __name__ == "__main__":
    main()
