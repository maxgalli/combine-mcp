"""Tests for the local-forum BM25 backend."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from combine_mcp.tools._forum_index import (
    ForumIndex,
    _DEFAULT_MIN_POST_CHARS,
    _parse_post_selector,
    _render_transcript,
)


# ---------------------------------------------------------------------------
# Fixtures: tiny Discourse-shaped JSONs
# ---------------------------------------------------------------------------


def _topic(
    *,
    topic_id: int,
    title: str,
    accepted: int | None,
    posts: list[dict],
) -> dict:
    return {
        "topic_id": topic_id,
        "title": title,
        "url": f"https://cms-talk.web.cern.ch/t/{topic_id}",
        "category_id": 279,
        "created_at": "2024-01-01T00:00:00Z",
        "last_posted_at": "2024-01-02T00:00:00Z",
        "tags": [],
        "accepted_answer_post_number": accepted,
        "posts": posts,
    }


def _post(
    *,
    n: int,
    user: str,
    text: str,
    is_accepted: bool = False,
) -> dict:
    return {
        "post_number": n,
        "username": user,
        "name": user.title(),
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:00:00Z",
        "reply_to_post_number": None,
        "is_accepted_answer": is_accepted,
        "text": text,
        "cooked_html": f"<p>{text}</p>",
    }


SOLVED_TOPIC = _topic(
    topic_id=142937,
    title="Spikes in regularization δ scans",
    accepted=10,
    posts=[
        _post(
            n=1, user="naislam",
            text=(
                "I observe sharp spikes in the δ scans of the mean global "
                "correlation coefficient. From our study these spikes appear "
                "linked to increased correlations between the POIs."
            ),
        ),
        _post(
            n=2, user="adewit",
            text="Have you checked the Hessian precision?",
        ),
        _post(
            n=10, user="amarini", is_accepted=True,
            text=(
                "Use --robustHesse 1 together with "
                "--cminDefaultMinimizerStrategy 1; this recomputes the "
                "correlation matrix with higher precision and removes the "
                "spikes."
            ),
        ),
    ],
)

UNSOLVED_TOPIC = _topic(
    topic_id=144631,
    title="Question about discrete profiling: NLL for background",
    accepted=None,
    posts=[
        _post(
            n=1, user="anikiten",
            text=(
                "In discrete profiling, what NLL is taken for the B-only "
                "fit when there are multiple background functional forms?"
            ),
        ),
        _post(
            n=2, user="amarini",
            text=(
                "The envelope is rebuilt at each value of r. The function "
                "that minimises at the S+B best-fit is in general "
                "different from the one at r=0."
            ),
        ),
    ],
)

SHORT_REPLY_TOPIC = _topic(
    topic_id=99999,
    title="thanks thread",
    accepted=None,
    posts=[
        _post(n=1, user="anon", text="Question with enough text to survive the min-chars filter."),
        _post(n=2, user="someone", text="thanks!"),  # tiny — flagged but kept
    ],
)


@pytest.fixture
def forum_dir(tmp_path: Path) -> Path:
    for topic in (SOLVED_TOPIC, UNSOLVED_TOPIC, SHORT_REPLY_TOPIC):
        (tmp_path / f"topic_{topic['topic_id']}.json").write_text(
            json.dumps(topic), encoding="utf-8",
        )
    # An unrelated file that must not be indexed.
    (tmp_path / "README.md").write_text("not a topic")
    (tmp_path / ".manifest.json").write_text('{"topics": {}}')
    return tmp_path


@pytest.fixture
def forum_index(forum_dir: Path) -> ForumIndex:
    return ForumIndex(local_root=forum_dir)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


class TestParsePostSelector:
    def test_post_colon_int(self) -> None:
        assert _parse_post_selector("post:1") == "1"

    def test_post_colon_accepted(self) -> None:
        assert _parse_post_selector("post:accepted") == "accepted"

    def test_case_insensitive_prefix(self) -> None:
        assert _parse_post_selector("POST:5") == "5"

    def test_markdown_returns_none(self) -> None:
        assert _parse_post_selector("markdown") is None

    def test_outline_returns_none(self) -> None:
        assert _parse_post_selector("outline") is None


class TestRenderTranscript:
    def test_includes_title_and_url(self) -> None:
        out = _render_transcript(
            SOLVED_TOPIC, min_post_chars=_DEFAULT_MIN_POST_CHARS,
        )
        assert "TOPIC: Spikes in regularization δ scans" in out
        assert "https://cms-talk.web.cern.ch/t/142937" in out

    def test_marks_solved_status(self) -> None:
        out_solved = _render_transcript(SOLVED_TOPIC, min_post_chars=50)
        out_unsolved = _render_transcript(UNSOLVED_TOPIC, min_post_chars=50)
        assert "SOLVED: yes (reply 10)" in out_solved
        assert "SOLVED: no" in out_unsolved

    def test_marks_accepted_answer_inline(self) -> None:
        out = _render_transcript(SOLVED_TOPIC, min_post_chars=50)
        assert "REPLY 10 [ACCEPTED ANSWER]" in out
        assert "REPLY 2" in out  # the non-accepted reply

    def test_short_post_is_flagged_not_dropped(self) -> None:
        out = _render_transcript(SHORT_REPLY_TOPIC, min_post_chars=50)
        assert "thanks!" in out
        assert "[short reply]" in out


# ---------------------------------------------------------------------------
# ForumIndex behaviour
# ---------------------------------------------------------------------------


class TestForumIndex:
    async def test_ensure_fresh_loads_expected_topics(
        self, forum_index: ForumIndex,
    ) -> None:
        await forum_index.ensure_fresh()
        ids = {t.get("topic_id") for t in forum_index.topics}
        assert ids == {142937, 144631, 99999}

    async def test_ignores_non_topic_files(
        self, forum_index: ForumIndex,
    ) -> None:
        await forum_index.ensure_fresh()
        # README.md and .manifest.json must NOT have been parsed as topics.
        for topic in forum_index.topics:
            assert isinstance(topic.get("topic_id"), int)

    async def test_skip_topic_ids_drops_blocklisted(
        self, forum_dir: Path,
    ) -> None:
        idx = ForumIndex(
            local_root=forum_dir,
            skip_topic_ids=frozenset({142937}),
        )
        await idx.ensure_fresh()
        ids = {t.get("topic_id") for t in idx.topics}
        assert 142937 not in ids
        assert {144631, 99999} <= ids

    async def test_search_surfaces_solved_thread(
        self, forum_index: ForumIndex,
    ) -> None:
        await forum_index.ensure_fresh()
        hits = forum_index.search(
            "spikes mean global correlation coefficient", limit=5,
        )
        assert hits
        assert hits[0]["path"] == "142937"
        assert hits[0]["url"] == "https://cms-talk.web.cern.ch/t/142937"
        assert hits[0]["snippet"]

    async def test_search_surfaces_unsolved_thread(
        self, forum_index: ForumIndex,
    ) -> None:
        await forum_index.ensure_fresh()
        hits = forum_index.search("discrete profiling envelope NLL", limit=5)
        assert hits
        assert hits[0]["path"] == "144631"

    async def test_get_topic_by_id(
        self, forum_index: ForumIndex,
    ) -> None:
        await forum_index.ensure_fresh()
        result = forum_index.get_topic("142937")
        assert result is not None
        assert result["title"] == "Spikes in regularization δ scans"
        assert "ACCEPTED ANSWER" in result["body"]
        assert result["url"] == "https://cms-talk.web.cern.ch/t/142937"
        assert result["post_number"] is None

    async def test_get_topic_by_url(
        self, forum_index: ForumIndex,
    ) -> None:
        await forum_index.ensure_fresh()
        result = forum_index.get_topic(
            "https://cms-talk.web.cern.ch/t/142937/4",
        )
        assert result is not None
        assert result["url"] == "https://cms-talk.web.cern.ch/t/142937"

    async def test_get_topic_unknown_returns_none(
        self, forum_index: ForumIndex,
    ) -> None:
        await forum_index.ensure_fresh()
        assert forum_index.get_topic("nope") is None
        assert forum_index.get_topic("123456789") is None

    async def test_get_topic_specific_post(
        self, forum_index: ForumIndex,
    ) -> None:
        await forum_index.ensure_fresh()
        result = forum_index.get_topic("142937", post=10)
        assert result is not None
        assert result["post_number"] == 10
        assert "robustHesse" in result["body"]
        assert result["url"].endswith("/142937/10")

    async def test_get_topic_accepted_post(
        self, forum_index: ForumIndex,
    ) -> None:
        await forum_index.ensure_fresh()
        result = forum_index.get_topic("142937", post="accepted")
        assert result is not None
        assert result["post_number"] == 10
        assert "ACCEPTED ANSWER" in result["body"]

    async def test_get_topic_accepted_when_unsolved_returns_none(
        self, forum_index: ForumIndex,
    ) -> None:
        await forum_index.ensure_fresh()
        # 144631 has accepted_answer_post_number = None.
        assert forum_index.get_topic("144631", post="accepted") is None

    async def test_empty_dir_yields_no_topics(self, tmp_path: Path) -> None:
        idx = ForumIndex(local_root=tmp_path)
        await idx.ensure_fresh()
        assert idx.topics == []
        assert idx.search("anything", limit=5) == []

    async def test_missing_dir_raises(self, tmp_path: Path) -> None:
        idx = ForumIndex(local_root=tmp_path / "does-not-exist")
        with pytest.raises(FileNotFoundError):
            await idx.ensure_fresh()


# ---------------------------------------------------------------------------
# config integration
# ---------------------------------------------------------------------------


class TestBundledCombineForumSource:
    """The bundled docs_sources.json should register combine-forum."""

    def test_combine_forum_registered(self) -> None:
        from combine_mcp.config import get_default_sources
        sources = get_default_sources()
        assert "combine-forum" in sources

    def test_combine_forum_points_at_corpora_forum(self) -> None:
        from combine_mcp.config import get_default_sources
        src = get_default_sources()["combine-forum"]
        assert src.source_type == "local-forum"
        assert src.local_root is not None
        assert src.local_root.endswith("corpora/forum")

    def test_local_forum_requires_local_root(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        from combine_mcp.config import load_sources
        p = tmp_path / "s.json"  # type: ignore[attr-defined]
        p.write_text(
            '{"sources":[{"id":"f","name":"F",'
            '"repo_url":"https://example.test",'
            '"docs_site_url":"https://example.test",'
            '"source_type":"local-forum"}]}'
        )
        with pytest.raises(ValueError, match="local_root"):
            load_sources(p)
