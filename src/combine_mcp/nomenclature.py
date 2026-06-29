"""Instructions blob describing the corpus this MCP exposes.

Embedded into the FastMCP ``instructions`` string by
:func:`combine_mcp.server._build_instructions`. Kept here so the
agent-facing description of the corpus lives in one place.
"""

from __future__ import annotations

COMBINE_DOCS_GUIDE = """\
# CMS Combine — Quick Reference

Source site: https://cms-analysis.github.io/HiggsAnalysis-CombinedLimit/latest
Source repo: https://github.com/cms-analysis/HiggsAnalysis-CombinedLimit (public)
Backend:     Material for MkDocs (BM25 over the published search_index.json)

## Scope

This MCP exposes the official CMS Combine (HiggsAnalysis-CombinedLimit)
documentation: the statistical-analysis tool used across CMS searches
and measurements. Coverage includes:

- Datacard syntax and physics models
- Running modes (AsymptoticLimits, FitDiagnostics, MultiDimFit,
  HybridNew, ChannelCompatibilityCheck, Significance, GoodnessOfFit, ...)
- Common statistical methods, advanced use cases, debugging fits
- Tutorials and worked examples
- Tool reference and option flags

## NOT in scope

- The Combine paper (arXiv:2404.06614) — not exposed here.
- The Combine source code — not exposed here.
- cms-talk forum Q&A — not exposed here.

These three corpora may become additional MCP sources in the future.

## Tools

- ``search_docs(query, source="combine-docs", limit=?)`` — BM25 search
  over the published MkDocs search payload. Returns titles, URLs,
  snippets only.
- ``fetch_doc(url_or_path, source="combine-docs", mode=?)`` — fetch the
  upstream Markdown source from GitHub. ``mode`` is one of:
    - ``"markdown"`` (default) — full body
    - ``"outline"`` — H1-H3 headings only
    - ``"sections:<heading>"`` — just the matching section

## Typical flow

1. ``search_docs("what does --robustFit do")`` -> hits with URLs.
2. ``fetch_doc(<url>, mode="outline")`` -> headings.
3. ``fetch_doc(<url>, mode="sections:Common options")`` -> just that
   section's Markdown.

## Freshness

The search index is refreshed at most every 24 hours from the published
MkDocs payload at ``/search/search_index.json``. Source Markdown is
fetched live from GitHub (raw.githubusercontent.com) on each call.
"""
