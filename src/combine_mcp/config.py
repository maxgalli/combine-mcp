"""Configuration for documentation sources.

This module owns the source registry: how each documentation site is
addressed (search index URL, repo URL, rendered site URL) and which
upstream VCS holds the raw Markdown.

All currently registered sources are public — auth is not modelled here.
If a future source requires it, this module is the place to add an
auth-config dataclass and Secret-Injection helpers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

__all__ = [
    "DocSource",
    "format_sources_guide",
    "get_default_sources",
    "load_sources",
    "parse_repo_path",
    "validate_source_id",
]


@dataclass(frozen=True)
class DocSource:
    """Configuration for a documentation source.

    The repo path (``<owner>/<repo>`` for GitHub, ``<group>/.../<repo>``
    for GitLab) is derived from :attr:`repo_url` on demand (see
    :attr:`gitlab_project_path`), so callers do not need to know or
    supply numeric project ids.

    Only the ``"mkdocs"`` source type is currently supported (each site
    publishes ``/search/search_index.json`` and the rendered URL strips
    the ``docs/`` prefix and the ``.md`` suffix). Additional source types
    (``"local-text"``, ``"local-files"``, ...) can be added when the
    paper / code / forum corpora are wired up.
    """

    id: str
    """Unique identifier (e.g. ``'combine-docs'``)."""

    name: str
    """Display name."""

    repo_url: str
    """Public URL of the source repository (GitHub or GitLab)."""

    docs_site_url: str
    """Base URL of the rendered documentation site, OR a stable citation
    URL for local-corpus sources (e.g. an arXiv abstract URL for the
    paper). Used as the prefix for per-section URLs."""

    search_index_url: str | None = None
    """URL of the published MkDocs ``search_index.json``. Required for
    ``source_type="mkdocs"``; unused for local sources."""

    source_type: str = "mkdocs"
    """Index backend. One of:

    - ``"mkdocs"`` — BM25 over the published ``search_index.json`` of
      a Material-for-MkDocs site; ``fetch_doc`` pulls Markdown from
      GitHub (or, in principle, another VCS).
    - ``"local-paper"`` — BM25 over the sections of a single local
      plain-text file (the Combine paper); ``fetch_doc`` returns a
      section's body from disk.

    Future values (``"local-files"``, ``"local-forum"``) will route to
    other backends when the code and forum corpora are added.
    """

    default_branch: str = "main"
    """Git ref used by ``fetch_doc`` to retrieve raw Markdown. Unused
    for ``local-*`` source types."""

    vcs_provider: str = "github"
    """Where ``fetch_doc`` pulls raw file contents from for VCS-backed
    sources. Currently only ``"github"`` is implemented. Unused for
    ``local-*`` source types."""

    local_path: str | None = None
    """Path to a single local data file. Required for ``local-paper``.

    Resolution:
    - Absolute paths are stored as-is.
    - Relative paths in a JSON config are resolved against the config
      file's directory (see :func:`load_sources`). The bundled
      ``docs_sources.json`` ships relative paths so the MCP repo is
      self-contained."""

    local_root: str | None = None
    """Path to a local directory tree. Required for ``local-files``
    (e.g. the Combine source submodule). Same relative-path resolution
    as :attr:`local_path`."""

    include_globs: tuple[str, ...] = ()
    """For ``local-files``, the glob patterns (relative to
    :attr:`local_root`) selecting which files become documents. The
    same file matched by multiple patterns is indexed once."""

    url_template: str | None = None
    """For ``local-files``, a template used to build per-file citation
    URLs. ``{relpath}`` is substituted with each file's POSIX path
    relative to :attr:`local_root`. Example::

        "https://github.com/cms-analysis/HiggsAnalysis-CombinedLimit/blob/v10.6.0/{relpath}"
    """

    @property
    def gitlab_project_path(self) -> str:
        """Path component of the repo URL (raw, not URL-encoded).

        Despite the legacy name, this is VCS-neutral —
        :func:`parse_repo_path` only inspects the URL path, not the
        host. Used as ``<owner>/<repo>`` for GitHub sources.
        """
        return parse_repo_path(self.repo_url)


def parse_repo_path(repo_url: str) -> str:
    """Extract the project path from a repo URL.

    For ``https://github.com/cms-analysis/HiggsAnalysis-CombinedLimit``
    returns ``"cms-analysis/HiggsAnalysis-CombinedLimit"``. Strips a
    trailing ``.git`` if present.

    Works for any standard ``<host>/<group>/<...>/<repo>`` URL shape
    (GitHub and GitLab both qualify); the fetch tool dispatches on
    :attr:`DocSource.vcs_provider`.
    """
    parsed = urlparse(repo_url)
    if not parsed.netloc:
        msg = f"repo_url has no host: {repo_url!r}"
        raise ValueError(msg)
    path = parsed.path.strip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    if not path:
        msg = f"repo_url has no path: {repo_url!r}"
        raise ValueError(msg)
    return path


def get_default_sources() -> dict[str, DocSource]:
    """Load default documentation sources shipped inside the package."""
    config_path = Path(__file__).parent / "docs_sources.json"
    return load_sources(config_path)


def _resolve_local_path(
    raw: str | None, config_dir: Path,
) -> str | None:
    """Resolve a ``local_path`` value from the JSON.

    Absolute paths pass through unchanged. Relative paths are resolved
    against ``config_dir`` and returned as an absolute path string. This
    lets the bundled JSON ship with a relative path (e.g.
    ``"../../corpora/paper_clean.txt"``) and stay portable across
    machines, while user-supplied configs can still use absolute paths.
    """
    if raw is None:
        return None
    p = Path(raw)
    if p.is_absolute():
        return str(p)
    return str((config_dir / p).resolve())


def load_sources(config_path: str | Path) -> dict[str, DocSource]:
    """Load documentation sources from a JSON config file.

    Schema (one entry per source)::

        {
          "id":            "...",
          "name":          "...",
          "repo_url":      "...",
          "docs_site_url": "...",
          "source_type":      "mkdocs",     // optional, default "mkdocs"
          "search_index_url": "...",        // required for "mkdocs"
          "local_path":       "...",        // required for "local-paper";
                                            // relative paths resolved
                                            // against this JSON's directory.
          "default_branch":   "main",       // optional, default "main"
          "vcs_provider":     "github"      // optional, default "github"
        }
    """
    config_path = Path(config_path)
    if not config_path.exists():
        msg = f"Config file not found: {config_path}"
        raise FileNotFoundError(msg)
    config_dir = config_path.parent.resolve()

    with open(config_path, encoding="utf-8") as f:
        data = json.load(f)

    sources: dict[str, DocSource] = {}
    for item in data.get("sources", []):
        try:
            source_type = item.get("source_type", "mkdocs")
            if source_type not in ("mkdocs", "local-paper", "local-files"):
                msg = (
                    f"source {item.get('id')!r}: source_type "
                    f"{source_type!r} not supported "
                    "(currently 'mkdocs', 'local-paper', or 'local-files')"
                )
                raise ValueError(msg)
            if source_type == "mkdocs" and not item.get("search_index_url"):
                msg = (
                    f"source {item.get('id')!r}: source_type='mkdocs' "
                    "requires search_index_url"
                )
                raise ValueError(msg)
            if source_type == "local-paper" and not item.get("local_path"):
                msg = (
                    f"source {item.get('id')!r}: source_type='local-paper' "
                    "requires local_path"
                )
                raise ValueError(msg)
            if source_type == "local-files":
                if not item.get("local_root"):
                    msg = (
                        f"source {item.get('id')!r}: source_type='local-files' "
                        "requires local_root"
                    )
                    raise ValueError(msg)
                if not item.get("include_globs"):
                    msg = (
                        f"source {item.get('id')!r}: source_type='local-files' "
                        "requires include_globs"
                    )
                    raise ValueError(msg)
            vcs_provider = item.get("vcs_provider", "github")
            if vcs_provider != "github":
                msg = (
                    f"source {item.get('id')!r}: vcs_provider "
                    f"{vcs_provider!r} not supported (only 'github' for now)"
                )
                raise ValueError(msg)
            source = DocSource(
                id=item["id"],
                name=item["name"],
                repo_url=item["repo_url"],
                docs_site_url=item["docs_site_url"],
                search_index_url=item.get("search_index_url"),
                source_type=source_type,
                default_branch=item.get("default_branch", "main"),
                vcs_provider=vcs_provider,
                local_path=_resolve_local_path(
                    item.get("local_path"), config_dir,
                ),
                local_root=_resolve_local_path(
                    item.get("local_root"), config_dir,
                ),
                include_globs=tuple(item.get("include_globs", ())),
                url_template=item.get("url_template"),
            )
            sources[source.id] = source
        except KeyError as exc:
            msg = f"Missing required field in source config: {exc}"
            raise ValueError(msg) from exc

    return sources


def validate_source_id(source_id: str, sources: dict[str, DocSource]) -> None:
    """Raise ``ValueError`` if ``source_id`` is not in the registry."""
    if source_id not in sources:
        available = ", ".join(f"'{s}'" for s in sorted(sources.keys()))
        msg = (
            f"Unknown documentation source '{source_id}'. "
            f"Available sources: {available}."
        )
        raise ValueError(msg)


def format_sources_guide(sources: dict[str, DocSource]) -> str:
    """Render the source registry as a human-readable bullet list.

    Used in Recovery Guide messages and the ``docs://sources`` resource.
    """
    lines = ["Available documentation sources:", ""]
    for source in sorted(sources.values(), key=lambda s: s.id):
        lines.append(f"  - {source.id:20} - {source.name}")
    return "\n".join(lines)
