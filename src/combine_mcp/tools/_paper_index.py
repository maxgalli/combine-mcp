"""BM25 index over the sections of a single local plain-text file.

Used for the Combine paper (``paper_clean.txt``): a 175 KB extraction
from arXiv:2404.06614v2 with no Markdown structure. Sections are
detected heuristically (numbered subsection headings + a small set of
un-numbered chapter heads). Each section becomes one BM25 document;
search returns title / id / snippet; the companion ``fetch_doc``
branch returns a section's full body.

Mirrors the :class:`combine_mcp.tools._index.DocsIndex` interface
(``ensure_fresh`` / ``search``) so the server's lifespan can treat
both backends uniformly.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rank_bm25 import BM25Okapi

from combine_mcp.tools._index import _make_snippet, _tokenize

if TYPE_CHECKING:
    import httpx

# How often to re-check the source file's mtime. Files rarely change at
# runtime, so the TTL can be coarse.
_TTL_SECONDS = 24 * 3600

# ---------------------------------------------------------------------------
# Heading detection
# ---------------------------------------------------------------------------
#
# The paper's PDF extraction yields three heading shapes:
#
# (1) Numbered inline: "4.2.1 Template-based shape analyses"
# (2) Numbered split:  "4.2"  (blank line)  "Shape analyses"
# (3) Un-numbered top-level chapter: "Installation" on its own line,
#     surrounded by blank lines.
#
# Numbered shapes are unambiguous. Un-numbered top-level chapters are
# detected by a permissive heuristic (short title-cased line surrounded
# by blanks); false positives become small "stub" sections that BM25
# rarely surfaces.

_INLINE_NUMBERED_RE = re.compile(
    r"^(\d+(?:\.\d+)+)\s+([A-Z][^\n]{1,90}?)\s*$",
    re.MULTILINE,
)
_SPLIT_NUMBERED_RE = re.compile(
    r"^(\d+(?:\.\d+)+)\n\n([A-Z][^\n.]{2,90}?)\s*\n",
    re.MULTILINE,
)
# Un-numbered chapter-style heading: short title-cased line between blanks.
_PLAIN_CHAPTER_RE = re.compile(
    r"(?:^|\n)\n([A-Z][A-Za-z][A-Za-z0-9 ,;:'\-/&]{3,55})\n\n",
)

# Lines that pass the plain-chapter regex but are almost certainly not
# chapter headings. Extend as needed; false positives become small
# low-content sections rather than breaking anything.
_PLAIN_CHAPTER_NOISE = frozenset({
    "Object name",
    "Type",
    "Description",
    "Contents",
    "Default values",
    "Counting analyses",  # caught by numbered detector instead
    "Shape analyses",     # caught by numbered detector instead
    "Pre-fit",
    "Post-fit",
    "Source",
    "Reference",
})

_SECTION_NUMBER_BOUND = 99  # any number bigger than this is noise (e.g. "0.25")
_MIN_SECTION_CHARS = 50     # drop sections whose body is essentially empty


def _is_plausible_number(num: str) -> bool:
    """Reject numeric strings that are obviously not section numbers."""
    parts = num.split(".")
    try:
        ints = [int(p) for p in parts]
    except ValueError:
        return False
    if not ints or ints[0] <= 0 or ints[0] > _SECTION_NUMBER_BOUND:
        return False
    # Sub-section depth: 4.2.1 fine, 4.2.1.1.1 is suspicious
    return len(ints) <= 4


def _slugify(text: str) -> str:
    """Lowercase, dash-separated, ASCII-only slug suitable for URLs."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def _find_headings(text: str) -> list[tuple[int, str, str]]:
    """Return ``[(byte_offset, section_id, title), ...]`` sorted by offset.

    ``section_id`` is the numbered form (``"4-2-1"``) for numbered
    headings, or a slug of the title for un-numbered chapter heads.
    """
    found: list[tuple[int, str, str]] = []
    seen_offsets: set[int] = set()

    for m in _INLINE_NUMBERED_RE.finditer(text):
        num = m.group(1)
        if not _is_plausible_number(num):
            continue
        title = m.group(2).strip()
        sid = num.replace(".", "-")
        found.append((m.start(), sid, f"{num} {title}"))
        seen_offsets.add(m.start())

    for m in _SPLIT_NUMBERED_RE.finditer(text):
        num = m.group(1)
        if not _is_plausible_number(num):
            continue
        title = m.group(2).strip()
        sid = num.replace(".", "-")
        if m.start() in seen_offsets:
            continue
        found.append((m.start(), sid, f"{num} {title}"))
        seen_offsets.add(m.start())

    for m in _PLAIN_CHAPTER_RE.finditer(text):
        title = m.group(1).strip()
        if title in _PLAIN_CHAPTER_NOISE:
            continue
        # Drop hits that look numeric ("4.2" plain-chapter capture).
        if re.fullmatch(r"\d+(\.\d+)*", title):
            continue
        # Skip if a numbered heading already sits at this offset (rare).
        if m.start(1) in seen_offsets:
            continue
        # Skip lines that contain math operators / Greek symbols / etc.
        if re.search(r"[=<>~⃗µν∑∏∫]", title):
            continue
        sid = _slugify(title)
        found.append((m.start(1), sid, title))
        seen_offsets.add(m.start(1))

    found.sort(key=lambda t: t[0])
    return found


