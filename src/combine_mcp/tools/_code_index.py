"""BM25 index over a local directory tree (one file = one document).

Used for the Combine source code: walks an ``include_globs`` list under
a configured ``local_root`` (e.g. ``corpora/combine`` for the pinned
v10.6.0 submodule), indexes the title + body of each file with BM25,
and returns per-file citation URLs built from a configurable
``url_template`` (typically a GitHub blob URL).

Each file becomes one BM25 document — no chunking. BM25 will surface
the right file for a query that mentions any of its tokens; the agent
calls ``fetch_doc`` to get the file's content (in markdown / outline /
sections projections).

Mirrors the :class:`combine_mcp.tools._index.DocsIndex` interface
(``ensure_fresh`` / ``search``) so the lifespan dispatch doesn't
branch.
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

# 24 h between mtime re-checks. Files seldom change at runtime; this is
# only here to catch the rare in-place edit during a long-lived server.
_TTL_SECONDS = 24 * 3600

# Bytes cap per file. Combine's biggest checked-in source is well under
# this; anything larger is treated as opaque and skipped, to keep the
# in-memory index small.
_MAX_FILE_BYTES = 256 * 1024

# Crude code-aware "outline" extractor used when fetch_doc is called
# with mode="outline" on a code file. Catches top-level Python defs /
# classes and C++ free function-or-method definitions / class
# declarations. Not exhaustive — just a cheap "scout this file" view.
_OUTLINE_RE = re.compile(
    r"^(?:"
    r"(?:def|class)\s+(\w+)"
    r"|(?:class|struct)\s+(\w+)"
    r"|(?:[A-Za-z_][\w:<>*&\s]*?\s+)?(\w+)\s*\([^)\n]*\)\s*(?:const)?\s*(?:\{|$)"
    r")",
    re.MULTILINE,
)


def _is_text_file(path: Path, max_bytes: int = _MAX_FILE_BYTES) -> bool:
    """Heuristic: file is small, decodes as UTF-8, no NUL bytes in head."""
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size == 0 or size > max_bytes:
        return False
    try:
        head = path.read_bytes()[:4096]
    except OSError:
        return False
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _walk_files(root: Path, include_globs: list[str]) -> list[Path]:
    """Return sorted, deduplicated files matching any of ``include_globs``.

    Globs are evaluated relative to ``root``. ``**`` recursion is
    supported (pathlib's ``glob`` semantics). Skipped files: binary,
    empty, or larger than the per-file cap.
    """
    seen: set[Path] = set()
    for pattern in include_globs:
        for path in sorted(root.glob(pattern)):
            if not path.is_file():
                continue
            rp = path.resolve()
            if rp in seen:
                continue
            if not _is_text_file(path):
                continue
            seen.add(rp)
    # Stable, deterministic order so the BM25 doc list is reproducible.
    return sorted(seen, key=lambda p: str(p))


def _outline_of(body: str, *, max_items: int = 40) -> list[str]:
    """Return a small list of "interesting names" from a source file."""
    out: list[str] = []
    for m in _OUTLINE_RE.finditer(body):
        name = next((g for g in m.groups() if g), None)
        if not name:
            continue
        if name in ("if", "for", "while", "return", "switch", "typedef"):
            continue
        if name not in out:
            out.append(name)
        if len(out) >= max_items:
            break
    return out


def _make_code_snippet(body: str, query_tokens: list[str]) -> str:
    """Snippet wrapper: reuse the docs snippet picker on the file body."""
    return _make_snippet(body, query_tokens)


class CodeIndex:
    """Lazy BM25 index over a local file tree."""

    def __init__(
        self,
        local_root: str | Path,
        include_globs: list[str] | tuple[str, ...],
        docs_site_url: str,
        url_template: str | None = None,
    ) -> None:
        self.local_root = Path(local_root)
        self.include_globs = list(include_globs)
        self.docs_site_url = docs_site_url.rstrip("/")
        self.url_template = url_template
        self.docs: list[dict[str, Any]] = []
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
        try:
            mtime = self.local_root.stat().st_mtime
        except OSError:
            return False
        return mtime > self._loaded_mtime

    def _url_for(self, relpath: str) -> str:
        if self.url_template:
            return self.url_template.format(relpath=relpath)
        return f"{self.docs_site_url}/{relpath}"

    async def ensure_fresh(
        self,
        http: httpx.AsyncClient | None = None,  # noqa: ARG002 — interface parity
        *,
        headers: dict[str, str] | None = None,  # noqa: ARG002
    ) -> None:
        if not self.is_stale:
            return
        if not self.local_root.exists():
            msg = f"local_root does not exist: {self.local_root}"
            raise FileNotFoundError(msg)
        self._loaded_mtime = self.local_root.stat().st_mtime

        files = _walk_files(self.local_root, self.include_globs)
        docs: list[dict[str, Any]] = []
        corpora: list[list[str]] = []
        for path in files:
            try:
                body = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            relpath = path.relative_to(self.local_root).as_posix()
            tokens = _tokenize(f"{relpath} {body}")
            if not tokens:
                continue
            docs.append({
                "relpath": relpath,
                "title": relpath,
                "body": body,
            })
            corpora.append(tokens)
        self.docs = docs
        self.bm25 = BM25Okapi(corpora) if corpora else None
        self.fetched_at = time.time()

    def search(
        self,
        query: str,
        *,
        limit: int,
        section: str | None = None,  # noqa: ARG002 — interface parity
    ) -> list[dict[str, Any]]:
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
            doc = self.docs[idx]
            results.append({
                "title": doc["title"],
                "url": self._url_for(doc["relpath"]),
                "path": doc["relpath"],
                "section": "",
                "score": round(score, 3),
                "snippet": _make_code_snippet(doc["body"], tokens),
            })
            if len(results) >= limit:
                break
        return results

    def get_file(self, relpath_or_url: str) -> dict[str, Any] | None:
        """Look up a file by its relpath (``"python/PhysicsModel.py"``)
        or by a URL built from :meth:`_url_for`. Returns
        ``{relpath, title, body, url}`` or ``None``.
        """
        wanted = relpath_or_url.strip()
        # If they passed the full URL, strip the prefix part to get the
        # relpath. Works for both ``url_template``-based URLs (the
        # template has ``{relpath}`` at the end) and ``docs_site_url``-
        # based ones.
        if "://" in wanted:
            # Take whatever comes after the last "/<branch_or_root>/"
            # segment that matches a known file. Cheap approach: try
            # each doc and see if its URL matches.
            for doc in self.docs:
                if self._url_for(doc["relpath"]) == wanted:
                    return {**doc, "url": wanted}
            return None
        # Bare relpath — normalize and match.
        wanted = wanted.lstrip("./").lstrip("/")
        for doc in self.docs:
            if doc["relpath"] == wanted:
                return {**doc, "url": self._url_for(doc["relpath"])}
        return None
