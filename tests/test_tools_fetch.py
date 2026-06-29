"""Tests for ``fetch_doc``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from combine_mcp.config import DocSource
from combine_mcp.tools._paper_index import PaperIndex
from combine_mcp.tools.fetch import (
    _build_raw_file_url,
    _candidate_source_paths,
    _extract_section,
    _make_outline,
    _rendered_url,
    register,
)
from tests.conftest import capture_tools

SAMPLE_MD = """# Running on the grid

Submit jobs with prun.

## Build

Compile your work area first.

### Tags

Use a release tag.

## Submit

Then submit the job.
"""


class TestCandidatePaths:
    SITE = "https://cms-analysis.github.io/HiggsAnalysis-CombinedLimit/latest"

    def test_url_with_trailing_slash(self) -> None:
        out = _candidate_source_paths(
            self.SITE + "/part3/runningthetool/", self.SITE,
        )
        assert out == [
            "docs/part3/runningthetool/index.md",
            "docs/part3/runningthetool.md",
        ]

    def test_relative_path_with_trailing_slash(self) -> None:
        assert _candidate_source_paths(
            "part3/runningthetool/", self.SITE,
        ) == [
            "docs/part3/runningthetool/index.md",
            "docs/part3/runningthetool.md",
        ]

    def test_relative_path_without_trailing_slash(self) -> None:
        assert _candidate_source_paths(
            "part3/runningthetool", self.SITE,
        ) == [
            "docs/part3/runningthetool/index.md",
            "docs/part3/runningthetool.md",
        ]

    def test_md_path_passthrough(self) -> None:
        assert _candidate_source_paths(
            "part3/runningthetool.md", self.SITE,
        ) == ["docs/part3/runningthetool.md"]

    def test_strips_existing_docs_prefix(self) -> None:
        assert _candidate_source_paths(
            "docs/part3/runningthetool.md", self.SITE,
        ) == ["docs/part3/runningthetool.md"]

    def test_root_url(self) -> None:
        assert _candidate_source_paths(
            self.SITE + "/", self.SITE,
        ) == ["docs/index.md"]

    def test_root_url_without_trailing_slash(self) -> None:
        assert _candidate_source_paths(self.SITE, self.SITE) == ["docs/index.md"]

    def test_empty(self) -> None:
        assert _candidate_source_paths("", self.SITE) == []
        assert _candidate_source_paths("   ", self.SITE) == []

    def test_drops_fragment(self) -> None:
        assert _candidate_source_paths(
            self.SITE + "/part3/runningthetool/#options", self.SITE,
        ) == [
            "docs/part3/runningthetool/index.md",
            "docs/part3/runningthetool.md",
        ]

    def test_drops_query(self) -> None:
        assert _candidate_source_paths(
            "part3/runningthetool/?foo=bar", self.SITE,
        ) == [
            "docs/part3/runningthetool/index.md",
            "docs/part3/runningthetool.md",
        ]

    def test_works_without_docs_site_url_for_simple_paths(self) -> None:
        """Relative paths don't need the site URL for stripping."""
        assert _candidate_source_paths("part3/intro.md") == [
            "docs/part3/intro.md",
        ]


class TestRenderedUrl:
    SITE = "https://cms-analysis.github.io/HiggsAnalysis-CombinedLimit/latest"

    def test_index_md(self) -> None:
        assert (
            _rendered_url(self.SITE, "docs/part3/runningthetool/index.md")
            == self.SITE + "/part3/runningthetool/"
        )

    def test_plain_md(self) -> None:
        assert (
            _rendered_url(self.SITE, "docs/part3/runningthetool.md")
            == self.SITE + "/part3/runningthetool/"
        )

    def test_root_index_md(self) -> None:
        assert _rendered_url(self.SITE, "docs/index.md") == self.SITE + "/"


class TestMakeOutline:
    def test_extracts_levels_one_to_three(self) -> None:
        outline = _make_outline(SAMPLE_MD)
        levels = {(h["level"], h["heading"]) for h in outline}
        assert (1, "Running on the grid") in levels
        assert (2, "Build") in levels
        assert (2, "Submit") in levels
        assert (3, "Tags") in levels


class TestExtractSection:
    def test_extracts_named_section(self) -> None:
        body = _extract_section(SAMPLE_MD, "Build")
        assert body.startswith("## Build")
        assert "Compile your work area first." in body
        assert "Then submit the job." not in body  # next H2 truncates

    def test_case_insensitive(self) -> None:
        body = _extract_section(SAMPLE_MD, "build")
        assert body.startswith("## Build")

    def test_missing_returns_empty(self) -> None:
        assert _extract_section(SAMPLE_MD, "Doesn't Exist") == ""


