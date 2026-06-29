"""Tests for MCP resource registration."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from combine_mcp.config import DocSource
from combine_mcp.resources import register


def _capture_resource(mcp: MagicMock) -> list[dict[str, Any]]:
    """Replace ``mcp.resource`` with a recorder; return the captured calls."""
    captured: list[dict[str, Any]] = []

    def resource_decorator(
        uri: str,
        *,
        name: str | None = None,
        description: str | None = None,
        mime_type: str | None = None,
    ) -> Any:
        def decorator(func: Any) -> Any:
            captured.append({
                "uri": uri,
                "name": name,
                "description": description,
                "mime_type": mime_type,
                "func": func,
            })
            return func
        return decorator

    mcp.resource = resource_decorator
    return captured


SAMPLE_SOURCES: dict[str, DocSource] = {
    "combine-docs": DocSource(
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
    ),
    "synthetic": DocSource(
        id="synthetic",
        name="Synthetic Test Source",
        search_index_url="https://example.test/search/search_index.json",
        repo_url="https://github.com/example/test-docs",
        docs_site_url="https://example.test",
        vcs_provider="github",
    ),
}


class TestRegister:
    def test_registers_sources_resource(self) -> None:
        mcp = MagicMock()
        captured = _capture_resource(mcp)
        register(mcp, SAMPLE_SOURCES)

        assert len(captured) == 1
        entry = captured[0]
        assert entry["uri"] == "docs://sources"
        assert entry["mime_type"] == "text/markdown"

        body = entry["func"]()
        # Lists every registered source by id and name.
        assert "combine-docs" in body
        assert "CMS Combine" in body
        assert "synthetic" in body
        assert "Synthetic Test Source" in body
        # Includes URLs (Resource Reference pattern - point, don't embed).
        assert "cms-analysis.github.io" in body
        assert "https://example.test" in body

    def test_handles_empty_registry(self) -> None:
        mcp = MagicMock()
        captured = _capture_resource(mcp)
        register(mcp, {})

        assert len(captured) == 1
        body = captured[0]["func"]()
        assert "no sources registered" in body
