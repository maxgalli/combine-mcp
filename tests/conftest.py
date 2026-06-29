from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from combine_mcp.config import DocSource
from combine_mcp.tools._index import DocsIndex


@pytest.fixture
def mock_http() -> MagicMock:
    """Stand-in for the shared httpx.AsyncClient.

    ``get`` is an AsyncMock so tests can drive responses with
    ``return_value`` or ``side_effect``.
    """
    http = MagicMock()
    http.get = AsyncMock()
    return http


@pytest.fixture
def make_response() -> Any:
    """Build a MagicMock that mimics httpx.Response.

    Tests pass ``status``, ``json_data``, and/or ``text``. Non-2xx codes
    raise httpx.HTTPStatusError on raise_for_status().
    """
    def _make(
        status: int = 200,
        json_data: Any = None,
        text: str = "",
    ) -> MagicMock:
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = status
        resp.json = MagicMock(return_value=json_data)
        resp.text = text
        if status >= 400:
            err = httpx.HTTPStatusError(
                f"{status}", request=MagicMock(), response=resp,
            )
            resp.raise_for_status = MagicMock(side_effect=err)
        else:
            resp.raise_for_status = MagicMock()
        return resp
    return _make


@pytest.fixture
def empty_index() -> DocsIndex:
    """A DocsIndex bound to combine-docs, with no docs loaded yet."""
    return DocsIndex(
        search_index_url=(
            "https://cms-analysis.github.io/HiggsAnalysis-CombinedLimit"
            "/latest/search/search_index.json"
        ),
    )


@pytest.fixture
def sample_sources() -> dict[str, DocSource]:
    """Two GitHub-backed sources used by the tool tests.

    ``combine-docs`` is the real default; ``synthetic`` is a second
    source so multi-source routing tests don't depend on a single ID.
    """
    return {
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
            default_branch="main",
        ),
        "synthetic": DocSource(
            id="synthetic",
            name="Synthetic Test Source",
            search_index_url="https://example.test/search/search_index.json",
            repo_url="https://github.com/example/test-docs",
            docs_site_url="https://example.test",
            vcs_provider="github",
            default_branch="main",
        ),
    }


@pytest.fixture
def sample_indices(
    sample_sources: dict[str, DocSource],
) -> dict[str, DocsIndex]:
    """One empty index per sample source, keyed by source id."""
    return {
        sid: DocsIndex(search_index_url=src.search_index_url)
        for sid, src in sample_sources.items()
    }


@pytest.fixture
def mock_ctx(
    mock_http: MagicMock,
    sample_sources: dict[str, DocSource],
    sample_indices: dict[str, DocsIndex],
) -> MagicMock:
    """Mock FastMCP Context with the multi-source lifespan dict."""
    ctx: MagicMock = MagicMock()
    ctx.request_context.lifespan_context = {
        "http": mock_http,
        "indices": sample_indices,
        "sources": sample_sources,
    }
    return ctx


def pytest_addoption(parser: Any) -> None:
    parser.addoption(
        "--runslow", action="store_true", default=False, help="run slow tests",
    )


def pytest_collection_modifyitems(config: Any, items: Any) -> None:
    if not config.getoption("--runslow"):
        skip_slow = pytest.mark.skip(reason="need --runslow option to run")
        for item in items:
            if "slow" in item.keywords:
                item.add_marker(skip_slow)


def capture_tools(register_func: Any) -> dict[str, Any]:
    """Capture tools registered by a register() function for direct calling.

    The MCP decorator is replaced with a no-op that just stashes the
    function in a dict by ``__name__``.
    """
    mcp = MagicMock()
    tools: dict[str, Any] = {}

    def capture_tool() -> Any:
        def decorator(func: Any) -> Any:
            tools[func.__name__] = func
            return func
        return decorator

    mcp.tool = capture_tool
    register_func(mcp)
    return tools
