"""Lazy-loaded BM25 index over the published MkDocs search payload.

Loads ``/search/search_index.json`` from the rendered docs site (Material
for MkDocs ships this for the in-page search box) and builds an
in-memory BM25 ranker over the per-page ``title + text`` blobs. Refreshes
at most every :data:`_TTL_SECONDS`.

We deliberately do **not** re-implement the Lunr index that lives inside
the JSON payload's ``index`` key. BM25 over the same ``docs`` array gives
us better recall on multi-token queries, with one well-maintained
dependency (``rank_bm25``).

Module-level helpers (``_tokenize``, ``_make_snippet``, ``_section_of``,
``_strip_anchor``, ``_absolute_url``) are also reused by the GitBook
backend in :mod:`combine_mcp.tools._gitbook_index`.
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any

from rank_bm25 import BM25Okapi

if TYPE_CHECKING:
    import httpx

TTL_SECONDS = 24 * 3600
_TTL_SECONDS = TTL_SECONDS  # back-compat alias
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
_SNIPPET_WORDS = 32
_PUNCT_STRIP = ".,;:()[]{}!?\"'`"


def _tokenize(text: str | None) -> list[str]:
    """Lowercase word-token split. Empty/None -> empty list."""
    if not text:
        return []
    return [m.group(0).lower() for m in _TOKEN_RE.finditer(text)]


def _strip_anchor(location: str) -> str:
    return location.split("#", 1)[0]


def _absolute_url(docs_base: str, location: str) -> str:
    base = docs_base.rstrip("/")
    if not location:
        return f"{base}/"
    return f"{base}/{location.lstrip('/')}"


def _section_of(location: str) -> str:
    """Return the first path segment of ``location`` (the top-level section).

    Empty string for the home page (``location`` is empty or just an
    anchor on root).
    """
    head = _strip_anchor(location).strip("/")
    if not head:
        return ""
    return head.split("/", 1)[0]


def _make_snippet(text: str, query_tokens: list[str]) -> str:
    """Pick a short window of words around the densest match of query tokens.

    The MkDocs ``text`` field is already plain text (HTML stripped), so we
    can word-split naively. Falls back to the first window if no token
    appears in the document.
    """
    if not text:
        return ""
    words = text.split()
    if not words:
        return ""
    if not query_tokens:
        return " ".join(words[:_SNIPPET_WORDS])

    qset = set(query_tokens)
    best_start = 0
    best_score = -1
    window = _SNIPPET_WORDS
    last_start = max(0, len(words) - window)
    for start in range(0, last_start + 1):
        chunk = words[start:start + window]
        score = sum(1 for w in chunk if w.lower().strip(_PUNCT_STRIP) in qset)
        if score > best_score:
            best_score = score
            best_start = start

    snippet = " ".join(words[best_start:best_start + window])
    if best_start + window < len(words):
        snippet = snippet + " ..."
    if best_start > 0:
        snippet = "... " + snippet
    return snippet


class DocsIndex:
    """Lazy-loaded BM25 index over the MkDocs ``search_index.json`` payload."""

    def __init__(
        self,
        docs_base: str | None = None,
        search_index_url: str | None = None,
    ) -> None:
        """Initialize the index.

        Args:
            docs_base: Base URL of the docs site (e.g.,
                'https://atlas-software.docs.cern.ch'). If provided,
                search_index_url is computed from it. Kept for backward
                compatibility.
            search_index_url: Full URL to search_index.json. Takes
                precedence over docs_base.
        """
        if search_index_url:
            self.search_index_url = search_index_url
            # Extract docs_base from search_index_url for _absolute_url calls
            self.docs_base = search_index_url.rsplit("/search/", 1)[0]
        elif docs_base:
            self.docs_base = docs_base.rstrip("/")
            self.search_index_url = f"{self.docs_base}/search/search_index.json"
        else:
            raise ValueError(
                "Either docs_base or search_index_url must be provided"
            )
        self.docs: list[dict[str, Any]] = []
        self.bm25: BM25Okapi | None = None
        self.fetched_at: float = 0.0

    @property
    def is_stale(self) -> bool:
        return (time.time() - self.fetched_at) > _TTL_SECONDS

    @property
    def is_loaded(self) -> bool:
        return self.bm25 is not None

    async def refresh(
        self,
        http: httpx.AsyncClient,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Download the search payload and rebuild the BM25 ranker.

        Args:
            http: Shared async HTTP client.
            headers: Extra HTTP headers (used for auth-gated sources).
                Pass ``None`` for public sources.
        """
        response = await http.get(
            self.search_index_url,
            headers=headers or None,
        )
        response.raise_for_status()
        payload = response.json()
        raw_docs = payload.get("docs", []) if isinstance(payload, dict) else []
        if not isinstance(raw_docs, list):
            raw_docs = []

        kept: list[dict[str, Any]] = []
        corpora: list[list[str]] = []
        for doc in raw_docs:
            if not isinstance(doc, dict):
                continue
            tokens = _tokenize(
                f"{doc.get('title') or ''} {doc.get('text') or ''}",
            )
            if not tokens:
                continue
            kept.append(doc)
            corpora.append(tokens)

        self.docs = kept
        self.bm25 = BM25Okapi(corpora) if corpora else None
        self.fetched_at = time.time()

    async def ensure_fresh(
        self,
        http: httpx.AsyncClient,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Refresh the index if missing or stale (older than 24 h)."""
        if not self.is_loaded or self.is_stale:
            await self.refresh(http, headers=headers)

    def search(
        self,
        query: str,
        *,
        limit: int,
        section: str | None,
    ) -> list[dict[str, Any]]:
        """Return up to ``limit`` BM25-ranked hits, optionally section-filtered.

        Each hit carries ``{title, url, path, section, score, snippet}``.
        Returns an empty list if the index is empty or no token in the
        query appears in any document.
        """
        if self.bm25 is None:
            return []
        tokens = _tokenize(query)
        if not tokens:
            return []

        scores = self.bm25.get_scores(tokens)
        order = sorted(
            range(len(scores)), key=lambda i: float(scores[i]), reverse=True,
        )

        section_lc = section.lower() if section else None
        results: list[dict[str, Any]] = []
        for idx in order:
            score = float(scores[idx])
            if score <= 0:
                break
            doc = self.docs[idx]
            location = doc.get("location") or ""
            if section_lc is not None and _section_of(location) != section_lc:
                continue
            results.append({
                "title": doc.get("title") or "(untitled)",
                "url": _absolute_url(self.docs_base, location),
                "path": _strip_anchor(location),
                "section": _section_of(location),
                "score": round(score, 3),
                "snippet": _make_snippet(doc.get("text") or "", tokens),
            })
            if len(results) >= limit:
                break
        return results
