"""BM25 index over a GitHub source tree, fetched at boot via tarball.

Same public surface as :class:`CodeIndex` (``ensure_fresh`` / ``search``
/ ``get_file``), so the tool layer doesn't need to branch. Bootstraps
by downloading a single tarball at the pinned ref from
``codeload.github.com``, extracting to a temp dir, then reusing
:func:`_walk_files` and the BM25 build logic from :mod:`_code_index`.
The temp dir is cleaned up after indexing; bodies are cached in
memory in ``self.docs``.

This is the "remote" counterpart to :class:`CodeIndex`. Both are
appropriate depending on the deployment shape:

- :class:`CodeIndex` — reads a local directory tree. Best when you
  have the source checked out (submodule, vendored copy).
- :class:`RemoteCodeIndex` — downloads a tarball on first use. Best
  when you don't want to ship the source with the server (e.g. a
  minimal container image with no submodule init).
"""

from __future__ import annotations

import io
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from rank_bm25 import BM25Okapi

from combine_mcp.tools._code_index import _TTL_SECONDS, CodeIndex, _walk_files
from combine_mcp.tools._index import _tokenize

if TYPE_CHECKING:
    import httpx


def _tarball_url(repo_url: str, ref: str) -> str:
    """Build the GitHub codeload URL for a source tarball at a ref.

    Works for both branches and tags. Example::

        _tarball_url(
            "https://github.com/cms-analysis/HiggsAnalysis-CombinedLimit",
            "v10.6.0",
        )
        # -> "https://codeload.github.com/cms-analysis/HiggsAnalysis-CombinedLimit/tar.gz/v10.6.0"
    """
    parsed = urlparse(repo_url)
    path = parsed.path.strip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    if "/" not in path:
        msg = f"repo_url has no owner/repo path: {repo_url!r}"
        raise ValueError(msg)
    return f"https://codeload.github.com/{path}/tar.gz/{ref}"


def _extract_safely(tar: tarfile.TarFile, dest: Path) -> None:
    """Extract a tarball using the ``data`` filter when available.

    Python 3.12+ supports ``extractall(filter="data")`` which blocks
    path traversal and other classic tar traps. On 3.11 we fall back
    to plain extractall — acceptable here because the tarball comes
    from a pinned, public GitHub ref, but we still prefer the filter
    where the runtime supports it.
    """
    if sys.version_info >= (3, 12):
        tar.extractall(dest, filter="data")
    else:
        tar.extractall(dest)  # noqa: S202 — see docstring


class RemoteCodeIndex(CodeIndex):
    """BM25 index over a GitHub source tree at a pinned ref.

    Public surface (inherited from :class:`CodeIndex`):

    - :meth:`search` — same shape as :meth:`CodeIndex.search`.
    - :meth:`get_file` — same shape as :meth:`CodeIndex.get_file`.

    Overridden:

    - :meth:`__init__` — takes ``repo_url`` + ``ref`` instead of
      ``local_root``.
    - :meth:`_url_for` — substitutes both ``{relpath}`` and ``{ref}``
      in ``url_template`` (parent only substitutes ``{relpath}``).
    - :meth:`is_stale` — TTL only, no local mtime to check.
    - :meth:`ensure_fresh` — downloads the tarball and indexes it.
    """

    def __init__(
        self,
        repo_url: str,
        ref: str,
        include_globs: list[str] | tuple[str, ...],
        docs_site_url: str,
        url_template: str | None = None,
    ) -> None:
        self.repo_url = repo_url
        self.ref = ref
        self.tarball_url = _tarball_url(repo_url, ref)
        self.include_globs = list(include_globs)
        self.docs_site_url = docs_site_url.rstrip("/")
        self.url_template = url_template
        self.docs = []
        self.bm25 = None
        self.fetched_at = 0.0
        # Parent's ``is_stale`` reads ``self.local_root.stat()``; we
        # override ``is_stale`` below, but keep the attribute present
        # as a sentinel so other parent-level code doesn't blow up.
        self.local_root = Path("/nonexistent")
        self._loaded_mtime = 0.0

    def _url_for(self, relpath: str) -> str:
        """Build the citation URL, substituting ``{relpath}`` and ``{ref}``.

        Templates without ``{ref}`` still work — :meth:`str.format`
        silently ignores extra kwargs. Templates without ``{relpath}``
        would raise, but that's a config error worth surfacing.
        """
        if self.url_template:
            return self.url_template.format(relpath=relpath, ref=self.ref)
        return f"{self.docs_site_url}/{relpath}"

    @property
    def is_stale(self) -> bool:
        """Simple TTL — no local mtime to consult for remote sources."""
        if not self.is_loaded:
            return True
        return (time.time() - self.fetched_at) > _TTL_SECONDS

    async def ensure_fresh(
        self,
        http: "httpx.AsyncClient | None" = None,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Download the tarball, extract to a temp dir, walk + index.

        The temp dir is deleted after indexing; file bodies are held
        in memory in ``self.docs``. Raises on network error, unexpected
        tarball shape, or malformed archive.
        """
        if not self.is_stale:
            return
        if http is None:
            msg = "RemoteCodeIndex.ensure_fresh requires an http client"
            raise ValueError(msg)

        resp = await http.get(
            self.tarball_url,
            follow_redirects=True,
            headers=headers or None,
        )
        resp.raise_for_status()

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with tarfile.open(
                fileobj=io.BytesIO(resp.content), mode="r:gz",
            ) as tar:
                _extract_safely(tar, tmp_path)

            entries = [p for p in tmp_path.iterdir() if p.is_dir()]
            if len(entries) != 1:
                msg = (
                    f"unexpected tarball structure at {self.tarball_url}: "
                    f"expected one top-level dir, got {len(entries)}"
                )
                raise ValueError(msg)
            # Resolve to match ``_walk_files``, which resolves paths for
            # dedup — otherwise ``relative_to`` fails on macOS where
            # ``/var/folders`` symlinks through ``/private/var/folders``.
            source_root = entries[0].resolve()

            files = _walk_files(source_root, self.include_globs)
            docs = []
            corpora = []
            for path in files:
                try:
                    body = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                relpath = path.relative_to(source_root).as_posix()
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
