"""``search_docs`` - keyword search across registered documentation sources.

Wraps :class:`combine_mcp.tools._index.DocsIndex` (BM25 over
published MkDocs search payloads). Returns token-efficient summaries
(arcade.dev Response Shaper / Token-Efficient Response): titles, URLs,
snippets only - no body. The agent retrieves bodies via ``fetch_doc``.
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import Context, FastMCP  # noqa: TC002

from combine_mcp.config import (
    format_sources_guide,
    validate_source_id,
)
from combine_mcp.tools._helpers import format_error

_MAX_LIMIT = 25


def register(mcp: FastMCP) -> None:
    """Register the search tool."""

    @mcp.tool()
    async def search_docs(
        query: str,
        source: str = "combine-docs",
        limit: int = 10,
        *,
        ctx: Context[Any, Any],
    ) -> str:
        """Keyword search across a documentation source.

        Returns ``{title, url, path, section, score, snippet}`` per hit.

        After a hit, call ``fetch_doc(url_or_path, source)`` to retrieve
        the Markdown body (full, outline, or one named section).

        Args:
            query: Free-text query. Word-token matched (case-insensitive)
                and ranked by BM25. Multi-token queries are AND-biased
                via BM25 scoring, not strict AND.
            source: Documentation source ID. Default: ``combine-docs``.
            limit: Max hits returned (1-25, default 10). Smaller is more
                token-efficient.
        """
        limit = max(1, min(int(limit), _MAX_LIMIT))
        source_norm = source.strip().lower() if source else "combine-docs"

        ctxd = ctx.request_context.lifespan_context
        http = ctxd["http"]
        indices = ctxd["indices"]
        sources_registry = ctxd["sources"]

        try:
            validate_source_id(source_norm, sources_registry)
        except ValueError as e:
            return format_error(e, recovery=[
                format_sources_guide(sources_registry),
            ])

        index = indices.get(source_norm)
        if not index:
            return format_error(
                ValueError(f"Index not found for source: {source_norm!r}"),
                recovery=[
                    "This is an internal error. Please report it.",
                ],
            )

        source_obj = sources_registry[source_norm]
        try:
            await index.ensure_fresh(http)
        except Exception as exc:  # noqa: BLE001
            return format_error(exc, recovery=[
                f"The MkDocs search index for '{source_norm}' could not be loaded. "
                "Try again shortly.",
                f"Verify the docs site is up: {source_obj.docs_site_url}",
            ])

        results = index.search(query, limit=limit, section=None)
        return json.dumps(
            {
                "query": query,
                "source": source_norm,
                "limit": limit,
                "returned": len(results),
                "results": results,
                "hint": (
                    "No matches - try broader / fewer terms."
                    if not results
                    else None
                ),
                "next_action": (
                    "Call fetch_doc(url_or_path, source) on a result to retrieve "
                    "the Markdown body."
                    if results
                    else None
                ),
            },
            default=str,
        )
