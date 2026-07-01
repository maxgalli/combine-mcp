"""Tests for the github-tarball BM25 backend."""

from __future__ import annotations

import io
import tarfile
import time
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from combine_mcp.tools._remote_code_index import (
    RemoteCodeIndex,
    _tarball_url,
)

# ---------------------------------------------------------------------------
# Fixtures — synthetic in-memory tarballs
# ---------------------------------------------------------------------------

PY_FILE_A = '''\
"""PhysicsModel: base class for physics models."""

from __future__ import annotations


class PhysicsModel:
    """Base class. Override doParametersOfInterest in subclasses."""

    def doParametersOfInterest(self) -> None:
        raise NotImplementedError
'''

PY_FILE_B = '''\
"""text2workspace utilities."""


def parse_datacard(path: str) -> dict:
    """Parse a Combine datacard."""
    return {}
'''

CPP_HEADER = """\
#pragma once

namespace combine {

class AsymptoticLimits {
public:
    void run();
    double getLimit() const;
};

}  // namespace combine
"""


def _make_tarball(
    files: dict[str, str],
    top_dir: str = "cms-analysis-HiggsAnalysis-CombinedLimit-abc123",
) -> bytes:
    """Build a gzipped tarball in memory mimicking GitHub's codeload shape.

    GitHub archives extract to a single top-level directory named
    ``<owner>-<repo>-<sha>``; this helper reproduces that so the index
    can find the source root the same way it would with the real API.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        top = tarfile.TarInfo(top_dir)
        top.type = tarfile.DIRTYPE
        top.mode = 0o755
        tar.addfile(top)
        for relpath, content in files.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(f"{top_dir}/{relpath}")
            info.size = len(data)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.fixture
def combine_tarball() -> bytes:
    """A minimal tarball with a combine-shaped file layout."""
    return _make_tarball({
        "python/PhysicsModel.py": PY_FILE_A,
        "python/text2workspace.py": PY_FILE_B,
        "interface/AsymptoticLimits.h": CPP_HEADER,
        "scripts/plotLimits.py": "# tiny plotter\n",
        "scripts/helper.sh": "#!/bin/sh\necho hi\n",
        "README.md": "# Not indexed — excluded by include_globs.",
    })


def _make_response(content: bytes, status: int = 200) -> MagicMock:
    """Build a mock httpx.Response with raw bytes ``content``."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.content = content
    if status >= 400:
        err = httpx.HTTPStatusError(
            f"{status}", request=MagicMock(), response=resp,
        )
        resp.raise_for_status = MagicMock(side_effect=err)
    else:
        resp.raise_for_status = MagicMock()
    return resp


@pytest.fixture
def tarball_http(combine_tarball: bytes) -> MagicMock:
    """A mock httpx client whose ``.get`` returns the synthetic tarball."""
    http = MagicMock()
    http.get = AsyncMock(return_value=_make_response(combine_tarball))
    return http


def _new_index() -> RemoteCodeIndex:
    """A default-config RemoteCodeIndex bound to combine-shaped globs."""
    return RemoteCodeIndex(
        repo_url=(
            "https://github.com/cms-analysis/HiggsAnalysis-CombinedLimit"
        ),
        ref="v10.6.0",
        include_globs=[
            "python/**/*.py",
            "scripts/*.py",
            "scripts/*.sh",
            "interface/*.h",
            "bin/*.cpp",
        ],
        docs_site_url=(
            "https://github.com/cms-analysis/HiggsAnalysis-CombinedLimit"
            "/tree/v10.6.0"
        ),
        url_template=(
            "https://github.com/cms-analysis/HiggsAnalysis-CombinedLimit"
            "/blob/{ref}/{relpath}"
        ),
    )


# ---------------------------------------------------------------------------
# _tarball_url
# ---------------------------------------------------------------------------


class TestTarballUrl:
    def test_with_tag(self) -> None:
        assert _tarball_url(
            "https://github.com/foo/bar", "v1.0.0",
        ) == "https://codeload.github.com/foo/bar/tar.gz/v1.0.0"

    def test_with_branch(self) -> None:
        assert _tarball_url(
            "https://github.com/foo/bar", "main",
        ) == "https://codeload.github.com/foo/bar/tar.gz/main"

    def test_strips_dot_git(self) -> None:
        assert _tarball_url(
            "https://github.com/foo/bar.git", "v1.0.0",
        ) == "https://codeload.github.com/foo/bar/tar.gz/v1.0.0"

    def test_rejects_missing_repo_path(self) -> None:
        with pytest.raises(ValueError, match="no owner/repo path"):
            _tarball_url("https://github.com/", "main")


# ---------------------------------------------------------------------------
# ensure_fresh
# ---------------------------------------------------------------------------