def _split_into_sections(text: str) -> list[dict[str, Any]]:
    """Cut the paper text into sections at detected heading offsets.

    Returns ``[{id, title, body}, ...]`` in document order. Drops
    sections whose body is shorter than :data:`_MIN_SECTION_CHARS`
    (typically PDF-extraction noise like equation captions caught by
    the un-numbered chapter regex).
    """
    headings = _find_headings(text)
    if not headings:
        return [{"id": "full-text", "title": "Full text", "body": text}]

    sections: list[dict[str, Any]] = []

    # Any preamble text before the first heading.
    first_offset = headings[0][0]
    preamble = text[:first_offset].strip()
    if len(preamble) >= _MIN_SECTION_CHARS:
        sections.append({
            "id": "preamble",
            "title": "Preamble",
            "body": preamble,
        })

    for i, (offset, sid, title) in enumerate(headings):
        end = headings[i + 1][0] if i + 1 < len(headings) else len(text)
        body = text[offset:end].strip()
        if len(body) < _MIN_SECTION_CHARS:
            continue
        sections.append({"id": sid, "title": title, "body": body})

    # Deduplicate by id (numbered sections sometimes repeat as running
    # headers across pages). Keep the longest body for each id.
    by_id: dict[str, dict[str, Any]] = {}
    for sec in sections:
        existing = by_id.get(sec["id"])
        if existing is None or len(sec["body"]) > len(existing["body"]):
            by_id[sec["id"]] = sec
    # Preserve original order using a position index.
    order = {sec["id"]: i for i, sec in enumerate(sections)}
    return sorted(by_id.values(), key=lambda s: order[s["id"]])


# ---------------------------------------------------------------------------
# PaperIndex
# ---------------------------------------------------------------------------


class PaperIndex:
    """Lazy BM25 index over the sections of a single local text file.

    Public surface (mirrors :class:`DocsIndex`):

    - :meth:`ensure_fresh` — load or reload the file if stale.
    - :meth:`search` — BM25 hits with snippets, returned in the same
      shape as :meth:`DocsIndex.search`.
    - :meth:`get_section` — fetch one section's full body by id.
      Consumed by ``fetch_doc``.
    """

    def __init__(
        self,
        local_path: str | Path,
        docs_site_url: str,
    ) -> None:
        self.local_path = Path(local_path)
        self.docs_site_url = docs_site_url.rstrip("/")
        self.sections: list[dict[str, Any]] = []
        self.bm25: BM25Okapi | None = None
        self.fetched_at: float = 0.0
        self._loaded_mtime: float = 0.0

    @property
    def is_loaded(self) -> bool:
        return self.bm25 is not None

    @property
    def is_stale(self) -> bool:
        if not self.is_loaded:
            return True
        if (time.time() - self.fetched_at) > _TTL_SECONDS:
            return True
        # File-mtime check is cheap and catches in-place edits.
        try:
            mtime = self.local_path.stat().st_mtime
        except OSError:
            return False
        return mtime > self._loaded_mtime

    def _section_url(self, section_id: str) -> str:
        return f"{self.docs_site_url}#sec-{section_id}"

    async def ensure_fresh(
        self,
        http: httpx.AsyncClient | None = None,  # noqa: ARG002 — interface parity
        *,
        headers: dict[str, str] | None = None,  # noqa: ARG002
    ) -> None:
        """Load the file and rebuild the BM25 ranker if needed.

        The ``http`` and ``headers`` arguments are kept for interface
        parity with :class:`DocsIndex` — they're unused here because the
        source is local.
        """
        if not self.is_stale:
            return
        text = self.local_path.read_text(encoding="utf-8")
        self._loaded_mtime = self.local_path.stat().st_mtime

        sections = _split_into_sections(text)
        corpora: list[list[str]] = []
        kept: list[dict[str, Any]] = []
        for sec in sections:
            tokens = _tokenize(f"{sec['title']} {sec['body']}")
            if not tokens:
                continue
            kept.append(sec)
            corpora.append(tokens)
        self.sections = kept
        self.bm25 = BM25Okapi(corpora) if corpora else None
        self.fetched_at = time.time()

    def search(
        self,
        query: str,
        *,
        limit: int,
        section: str | None = None,  # noqa: ARG002 — interface parity
    ) -> list[dict[str, Any]]:
        """Return up to ``limit`` BM25-ranked sections.

        Each hit carries ``{title, url, path, section, score, snippet}``
        — the same shape as :meth:`DocsIndex.search` so downstream tools
        don't need to branch.
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
        results: list[dict[str, Any]] = []
        for idx in order:
            score = float(scores[idx])
            if score <= 0:
                break
            sec = self.sections[idx]
            results.append({
                "title": sec["title"],
                "url": self._section_url(sec["id"]),
                "path": sec["id"],
                "section": "",
                "score": round(score, 3),
                "snippet": _make_snippet(sec["body"], tokens),
            })
            if len(results) >= limit:
                break
        return results

    def get_section(self, section_id: str) -> dict[str, Any] | None:
        """Return ``{id, title, body, url}`` for a section, or ``None``.

        ``section_id`` may be either the bare id (``"4-2-1"``) or a
        section URL produced by :meth:`search`; we strip everything
        up to and including the ``#sec-`` anchor before matching.
        """
        wanted = section_id.strip()
        if "#sec-" in wanted:
            wanted = wanted.split("#sec-", 1)[1]
        elif "#" in wanted:
            wanted = wanted.split("#", 1)[1]
        wanted = wanted.strip("/").strip()
        for sec in self.sections:
            if sec["id"] == wanted:
                return {
                    **sec,
                    "url": self._section_url(sec["id"]),
                }
        return None
