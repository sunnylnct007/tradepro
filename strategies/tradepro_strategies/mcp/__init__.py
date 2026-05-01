"""tradepro-mcp — Model Context Protocol server.

Exposes the platform as MCP resources + tools + prompts so any
MCP-aware client (Claude Desktop, Cursor, our own /chat page) can
ask questions of your portfolio data with full citation tracking.

Strict accuracy contract — three layers:
  1. Tool outputs carry `_source` paths (e.g.
     `tradepro://compare/etf_us_core/rows[3]/stats/sharpe`) so every
     number is traceable back to a specific JSON field.
  2. Decomposition prompts force the LLM to plan + call tools BEFORE
     answering. No tool calls = no answer.
  3. `verify_answer` tool cross-checks a draft answer against the
     tool outputs and flags any unsupported numerical claims — the
     accuracy guarantee for financial decisions.
"""
from .server import build_server

__all__ = ["build_server"]
