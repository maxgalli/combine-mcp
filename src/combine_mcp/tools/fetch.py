"""``fetch_doc`` - retrieve one page as Markdown from a documentation source.

Hits ``raw.githubusercontent.com`` for the source repo. Sources are
public; no auth needed.

Implements the arcade.dev Progressive Detail pattern via ``mode``:

- ``markdown`` - full body (default)
- ``outline`` - H1-H3 headings only
- ``sections:<heading>`` - just one section starting at a matching heading

Implements Natural Identifier: callers may pass a rendered URL, a
relative path, or a ``.md`` path - the tool resolves each shape to a
candidate ``docs/<path>`` and tries them in order.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

from mcp.server.fastmcp import Context, FastMCP  # noqa: TC002

from combine_mcp.config import (
    DocSource,  # noqa: TC001
    format_sources_guide,
    validate_source_id,
)
from combine_mcp.tools._helpers import format_error
from combine_mcp.tools._paper_index import PaperIndex

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_OUTLINE_MAX_LEVEL = 3


def _candidate_source_paths(
    url_or_path: str,
    docs_site_url: str = "",
) -> list[str]:
    """Map a docs URL or relative path to candidate ``docs/...`` paths.

    Handles three input shapes:
    - Rendered URL: ``https://example.docs.cern.ch/analysis/grid/``
    - Relative path: ``analysis/grid/`` or ``/analysis/grid/``
    - Direct .md path: ``analysis/grid.md`` (with or without ``docs/`` prefix)

    For directory-style inputs ``foo/bar/`` we return both ``foo/bar/index.md``
    (the MkDocs convention) and ``foo/bar.md`` (the alternative MkDocs
    convention). The tool tries them in order and falls through 404s.

    When ``docs_site_url`` has a base path (e.g. the Combine docs live at
    ``…/HiggsAnalysis-CombinedLimit/latest/``), that prefix is stripped
    from rendered-URL inputs so the candidate paths don't end up
    polluted with the site's base path.
    """
    raw = url_or_path.strip()
    if not raw:
        return []
    parsed = urlparse(raw)
    if parsed.scheme:
        path = parsed.path
        site_base = urlparse(docs_site_url).path.rstrip("/") if docs_site_url else ""
        if site_base and path.startswith(site_base + "/"):
            path = path[len(site_base):]
        elif site_base and path == site_base:
            path = ""
    else:
        path = raw
    path = path.split("#", 1)[0].split("?", 1)[0]
    path = path.strip("/")
    if path.startswith("docs/"):
        path = path[len("docs/"):]
    if not path:
        return ["docs/index.md"]
    if path.endswith(".md"):
        return [f"docs/{path}"]
    return [f"docs/{path}/index.md", f"docs/{path}.md"]


def _make_outline(markdown: str) -> list[dict[str, Any]]:
    """Extract H1-H3 headings as ``[{level, heading}, ...]``."""
    return [
        {"level": len(m.group(1)), "heading": m.group(2)}
        for m in _HEADING_RE.finditer(markdown)
        if len(m.group(1)) <= _OUTLINE_MAX_LEVEL
    ]


def _extract_section(markdown: str, heading: str) -> str:
    """Return the slice from a matching heading to the next equal-or-higher heading.

    Heading match is case-insensitive on the trimmed heading text. Returns
    ``""`` if no heading matches.
    """
    target = heading.strip().lower()
    matches = list(_HEADING_RE.finditer(markdown))
    for i, m in enumerate(matches):
        if m.group(2).strip().lower() != target:
            continue
        start = m.start()
        level = len(m.group(1))
        end = len(markdown)
        for j in range(i + 1, len(matches)):
            if len(matches[j].group(1)) <= level:
                end = matches[j].start()
                break
        return markdown[start:end].rstrip() + "\n"
    return ""


def _project(markdown: str, mode: str) -> dict[str, Any]:
    """Apply the ``mode`` projection to a Markdown body."""
    if mode == "outline":
        return {"mode": "outline", "outline": _make_outline(markdown)}
    if mode.startswith("sections:"):
        heading = mode.split(":", 1)[1]
        section = _extract_section(markdown, heading)
        return {
            "mode": mode,
            "heading": heading,
            "found": bool(section),
            "content": section,
        }
    return {"mode": "markdown", "content": markdown}


def _rendered_url(docs_base: str, source_path: str) -> str:
    """Map a ``docs/<path>`` source path back to its rendered URL (MkDocs)."""
    inner = source_path[len("docs/"):] if source_path.startswith("docs/") else source_path
    inner = inner.removesuffix(".md")
    if inner == "index":
        inner = ""
    elif inner.endswith("/index"):
        inner = inner[: -len("/index")]
    base = docs_base.rstrip("/")
    if not inner:
        return f"{base}/"
    return f"{base}/{inner}/"


async def _fetch_paper_section(
    url_or_path: str,
    mode: str,
    source_norm: str,
    index: PaperIndex | None,
    http: Any,
) -> str:
    """Look up one section in a :class:`PaperIndex` and project it.

    ``url_or_path`` accepts either a section id (``"4-2-1"``,
    ``"the-statistical-model"``) or a URL produced by ``search_docs``
    (``"...#sec-4-2-1"``). The mode machinery (``markdown``/``outline``
    /``sections:<heading>``) reuses the existing helpers.
    """
    if index is None or not isinstance(index, PaperIndex):
        return format_error(
            RuntimeError(f"Source {source_norm!r} has no PaperIndex bound"),
            recovery=["This is an internal error. Please report it."],
        )
    try:
        await index.ensure_fresh(http)
    except Exception as exc:  # noqa: BLE001
        return format_error(exc, recovery=[
            f"The local source backing '{source_norm}' could not be loaded.",
            "Verify the file exists at the configured local_path.",
        ])

    section = index.get_section(url_or_path)
    if section is None:
        available = ", ".join(
            f"'{s['id']}'" for s in index.sections[:10]
        )
        return format_error(
            ValueError(f"No section matched {url_or_path!r}"),
            recovery=[
                "Pass either a section id (e.g. '4-2-1') or a URL with a "
                "'#sec-<id>' anchor as returned by search_docs.",
                f"First 10 known section ids: {available}",
                "Use search_docs(query=..., source=...) to find a valid "
                "section id first.",
            ],
        )

    projection = _project(section["body"], mode)
    return json.dumps(
        {
            "source": source_norm,
            "source_path": section["id"],
            "url": section["url"],
            **projection,
        },
        default=str,
    )


def _build_raw_file_url(src: DocSource, path: str) -> str:
    """Return the URL for fetching a raw file from the source repo.

    Currently only GitHub is supported (the only ``vcs_provider`` value
    accepted at config-load time). Future providers branch here.
    """
    owner_repo = src.gitlab_project_path  # vcs-neutral parse
    return (
        f"https://raw.githubusercontent.com/{owner_repo}/"
        f"{src.default_branch}/{path}"
    )


def register(mcp: FastMCP) -> None:
    """Register the fetch tool."""

    @mcp.tool()
    async def fetch_doc(
        url_or_path: str,
        source: str = "combine-docs",
        mode: str = "markdown",
        *,
        ctx: Context[Any, Any],
    ) -> str:
        """Fetch one documentation page as Markdown from upstream VCS.

        Tries both ``docs/<path>/index.md`` and ``docs/<path>.md`` for
        directory-style inputs (MkDocs admits both). Falls through 404s.

        Args:
            url_or_path: Any of:
                - A rendered URL, e.g.
                  ``https://cms-analysis.github.io/HiggsAnalysis-CombinedLimit/latest/part3/runningthetool/``
                - A relative path, e.g. ``part3/runningthetool/``
                - A direct ``.md`` source path, e.g.
                  ``part3/runningthetool.md``
            source: Documentation source ID. Default: ``combine-docs``.
            mode: Output projection.
                - ``"markdown"`` (default): full body.
                - ``"outline"``: list of H1-H3 headings only - cheap way
                  to scout a long page.
                - ``"sections:<heading>"``: extract one section starting
                  from a matching heading (case-insensitive). E.g.
                  ``"sections:Common options"``.
        """
        source_norm = source.strip().lower() if source else "combine-docs"

        ctxd = ctx.request_context.lifespan_context
        http = ctxd["http"]
        sources_registry = ctxd["sources"]
        indices = ctxd["indices"]

        try:
            validate_source_id(source_norm, sources_registry)
        except ValueError as e:
            return format_error(e, recovery=[
                format_sources_guide(sources_registry),
            ])

        source_obj = sources_registry[source_norm]

        # Local-paper sources read from an in-memory PaperIndex; no HTTP.
        if source_obj.source_type == "local-paper":
            return await _fetch_paper_section(
                url_or_path=url_or_path,
                mode=mode,
                source_norm=source_norm,
                index=indices.get(source_norm),
                http=http,
            )

        candidates = _candidate_source_paths(
            url_or_path, source_obj.docs_site_url,
        )
        if not candidates:
            return format_error(
                ValueError(f"Could not derive a source path from {url_or_path!r}"),
                recovery=[
                    "Pass a URL like https://<docs-site>/<path>/",
                    "or a relative path like 'part3/runningthetool/'.",
                    "Use search_docs(query=..., source=...) to find a valid URL first.",
                ],
            )

        last_error: Exception | None = None
        for path in candidates:
            api_url = _build_raw_file_url(source_obj, path)
            try:
                response = await http.get(api_url)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                continue
            if response.status_code == 404:
                continue
            try:
                response.raise_for_status()
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                continue

            markdown = response.text
            projection = _project(markdown, mode)
            return json.dumps(
                {
                    "source": source_norm,
                    "source_path": path,
                    "url": _rendered_url(source_obj.docs_site_url, path),
                    **projection,
                },
                default=str,
            )

        return format_error(
            last_error or FileNotFoundError("No matching source file"),
            recovery=[
                f"Tried {len(candidates)} candidate path(s) - none resolved.",
                "Use search_docs(query=..., source=...) to find the correct URL first, "
                "then re-call fetch with that URL.",
            ],
        )
