"""Shared helpers for atlas-software-docs-mcp tool implementations."""

from __future__ import annotations


def format_error(exc: Exception, *, recovery: list[str] | None = None) -> str:
    """Format an error with recovery guidance for the LLM.

    Follows the arcade.dev Recovery Guide pattern: errors should teach,
    not just fail. Each message includes what went wrong plus concrete,
    actionable steps the agent can take to recover (typically the next
    tool call to make).

    Args:
        exc: The caught exception.
        recovery: Suggested next actions the agent can take.

    Returns:
        A structured error string with actionable guidance.
    """
    parts = [f"Error: {exc}"]
    if recovery:
        parts.append("Recovery steps:")
        parts.extend(f"- {step}" for step in recovery)
    return "\n".join(parts)
