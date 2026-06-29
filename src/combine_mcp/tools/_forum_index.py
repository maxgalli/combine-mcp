"""BM25 index over a local tree of Discourse-shaped forum JSONs.

Used for cms-talk threads scraped by :mod:`combine_mcp.scrape`. Each
``topic_*.json`` file becomes one BM25 document — title + rendered
transcript of all posts. The matching ``.txt`` mirrors that the
scraper writes alongside are ignored here; we re-render from JSON so
the indexed body's structure is consistent regardless of the scraper
version.

Mirrors the :class:`combine_mcp.tools._index.DocsIndex` interface
(``ensure_fresh`` / ``search``) plus a ``get_topic(id_or_url, post=...)``
accessor used by ``fetch_doc``'s forum branch.

Search hits and bodies have the same JSON shape as the other backends
so the tool handlers don't need to branch.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rank_bm25 import BM25Okapi

from combine_mcp.tools._index import _make_snippet, _tokenize

if TYPE_CHECKING:
    import httpx

# Forum data changes more often than docs/code; refresh on the same
# 24-h TTL but also opportunistically when the directory mtime advances
# (i.e. the scraper has written new files).
_TTL_SECONDS = 24 * 3600

# Drop posts shorter than this from the indexed transcript: usually
# noise like "thanks!", "fixed it", "you're welcome". Same threshold
# combine-bot's RAG used.
_DEFAULT_MIN_POST_CHARS = 50

# Topics to ignore even if present on disk. The scraper already filters
# these, but defending in depth in the indexer means a stale file from
# an older scrape doesn't sneak in.
_DEFAULT_SKIP_TOPIC_IDS: frozenset[int] = frozenset({
    19755,  # subscribers list
})


def _format_post_header(
    post: dict[str, Any],
    *,
    is_accepted: bool,
) -> str:
    """Build the single-line header that precedes a post's body in the
    rendered transcript."""
    n = post.get("post_number", 0)
    if n == 1:
        role = "QUESTION"
    elif is_accepted:
        role = f"REPLY {n} [ACCEPTED ANSWER]"
    else:
        role = f"REPLY {n}"
    user = post.get("username") or "?"
    when = post.get("created_at") or ""
    return f"{role} (by {user} @ {when}):"


def _render_transcript(topic: dict[str, Any], *, min_post_chars: int) -> str:
    """Render a Discourse topic JSON as a plain-text transcript.

    The same shape the agent will see when ``fetch_doc(mode="markdown")``
    is called. Posts shorter than ``min_post_chars`` are kept in the
    indexed body but flagged; they are NOT dropped here because the
    indexer's job is to surface the *thread*, not censor it. (The min-
    post-chars filter applies to noise-aware *snippets* further down.)
    """
    title = topic.get("title") or ""
    topic_id = topic.get("topic_id")
    url = topic.get("url") or ""
    accepted = topic.get("accepted_answer_post_number")
    posts = topic.get("posts") or []

    lines: list[str] = [
        f"TOPIC: {title}",
        f"URL: {url}",
        f"SOLVED: {'yes (reply ' + str(accepted) + ')' if accepted else 'no'}",
        "=" * 60,
    ]
    _ = topic_id  # currently unused in the body; kept for forward compat
    for post in posts:
        body = (post.get("text") or "").strip()
        if not body:
            continue
        if len(body) < min_post_chars:
            # Keep, but mark — useful for the agent to know it was thin.
            body = body + "  [short reply]"
        n = post.get("post_number", 0)
        is_accepted = bool(
            post.get("is_accepted_answer")
            or (accepted is not None and n == accepted),
        )
        lines.append("")
        lines.append(_format_post_header(post, is_accepted=is_accepted))
        lines.append(body)
        lines.append("-" * 40)
    return "\n".join(lines) + "\n"


_POST_RE = re.compile(r"^post[:/]?(?P<key>\S+)$", re.IGNORECASE)


def _parse_post_selector(mode: str) -> str | None:
    """If ``mode`` is ``"post:<key>"``, return ``"<key>"``; else ``None``.

    Accepts ``post:1``, ``post:accepted``, etc. Case-insensitive on the
    ``post:`` prefix. Returns the key unchanged (still a string).
    """
    m = _POST_RE.match(mode.strip())
    return m.group("key") if m else None


def _load_topic(path: Path) -> dict[str, Any] | None:
    """Read one topic JSON from disk. Returns ``None`` on parse error."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


