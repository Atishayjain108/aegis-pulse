"""
Agent tools.

Each tool is a small, async, side-effect-bearing capability that
agents can invoke. Tools are deliberately simple — they wrap a single
external resource (a SQL query, an HTTP fetch, a Monte Carlo sim) and
return a structured result.

A tool MUST:
  * Be idempotent OR clearly document that it is not.
  * Have a timeout on every external call.
  * Handle its own exceptions and return a typed error rather than
    raising into the agent (agents must NOT need try/except around
    tools).
  * Be unit-testable by patching only its dependencies.
"""

from .base import ToolError, ToolResult, tool_call

__all__ = ["ToolError", "ToolResult", "tool_call"]