class TestEnsureFresh:
    async def test_indexes_expected_files(
        self, tarball_http: MagicMock,
    ) -> None:
        idx = _new_index()
        await idx.ensure_fresh(tarball_http)
        relpaths = {d["relpath"] for d in idx.docs}
        assert relpaths == {
            "python/PhysicsModel.py",
            "python/text2workspace.py",
            "interface/AsymptoticLimits.h",
            "scripts/plotLimits.py",
            "scripts/helper.sh",
        }

    async def test_ignores_files_outside_globs(
        self, tarball_http: MagicMock,
    ) -> None:
        idx = _new_index()
        await idx.ensure_fresh(tarball_http)
        assert "README.md" not in {d["relpath"] for d in idx.docs}

    async def test_calls_codeload_url(
        self, tarball_http: MagicMock,
    ) -> None:
        idx = _new_index()
        await idx.ensure_fresh(tarball_http)
        assert tarball_http.get.call_args.args[0] == (
            "https://codeload.github.com/cms-analysis/"
            "HiggsAnalysis-CombinedLimit/tar.gz/v10.6.0"
        )

    async def test_follows_redirects(
        self, tarball_http: MagicMock,
    ) -> None:
        idx = _new_index()
        await idx.ensure_fresh(tarball_http)
        assert (
            tarball_http.get.call_args.kwargs.get("follow_redirects") is True
        )

    async def test_raises_without_http_client(self) -> None:
        idx = _new_index()
        with pytest.raises(ValueError, match="requires an http client"):
            await idx.ensure_fresh(None)

    async def test_ttl_prevents_second_download(
        self, tarball_http: MagicMock,
    ) -> None:
        idx = _new_index()
        await idx.ensure_fresh(tarball_http)
        assert tarball_http.get.call_count == 1
        await idx.ensure_fresh(tarball_http)
        assert tarball_http.get.call_count == 1

    async def test_reloads_when_stale(
        self, tarball_http: MagicMock,
    ) -> None:
        idx = _new_index()
        await idx.ensure_fresh(tarball_http)
        idx.fetched_at = time.time() - (25 * 3600)  # 25 h -> stale
        await idx.ensure_fresh(tarball_http)
        assert tarball_http.get.call_count == 2

    async def test_rejects_wrong_top_level_shape(self) -> None:
        # Two top-level dirs is malformed for a github tarball.
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for name in ("first-top", "second-top"):
                info = tarfile.TarInfo(name)
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
        http = MagicMock()
        http.get = AsyncMock(
            return_value=_make_response(buf.getvalue()),
        )
        idx = _new_index()
        with pytest.raises(ValueError, match="unexpected tarball structure"):
            await idx.ensure_fresh(http)


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


class TestSearch:
    async def test_surfaces_physics_model(
        self, tarball_http: MagicMock,
    ) -> None:
        idx = _new_index()
        await idx.ensure_fresh(tarball_http)
        hits = idx.search(
            "PhysicsModel doParametersOfInterest", limit=5,
        )
        assert hits
        assert hits[0]["path"] == "python/PhysicsModel.py"

    async def test_no_match_returns_empty(
        self, tarball_http: MagicMock,
    ) -> None:
        idx = _new_index()
        await idx.ensure_fresh(tarball_http)
        assert idx.search("xyzzyabsolutelynothingmatches", limit=5) == []

    async def test_before_ensure_fresh_returns_empty(self) -> None:
        idx = _new_index()
        assert idx.search("anything", limit=5) == []


# ---------------------------------------------------------------------------
# get_file
# ---------------------------------------------------------------------------


class TestGetFile:
    async def test_by_relpath(
        self, tarball_http: MagicMock,
    ) -> None:
        idx = _new_index()
        await idx.ensure_fresh(tarball_http)
        doc = idx.get_file("python/PhysicsModel.py")
        assert doc is not None
        assert doc["relpath"] == "python/PhysicsModel.py"
        assert "class PhysicsModel" in doc["body"]

    async def test_url_includes_pinned_ref(
        self, tarball_http: MagicMock,
    ) -> None:
        idx = _new_index()
        await idx.ensure_fresh(tarball_http)
        doc = idx.get_file("python/PhysicsModel.py")
        assert doc is not None
        assert doc["url"] == (
            "https://github.com/cms-analysis/HiggsAnalysis-CombinedLimit/"
            "blob/v10.6.0/python/PhysicsModel.py"
        )

    async def test_by_url_round_trip(
        self, tarball_http: MagicMock,
    ) -> None:
        idx = _new_index()
        await idx.ensure_fresh(tarball_http)
        url = (
            "https://github.com/cms-analysis/HiggsAnalysis-CombinedLimit/"
            "blob/v10.6.0/interface/AsymptoticLimits.h"
        )
        doc = idx.get_file(url)
        assert doc is not None
        assert doc["relpath"] == "interface/AsymptoticLimits.h"

    async def test_unknown_returns_none(
        self, tarball_http: MagicMock,
    ) -> None:
        idx = _new_index()
        await idx.ensure_fresh(tarball_http)
        assert idx.get_file("nonexistent/path.py") is None


# ---------------------------------------------------------------------------
# url_template shape
# ---------------------------------------------------------------------------


class TestUrlTemplate:
    async def test_ref_substituted(
        self, tarball_http: MagicMock,
    ) -> None:
        idx = _new_index()
        await idx.ensure_fresh(tarball_http)
        assert idx._url_for("foo/bar.py").endswith(  # noqa: SLF001
            "/blob/v10.6.0/foo/bar.py",
        )

    async def test_default_url_without_template(
        self, combine_tarball: bytes,
    ) -> None:
        idx = RemoteCodeIndex(
            repo_url="https://github.com/foo/bar",
            ref="v1.0.0",
            include_globs=["python/*.py"],
            docs_site_url="https://github.com/foo/bar",
            url_template=None,
        )
        http = MagicMock()
        http.get = AsyncMock(return_value=_make_response(combine_tarball))
        await idx.ensure_fresh(http)
        assert idx._url_for("python/foo.py") == (  # noqa: SLF001
            "https://github.com/foo/bar/python/foo.py"
        )


# ---------------------------------------------------------------------------
# is_stale
# ---------------------------------------------------------------------------


class TestIsStale:
    def test_new_index_is_stale(self) -> None:
        assert _new_index().is_stale is True

    async def test_fresh_after_load(
        self, tarball_http: MagicMock,
    ) -> None:
        idx = _new_index()
        await idx.ensure_fresh(tarball_http)
        assert idx.is_stale is False

    async def test_stale_past_ttl(
        self, tarball_http: MagicMock,
    ) -> None:
        idx = _new_index()
        await idx.ensure_fresh(tarball_http)
        idx.fetched_at = time.time() - (25 * 3600)
        assert idx.is_stale is True