class ForumIndex:
    """Lazy BM25 index over a local directory of Discourse-shaped JSONs."""

    def __init__(
        self,
        local_root: str | Path,
        *,
        include_globs: tuple[str, ...] = ("topic_*.json",),
        skip_topic_ids: frozenset[int] = _DEFAULT_SKIP_TOPIC_IDS,
        min_post_chars: int = _DEFAULT_MIN_POST_CHARS,
        docs_site_url: str = "https://cms-talk.web.cern.ch",
    ) -> None:
        self.local_root = Path(local_root)
        self.include_globs = list(include_globs)
        self.skip_topic_ids = skip_topic_ids
        self.min_post_chars = min_post_chars
        self.docs_site_url = docs_site_url.rstrip("/")
        # Parallel arrays keyed by topic order: topics[i] is the parsed
        # JSON, transcripts[i] is the rendered body string.
        self.topics: list[dict[str, Any]] = []
        self.transcripts: list[str] = []
        self.bm25: BM25Okapi | None = None
        self.fetched_at: float = 0.0
        self._loaded_mtime: float = 0.0

    # ---- staleness / lazy load -----------------------------------------

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

    def _topic_url(self, topic: dict[str, Any]) -> str:
        """Prefer the URL the scraper wrote; fall back to building one."""
        url = topic.get("url")
        if url:
            return str(url)
        topic_id = topic.get("topic_id")
        return f"{self.docs_site_url}/t/{topic_id}"

    def _post_url(self, topic: dict[str, Any], post_number: int) -> str:
        """Discourse per-post URL: ``<site>/t/<id>/<n>``."""
        return f"{self._topic_url(topic).rstrip('/')}/{post_number}"

    async def ensure_fresh(
        self,
        http: "httpx.AsyncClient | None" = None,  # noqa: ARG002 — interface parity
        *,
        headers: dict[str, str] | None = None,  # noqa: ARG002
    ) -> None:
        """Read every topic file, render transcripts, build BM25."""
        if not self.is_stale:
            return
        if not self.local_root.exists():
            msg = f"local_root does not exist: {self.local_root}"
            raise FileNotFoundError(msg)
        self._loaded_mtime = self.local_root.stat().st_mtime

        topics: list[dict[str, Any]] = []
        transcripts: list[str] = []
        corpora: list[list[str]] = []
        paths: set[Path] = set()
        for pattern in self.include_globs:
            for path in self.local_root.glob(pattern):
                if path in paths or not path.is_file():
                    continue
                paths.add(path)

        for path in sorted(paths):
            topic = _load_topic(path)
            if topic is None:
                continue
            topic_id = topic.get("topic_id")
            if isinstance(topic_id, int) and topic_id in self.skip_topic_ids:
                continue
            transcript = _render_transcript(
                topic, min_post_chars=self.min_post_chars,
            )
            tokens = _tokenize(f"{topic.get('title') or ''} {transcript}")
            if not tokens:
                continue
            topics.append(topic)
            transcripts.append(transcript)
            corpora.append(tokens)

        self.topics = topics
        self.transcripts = transcripts
        self.bm25 = BM25Okapi(corpora) if corpora else None
        self.fetched_at = time.time()

    # ---- search --------------------------------------------------------

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
            range(len(scores)),
            key=lambda i: float(scores[i]),
            reverse=True,
        )
        results: list[dict[str, Any]] = []
        for idx in order:
            score = float(scores[idx])
            if score <= 0:
                break
            topic = self.topics[idx]
            transcript = self.transcripts[idx]
            topic_id = topic.get("topic_id")
            results.append({
                "title": topic.get("title") or f"topic {topic_id}",
                "url": self._topic_url(topic),
                "path": str(topic_id) if topic_id is not None else "",
                "section": "",
                "score": round(score, 3),
                "snippet": _make_snippet(transcript, tokens),
            })
            if len(results) >= limit:
                break
        return results

    # ---- fetch_doc accessor -------------------------------------------

    def get_topic(
        self,
        id_or_url: str,
        *,
        post: str | int | None = None,
    ) -> dict[str, Any] | None:
        """Look up a topic by id (``"142937"``) or by its URL.

        Args:
            id_or_url: Either the bare topic id as a string, an integer,
                or a Discourse URL (with or without a per-post suffix).
            post: When ``None``, returns the whole-thread transcript.
                When an integer or the literal string ``"accepted"``,
                returns just that post's body and a per-post URL.

        Returns ``{title, body, url, post_number}`` or ``None``.
        """
        topic = self._lookup_topic(id_or_url)
        if topic is None:
            return None
        title = topic.get("title") or ""
        if post is None:
            transcript = self.transcripts[self.topics.index(topic)]
            return {
                "title": title,
                "body": transcript,
                "url": self._topic_url(topic),
                "post_number": None,
            }
        # Single-post fetch.
        target = self._resolve_post_selector(topic, post)
        if target is None:
            return None
        is_accepted = bool(
            target.get("is_accepted_answer")
            or target.get("post_number")
            == topic.get("accepted_answer_post_number"),
        )
        body_lines = [
            f"TOPIC: {title}",
            f"URL: {self._post_url(topic, target['post_number'])}",
            "=" * 60,
            "",
            _format_post_header(target, is_accepted=is_accepted),
            (target.get("text") or "").strip(),
        ]
        return {
            "title": title,
            "body": "\n".join(body_lines) + "\n",
            "url": self._post_url(topic, target["post_number"]),
            "post_number": target["post_number"],
        }

    # ---- internals -----------------------------------------------------

    def _lookup_topic(self, id_or_url: str | int) -> dict[str, Any] | None:
        """Resolve ``id_or_url`` to a topic dict."""
        if isinstance(id_or_url, int):
            wanted = id_or_url
        else:
            raw = id_or_url.strip()
            if not raw:
                return None
            # Pull the numeric id out of /t/<id> or /t/<id>/<post>.
            m = re.search(r"/t/(\d+)", raw)
            if m:
                wanted = int(m.group(1))
            else:
                try:
                    wanted = int(raw)
                except ValueError:
                    return None
        for topic in self.topics:
            if topic.get("topic_id") == wanted:
                return topic
        return None

    def _resolve_post_selector(
        self,
        topic: dict[str, Any],
        selector: str | int,
    ) -> dict[str, Any] | None:
        """Resolve ``selector`` to one post within ``topic``.

        ``selector`` is either an int post number, the literal string
        ``"accepted"``, or a numeric string. Returns the post dict or
        ``None`` if no match.
        """
        posts = topic.get("posts") or []
        if isinstance(selector, str):
            sel = selector.strip().lower()
            if sel == "accepted":
                accepted_n = topic.get("accepted_answer_post_number")
                if accepted_n is None:
                    return None
                for p in posts:
                    if p.get("post_number") == accepted_n:
                        return p
                return None
            try:
                n = int(sel)
            except ValueError:
                return None
        else:
            n = int(selector)
        for p in posts:
            if p.get("post_number") == n:
                return p
        return None