class TestFetchTool:
    async def test_returns_full_markdown_by_default(
        self,
        mock_ctx: MagicMock,
        mock_http: MagicMock,
        make_response: Any,
    ) -> None:
        mock_http.get.return_value = make_response(text=SAMPLE_MD)
        tools = capture_tools(register)

        result = await tools["fetch_doc"](
            "part3/runningthetool/", ctx=mock_ctx,
        )
        data = json.loads(result)
        assert data["mode"] == "markdown"
        assert data["source"] == "combine-docs"
        assert "Submit jobs with prun" in data["content"]
        assert data["url"].endswith("/part3/runningthetool/")
        assert data["source_path"] == "docs/part3/runningthetool/index.md"

    async def test_unknown_source_returns_recovery(
        self,
        mock_ctx: MagicMock,
    ) -> None:
        tools = capture_tools(register)
        result = await tools["fetch_doc"](
            "part3/runningthetool/", source="bogus", ctx=mock_ctx,
        )
        assert "Recovery steps" in result
        assert "combine-docs" in result

    async def test_falls_through_404_to_alternate_candidate(
        self,
        mock_ctx: MagicMock,
        mock_http: MagicMock,
        make_response: Any,
    ) -> None:
        mock_http.get.side_effect = [
            make_response(status=404),
            make_response(text=SAMPLE_MD),
        ]
        tools = capture_tools(register)

        result = await tools["fetch_doc"](
            "part3/runningthetool/", ctx=mock_ctx,
        )
        data = json.loads(result)
        assert data["source_path"] == "docs/part3/runningthetool.md"
        assert mock_http.get.call_count == 2

    async def test_outline_mode_returns_headings_only(
        self,
        mock_ctx: MagicMock,
        mock_http: MagicMock,
        make_response: Any,
    ) -> None:
        mock_http.get.return_value = make_response(text=SAMPLE_MD)
        tools = capture_tools(register)

        result = await tools["fetch_doc"](
            "part3/runningthetool/", mode="outline", ctx=mock_ctx,
        )
        data = json.loads(result)
        assert data["mode"] == "outline"
        levels = {(h["level"], h["heading"]) for h in data["outline"]}
        assert (1, "Running on the grid") in levels
        assert (2, "Build") in levels
        assert (2, "Submit") in levels
        assert (3, "Tags") in levels

    async def test_section_mode_extracts_one_section(
        self,
        mock_ctx: MagicMock,
        mock_http: MagicMock,
        make_response: Any,
    ) -> None:
        mock_http.get.return_value = make_response(text=SAMPLE_MD)
        tools = capture_tools(register)

        result = await tools["fetch_doc"](
            "part3/runningthetool/", mode="sections:Build", ctx=mock_ctx,
        )
        data = json.loads(result)
        assert data["found"] is True
        assert "Compile your work area" in data["content"]
        assert "Then submit the job" not in data["content"]

    async def test_section_mode_missing_heading_reports_not_found(
        self,
        mock_ctx: MagicMock,
        mock_http: MagicMock,
        make_response: Any,
    ) -> None:
        mock_http.get.return_value = make_response(text=SAMPLE_MD)
        tools = capture_tools(register)

        result = await tools["fetch_doc"](
            "part3/runningthetool/", mode="sections:Nonexistent", ctx=mock_ctx,
        )
        data = json.loads(result)
        assert data["found"] is False
        assert data["content"] == ""

    async def test_all_404_returns_recovery_pointing_at_search(
        self,
        mock_ctx: MagicMock,
        mock_http: MagicMock,
        make_response: Any,
    ) -> None:
        mock_http.get.return_value = make_response(status=404)
        tools = capture_tools(register)

        result = await tools["fetch_doc"](
            "nope/nada/", ctx=mock_ctx,
        )
        assert "Recovery steps" in result
        assert "search_docs" in result

    async def test_empty_input_returns_recovery(
        self,
        mock_ctx: MagicMock,
    ) -> None:
        tools = capture_tools(register)
        result = await tools["fetch_doc"]("", ctx=mock_ctx)
        assert "Could not derive" in result
        assert "Recovery steps" in result


class TestBuildRawFileUrl:
    """Unit tests for the github URL-builder helper."""

    def _src(self) -> DocSource:
        return DocSource(
            id="combine-docs",
            name="CMS Combine",
            search_index_url=(
                "https://cms-analysis.github.io/HiggsAnalysis-CombinedLimit"
                "/latest/search/search_index.json"
            ),
            repo_url=(
                "https://github.com/cms-analysis/HiggsAnalysis-CombinedLimit"
            ),
            docs_site_url=(
                "https://cms-analysis.github.io/HiggsAnalysis-CombinedLimit/latest"
            ),
            vcs_provider="github",
            default_branch="main",
        )

    def test_github_raw_url_shape(self) -> None:
        assert _build_raw_file_url(
            self._src(), "docs/part3/runningthetool.md",
        ) == (
            "https://raw.githubusercontent.com/"
            "cms-analysis/HiggsAnalysis-CombinedLimit/main/"
            "docs/part3/runningthetool.md"
        )


