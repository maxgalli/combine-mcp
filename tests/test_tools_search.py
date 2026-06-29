"""Tests for ``search_docs``."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

from combine_mcp.tools.search import register
from tests.conftest import capture_tools

# Combine-flavoured payload. Three docs so BM25 IDF stays positive for
# single-doc terms (df < N/2 keeps log() > 0).
SAMPLE_PAYLOAD = {
    "docs": [
        {
            "location": "part3/runningthetool/",
            "title": "Running the tool",
            "text": (
                "Use combine -M FitDiagnostics to run a maximum-likelihood "
                "fit and diagnose nuisance pulls."
            ),
        },
        {
            "location": "part3/debugging/",
            "title": "Debugging fits",
            "text": (
                "Use combineTool.py -M FastScan to scan the NLL for each "
                "parameter individually."
            ),
        },
        {
            "location": "part2/settinguptheanalysis/",
            "title": "Preparing the datacard",
            "text": (
                "Declare a rateParam to let a process rate float freely "
                "during the fit."
            ),
        },
    ],
}

SAMPLE_PAYLOAD_SYNTHETIC = {
    "docs": [
        {
            "location": "intro/",
            "title": "Intro",
            "text": "Welcome to the synthetic test corpus.",
        },
        {
            "location": "tutorial/",
            "title": "Tutorial",
            "text": "A second page to keep BM25 IDF non-degenerate.",
        },
        {
            "location": "advanced/",
            "title": "Advanced",
            "text": "A third page on advanced topics.",
        },
    ],
}


class TestSearchTool:
    async def test_loads_index_and_returns_hits(
        self,
        mock_ctx: MagicMock,
        mock_http: MagicMock,
        make_response: Any,
    ) -> None:
        mock_http.get.return_value = make_response(json_data=SAMPLE_PAYLOAD)
        tools = capture_tools(register)

        result = await tools["search_docs"](
            query="FitDiagnostics", ctx=mock_ctx,
        )
        data = json.loads(result)
        assert data["query"] == "FitDiagnostics"
        assert data["source"] == "combine-docs"  # default
        assert data["returned"] >= 1
        top = data["results"][0]
        assert "runningthetool" in top["url"]
        assert top["snippet"]
        assert data["hint"] is None
        assert data["next_action"] and "fetch_doc" in data["next_action"]

    async def test_unknown_source_returns_recovery_listing_valid_sources(
        self, mock_ctx: MagicMock,
    ) -> None:
        tools = capture_tools(register)
        result = await tools["search_docs"](
            query="anything", source="bogus-not-registered", ctx=mock_ctx,
        )
        assert "Recovery steps" in result
        assert "combine-docs" in result

    async def test_routes_to_correct_source(
        self,
        mock_ctx: MagicMock,
        mock_http: MagicMock,
        make_response: Any,
    ) -> None:
        mock_http.get.return_value = make_response(
            json_data=SAMPLE_PAYLOAD_SYNTHETIC,
        )
        tools = capture_tools(register)

        result = await tools["search_docs"](
            query="synthetic", source="synthetic", ctx=mock_ctx,
        )
        data = json.loads(result)
        assert data["source"] == "synthetic"
        # All hit URLs come from the synthetic source's docs_site.
        for hit in data["results"]:
            assert hit["url"].startswith("https://example.test/")
        # The index downloaded from the synthetic source's payload.
        called_url = mock_http.get.call_args.args[0]
        assert called_url == "https://example.test/search/search_index.json"

    async def test_empty_results_includes_hint(
        self,
        mock_ctx: MagicMock,
        mock_http: MagicMock,
        make_response: Any,
    ) -> None:
        mock_http.get.return_value = make_response(json_data=SAMPLE_PAYLOAD)
        tools = capture_tools(register)

        result = await tools["search_docs"](
            query="zzzzzzz_nomatch", ctx=mock_ctx,
        )
        data = json.loads(result)
        assert data["returned"] == 0
        assert data["hint"]
        assert data["next_action"] is None

    async def test_index_load_failure_returns_recovery(
        self,
        mock_ctx: MagicMock,
        mock_http: MagicMock,
    ) -> None:
        mock_http.get.side_effect = RuntimeError("network down")
        tools = capture_tools(register)

        result = await tools["search_docs"](
            query="FitDiagnostics", ctx=mock_ctx,
        )
        assert "network down" in result
        assert "Recovery steps" in result
        # Recovery message names the affected source's site.
        assert "cms-analysis.github.io" in result

    async def test_limit_clamped(
        self,
        mock_ctx: MagicMock,
        mock_http: MagicMock,
        make_response: Any,
    ) -> None:
        mock_http.get.return_value = make_response(json_data=SAMPLE_PAYLOAD)
        tools = capture_tools(register)

        result = await tools["search_docs"](
            query="FitDiagnostics", limit=500, ctx=mock_ctx,
        )
        data = json.loads(result)
        assert data["limit"] == 25  # _MAX_LIMIT

    async def test_index_loaded_only_once_across_calls_per_source(
        self,
        mock_ctx: MagicMock,
        mock_http: MagicMock,
        make_response: Any,
    ) -> None:
        mock_http.get.return_value = make_response(json_data=SAMPLE_PAYLOAD)
        tools = capture_tools(register)

        await tools["search_docs"](query="FitDiagnostics", ctx=mock_ctx)
        await tools["search_docs"](query="rateParam", ctx=mock_ctx)
        # Same source -> payload downloaded once thanks to DocsIndex TTL.
        assert mock_http.get.call_count == 1

    async def test_each_source_loads_its_own_index(
        self,
        mock_ctx: MagicMock,
        mock_http: MagicMock,
        make_response: Any,
    ) -> None:
        mock_http.get.side_effect = [
            make_response(json_data=SAMPLE_PAYLOAD),
            make_response(json_data=SAMPLE_PAYLOAD_SYNTHETIC),
        ]
        tools = capture_tools(register)

        await tools["search_docs"](
            query="FitDiagnostics", source="combine-docs", ctx=mock_ctx,
        )
        await tools["search_docs"](
            query="synthetic", source="synthetic", ctx=mock_ctx,
        )
        # One fetch per source.
        assert mock_http.get.call_count == 2
        called_urls = {c.args[0] for c in mock_http.get.call_args_list}
        assert called_urls == {
            (
                "https://cms-analysis.github.io/HiggsAnalysis-CombinedLimit"
                "/latest/search/search_index.json"
            ),
            "https://example.test/search/search_index.json",
        }
