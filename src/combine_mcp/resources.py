"""MCP resources exposing the docs-mcp source registry to the LLM."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP  # noqa: TC002

from combine_mcp.config import DocSource


def _format_sources(sources: dict[str, DocSource]) -> str:
    """Render the sources registry as a Markdown reference page.

    The agent reads this to discover which ``source`` IDs are valid for
    the ``search_docs`` and ``fetch_doc`` tools, and what corpus each
    one covers. Designed as a Resource Reference (arcade.dev pattern):
    short, no body content from the docs themselves, just pointers.
    """
    if not sources:
        return "# Documentation Sources\n\n(no sources registered)\n"

    lines = [
        "# Documentation Sources",
        "",
        "Each entry is a `source` ID accepted by `search_docs` and "
        "`fetch_doc`. Sources are independent corpora; a single tool "
        "call queries exactly one.",
        "",
    ]
    for src in sorted(sources.values(), key=lambda s: s.id):
        lines.extend([
            f"## `{src.id}` - {src.name}",
            f"- Site: {src.docs_site_url}",
            f"- Repo: {src.repo_url}",
            "",
        ])
    return "\n".join(lines)


def register(mcp: FastMCP, sources: dict[str, DocSource]) -> None:
    """Register documentation resources with the MCP server.

    Args:
        mcp: The FastMCP instance to register the resource on.
        sources: The source registry loaded by the server. The list is
            captured by closure at registration time, so it is stable
            for the lifetime of the server.
    """
    body = _format_sources(sources)

    @mcp.resource(
        "docs://sources",
        name="Documentation Sources",
        description=(
            "Registry of documentation sources this MCP can query. "
            "Lists each source ID, its rendered docs site URL, and "
            "its source repository. Read this when you need to choose "
            "the `source` argument for search_docs or fetch_doc."
        ),
        mime_type="text/markdown",
    )
    def list_sources() -> str:
        return body