class TestFetchToolUrlConstruction:
    """End-to-end: fetch_doc hits the right raw.githubusercontent URL."""

    async def test_calls_github_raw_url_no_params(
        self,
        mock_ctx: MagicMock,
        mock_http: MagicMock,
        make_response: Any,
    ) -> None:
        mock_http.get.return_value = make_response(text=SAMPLE_MD)
        tools = capture_tools(register)

        await tools["fetch_doc"](
            "part3/runningthetool.md", ctx=mock_ctx,
        )
        called_url = mock_http.get.call_args.args[0]
        assert called_url == (
            "https://raw.githubusercontent.com/"
            "cms-analysis/HiggsAnalysis-CombinedLimit/main/"
            "docs/part3/runningthetool.md"
        )
        # GitHub bakes the ref into the URL path, so no query params.
        assert mock_http.get.call_args.kwargs == {}

    async def test_routes_to_correct_source(
        self,
        mock_ctx: MagicMock,
        mock_http: MagicMock,
        make_response: Any,
    ) -> None:
        """source='synthetic' should hit example/test-docs, not Combine."""
        mock_http.get.return_value = make_response(text=SAMPLE_MD)
        tools = capture_tools(register)

        await tools["fetch_doc"](
            "intro.md", source="synthetic", ctx=mock_ctx,
        )
        called_url = mock_http.get.call_args.args[0]
        assert called_url == (
            "https://raw.githubusercontent.com/example/test-docs/main/docs/intro.md"
        )


# ---------------------------------------------------------------------------
# fetch_doc against a local-paper source
# ---------------------------------------------------------------------------

_TINY_PAPER = """\
Preamble paragraph that introduces the paper. It must be long enough to
survive the minimum-section-size filter, so here is a second sentence
to comfortably cross the threshold.

The statistical model

The primary task of Combine is to produce a statistical model that
encodes the probability density of the data parameterized by the model
parameters. The parameters are split into parameters of interest and
nuisance parameters.

4.1

Counting analyses

A counting analysis has only one primary observable, namely the total
event count in a single channel. The primary observable is the integer
count of events selected by the analysis.
"""


class TestFetchToolOnLocalPaperSource:
    """End-to-end: fetch_doc routes local-paper through PaperIndex."""

    def _make_paper_ctx(
        self,
        mock_http: MagicMock,
        tmp_path: Path,
    ) -> MagicMock:
        paper_file = tmp_path / "paper.txt"
        paper_file.write_text(_TINY_PAPER, encoding="utf-8")
        src = DocSource(
            id="combine-paper",
            name="CMS Combine — Paper",
            repo_url="https://arxiv.org/abs/2404.06614",
            docs_site_url="https://arxiv.org/abs/2404.06614v2",
            source_type="local-paper",
            local_path=str(paper_file),
        )
        sources = {"combine-paper": src}
        indices = {
            "combine-paper": PaperIndex(
                local_path=paper_file,
                docs_site_url=src.docs_site_url,
            ),
        }
        ctx: MagicMock = MagicMock()
        ctx.request_context.lifespan_context = {
            "http": mock_http,
            "indices": indices,
            "sources": sources,
        }
        return ctx

    async def test_fetch_paper_section_by_id(
        self,
        mock_http: MagicMock,
        tmp_path: Path,
    ) -> None:
        ctx = self._make_paper_ctx(mock_http, tmp_path)
        tools = capture_tools(register)

        result = await tools["fetch_doc"](
            "the-statistical-model", source="combine-paper", ctx=ctx,
        )
        data = json.loads(result)
        assert data["source"] == "combine-paper"
        assert data["source_path"] == "the-statistical-model"
        assert data["url"].endswith("#sec-the-statistical-model")
        assert "primary task of Combine" in data["content"]
        # Critically, the paper backend does NOT hit the HTTP client.
        mock_http.get.assert_not_called()

    async def test_fetch_paper_section_by_url(
        self,
        mock_http: MagicMock,
        tmp_path: Path,
    ) -> None:
        ctx = self._make_paper_ctx(mock_http, tmp_path)
        tools = capture_tools(register)

        result = await tools["fetch_doc"](
            "https://arxiv.org/abs/2404.06614v2#sec-4-1",
            source="combine-paper",
            ctx=ctx,
        )
        data = json.loads(result)
        assert data["source_path"] == "4-1"
        assert data["title"] if "title" in data else True  # mode=markdown shape
        assert "counting analysis" in data["content"].lower()

    async def test_fetch_paper_outline_mode(
        self,
        mock_http: MagicMock,
        tmp_path: Path,
    ) -> None:
        ctx = self._make_paper_ctx(mock_http, tmp_path)
        tools = capture_tools(register)

        result = await tools["fetch_doc"](
            "4-1", source="combine-paper", mode="outline", ctx=ctx,
        )
        data = json.loads(result)
        # The outline of a plain-text section just sees a single heading
        # line at the top of the body ("4.1 Counting analyses"), which
        # _make_outline won't pick up because there's no '#' marker. The
        # interesting behaviour: mode is respected, not an error.
        assert data["mode"] == "outline"
        assert "outline" in data

    async def test_unknown_section_returns_recovery(
        self,
        mock_http: MagicMock,
        tmp_path: Path,
    ) -> None:
        ctx = self._make_paper_ctx(mock_http, tmp_path)
        tools = capture_tools(register)

        result = await tools["fetch_doc"](
            "nonexistent-section", source="combine-paper", ctx=ctx,
        )
        assert "Recovery steps" in result
        assert "search_docs" in result
        assert "First 10 known section ids" in result
