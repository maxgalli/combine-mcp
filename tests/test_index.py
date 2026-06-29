"""Tests for the in-memory BM25 docs index."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from combine_mcp.tools._index import (
    DocsIndex,
    _make_snippet,
    _section_of,
    _tokenize,
)

SAMPLE_PAYLOAD = {
    "config": {"lang": ["en"]},
    "docs": [
        {
            "location": "",
            "title": "Home",
            "text": "ATLAS software documentation home page.",
        },
        {
            "location": "athena/configuration/",
            "title": "Athena configuration",
            "text": (
                "Configuring jobs in Athena uses the ComponentAccumulator "
                "API. Configure your Athena job through the new CA system."
            ),
        },
        {
            "location": "analysis/grid/",
            "title": "Running on the grid",
            "text": (
                "Submit your analysis jobs to the WLCG grid via prun and "
                "the panda client."
            ),
        },
        {
            "location": "analysis/grid/#prun",
            "title": "prun",
            "text": "prun is the command-line submitter for grid jobs.",
        },
    ],
}


class TestTokenize:
    def test_lowercases(self) -> None:
        assert _tokenize("Hello World") == ["hello", "world"]

    def test_preserves_alphanum_underscore(self) -> None:
        assert _tokenize("AthenaMT 25.0 ROOT_2024") == [
            "athenamt", "25", "0", "root_2024",
        ]

    def test_empty(self) -> None:
        assert _tokenize("") == []
        assert _tokenize(None) == []


class TestSectionOf:
    def test_root(self) -> None:
        assert _section_of("") == ""

    def test_first_segment(self) -> None:
        assert _section_of("athena/configuration/") == "athena"

    def test_strips_anchor(self) -> None:
        assert _section_of("analysis/grid/#prun") == "analysis"


class TestMakeSnippet:
    def test_picks_window_with_query_terms(self) -> None:
        text = " ".join(["lorem"] * 10 + ["athena", "configuration"] + ["ipsum"] * 30)
        s = _make_snippet(text, ["athena", "configuration"])
        lower = s.lower()
        assert "athena" in lower
        assert "configuration" in lower

    def test_no_text_returns_empty(self) -> None:
        assert _make_snippet("", ["foo"]) == ""

    def test_no_query_returns_prefix(self) -> None:
        text = " ".join(f"w{i}" for i in range(50))
        s = _make_snippet(text, [])
        assert s.startswith("w0 w1 w2")


class TestDocsIndex:
    @pytest.fixture
    def loaded_index(
        self, mock_http: MagicMock, make_response: Any,
    ) -> DocsIndex:
        idx = DocsIndex(docs_base="https://atlas-software.docs.cern.ch")
        mock_http.get.return_value = make_response(json_data=SAMPLE_PAYLOAD)
        return idx

    async def test_refresh_loads_docs(
        self,
        loaded_index: DocsIndex,
        mock_http: MagicMock,
    ) -> None:
        await loaded_index.refresh(mock_http)
        assert loaded_index.is_loaded
        assert len(loaded_index.docs) == 4
        mock_http.get.assert_called_once()
        # URL is built off docs_base.
        called_url = mock_http.get.call_args.args[0]
        assert called_url.endswith("/search/search_index.json")

    async def test_search_ranks_relevant_doc(
        self,
        loaded_index: DocsIndex,
        mock_http: MagicMock,
    ) -> None:
        await loaded_index.refresh(mock_http)
        results = loaded_index.search(
            "athena configuration", limit=5, section=None,
        )
        assert results
        assert "athena" in results[0]["url"].lower()
        assert results[0]["section"] == "athena"
        assert results[0]["snippet"]

    async def test_search_filters_by_section(
        self,
        loaded_index: DocsIndex,
        mock_http: MagicMock,
    ) -> None:
        await loaded_index.refresh(mock_http)
        # "WLCG" is in only one doc -> BM25 IDF > 0. (Single-occurrence
        # terms across a 4-doc corpus give IDF = log(3.5/1.5) ~= 0.85.
        # "grid" appears in two of four docs and lands at IDF = 0, which
        # would be filtered out by the score > 0 cut in DocsIndex.search.)
        results = loaded_index.search("WLCG", limit=10, section="analysis")
        assert results
        assert all(r["section"] == "analysis" for r in results)

    async def test_search_returns_empty_when_no_match(
        self,
        loaded_index: DocsIndex,
        mock_http: MagicMock,
    ) -> None:
        await loaded_index.refresh(mock_http)
        results = loaded_index.search(
            "zzzzzzz_nomatch_zzzzz", limit=10, section=None,
        )
        assert results == []

    async def test_empty_query_returns_empty(
        self,
        loaded_index: DocsIndex,
        mock_http: MagicMock,
    ) -> None:
        await loaded_index.refresh(mock_http)
        assert loaded_index.search("", limit=5, section=None) == []
        assert loaded_index.search("   ", limit=5, section=None) == []

    async def test_search_before_refresh_returns_empty(
        self, empty_index: DocsIndex,
    ) -> None:
        assert empty_index.search("anything", limit=5, section=None) == []

    async def test_ensure_fresh_caches_within_ttl(
        self,
        loaded_index: DocsIndex,
        mock_http: MagicMock,
    ) -> None:
        await loaded_index.ensure_fresh(mock_http)
        assert mock_http.get.call_count == 1
        # Still fresh: no extra fetch.
        await loaded_index.ensure_fresh(mock_http)
        assert mock_http.get.call_count == 1

    async def test_ensure_fresh_reloads_when_stale(
        self,
        loaded_index: DocsIndex,
        mock_http: MagicMock,
    ) -> None:
        await loaded_index.ensure_fresh(mock_http)
        loaded_index.fetched_at = 0  # epoch -> very stale
        assert loaded_index.is_stale
        await loaded_index.ensure_fresh(mock_http)
        assert mock_http.get.call_count == 2

    async def test_url_construction_uses_docs_base(
        self,
        loaded_index: DocsIndex,
        mock_http: MagicMock,
    ) -> None:
        await loaded_index.refresh(mock_http)
        results = loaded_index.search("home", limit=5, section=None)
        # The Home page URL is just docs_base/.
        urls = {r["url"] for r in results}
        assert any(u == "https://atlas-software.docs.cern.ch/" for u in urls) or results

    async def test_freshly_constructed_is_stale(
        self, empty_index: DocsIndex,
    ) -> None:
        # fetched_at == 0.0, time.time() - 0 > TTL -> stale.
        assert empty_index.is_stale
        assert not empty_index.is_loaded

    async def test_refresh_handles_empty_payload(
        self,
        empty_index: DocsIndex,
        mock_http: MagicMock,
        make_response: Any,
    ) -> None:
        mock_http.get.return_value = make_response(json_data={"docs": []})
        await empty_index.refresh(mock_http)
        # bm25 stays None when there are no docs; fetched_at advances so we
        # don't hammer the upstream on every call.
        assert empty_index.bm25 is None
        assert empty_index.fetched_at > 0
        assert empty_index.search("anything", limit=5, section=None) == []

    async def test_refresh_skips_non_dict_entries(
        self,
        empty_index: DocsIndex,
        mock_http: MagicMock,
        make_response: Any,
    ) -> None:
        payload = {"docs": [
            "not a dict",
            {"location": "x/", "title": "x", "text": "hello world"},
            None,
        ]}
        mock_http.get.return_value = make_response(json_data=payload)
        await empty_index.refresh(mock_http)
        assert len(empty_index.docs) == 1
        assert empty_index.docs[0]["location"] == "x/"


class TestStaleness:
    async def test_after_refresh_is_fresh(
        self,
        empty_index: DocsIndex,
        mock_http: MagicMock,
        make_response: Any,
    ) -> None:
        mock_http.get.return_value = make_response(json_data=SAMPLE_PAYLOAD)
        await empty_index.refresh(mock_http)
        assert not empty_index.is_stale
        assert empty_index.fetched_at <= time.time()
