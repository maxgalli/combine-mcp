"""Tests for the local-files BM25 backend."""

from __future__ import annotations

from pathlib import Path

import pytest

from combine_mcp.tools._code_index import (
    CodeIndex,
    _is_text_file,
    _outline_of,
    _walk_files,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


PY_FILE_A = '''\
"""PhysicsModel: base class for physics models."""

from __future__ import annotations


class PhysicsModel:
    """Base class. Override doParametersOfInterest in subclasses."""

    def doParametersOfInterest(self) -> None:
        raise NotImplementedError

    def getYieldScale(self, bin: str, process: str) -> float:
        return 1.0
'''

PY_FILE_B = '''\
"""text2workspace utilities."""


def parse_datacard(path: str) -> dict:
    """Parse a Combine datacard."""
    return {}


def build_workspace(card: dict) -> object:
    """Build a RooWorkspace from a parsed datacard."""
    return None
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


@pytest.fixture
def code_tree(tmp_path: Path) -> Path:
    """Build a tiny tree mirroring the Combine layout used by include_globs."""
    (tmp_path / "python").mkdir()
    (tmp_path / "python" / "PhysicsModel.py").write_text(PY_FILE_A)
    (tmp_path / "python" / "text2workspace.py").write_text(PY_FILE_B)

    (tmp_path / "interface").mkdir()
    (tmp_path / "interface" / "AsymptoticLimits.h").write_text(CPP_HEADER)

    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "plotLimits.py").write_text("# tiny plotter\n")
    (tmp_path / "scripts" / "helper.sh").write_text("#!/bin/sh\necho hi\n")

    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "combine.cpp").write_text("int main() { return 0; }\n")

    # Decoy files that must NOT be picked up by the default globs.
    (tmp_path / "README.md").write_text("# combine\n")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "datacard.txt").write_text("imax 1\n")

    return tmp_path


@pytest.fixture
def code_index(code_tree: Path) -> CodeIndex:
    return CodeIndex(
        local_root=code_tree,
        include_globs=[
            "python/**/*.py",
            "scripts/*.py",
            "scripts/*.sh",
            "interface/*.h",
            "bin/*.cpp",
        ],
        docs_site_url="https://example.test/tree/v10.6.0",
        url_template="https://example.test/blob/v10.6.0/{relpath}",
    )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


class TestIsTextFile:
    def test_accepts_python(self, tmp_path: Path) -> None:
        p = tmp_path / "x.py"
        p.write_text("print('hi')\n")
        assert _is_text_file(p)

    def test_rejects_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "x.py"
        p.write_text("")
        assert not _is_text_file(p)

    def test_rejects_binary(self, tmp_path: Path) -> None:
        p = tmp_path / "x.bin"
        p.write_bytes(b"\x00\x01\x02\x03")
        assert not _is_text_file(p)

    def test_rejects_huge(self, tmp_path: Path) -> None:
        p = tmp_path / "x.txt"
        p.write_text("a" * (300 * 1024))
        assert not _is_text_file(p)


class TestWalkFiles:
    def test_collects_globs(self, code_tree: Path) -> None:
        files = _walk_files(
            code_tree,
            ["python/**/*.py", "interface/*.h", "bin/*.cpp"],
        )
        relpaths = {p.relative_to(code_tree).as_posix() for p in files}
        assert relpaths == {
            "python/PhysicsModel.py",
            "python/text2workspace.py",
            "interface/AsymptoticLimits.h",
            "bin/combine.cpp",
        }

    def test_dedups_overlapping_globs(self, code_tree: Path) -> None:
        files = _walk_files(
            code_tree,
            ["python/**/*.py", "python/*.py"],  # overlapping
        )
        relpaths = [p.relative_to(code_tree).as_posix() for p in files]
        assert sorted(relpaths) == [
            "python/PhysicsModel.py",
            "python/text2workspace.py",
        ]

    def test_skips_decoys(self, code_tree: Path) -> None:
        """README.md and data/datacard.txt must not appear under the
        Combine include_globs (they don't match the patterns)."""
        files = _walk_files(
            code_tree,
            ["python/**/*.py", "scripts/*.py", "scripts/*.sh",
             "interface/*.h", "bin/*.cpp"],
        )
        relpaths = {p.relative_to(code_tree).as_posix() for p in files}
        assert "README.md" not in relpaths
        assert "data/datacard.txt" not in relpaths


class TestOutlineOf:
    def test_captures_python_defs(self) -> None:
        names = _outline_of(PY_FILE_B)
        assert "parse_datacard" in names
        assert "build_workspace" in names

    def test_captures_python_classes(self) -> None:
        names = _outline_of(PY_FILE_A)
        assert "PhysicsModel" in names

    def test_captures_cpp_classes(self) -> None:
        names = _outline_of(CPP_HEADER)
        assert "AsymptoticLimits" in names


# ---------------------------------------------------------------------------
# CodeIndex
# ---------------------------------------------------------------------------


class TestCodeIndex:
    async def test_ensure_fresh_loads_expected_files(
        self, code_index: CodeIndex,
    ) -> None:
        await code_index.ensure_fresh()
        relpaths = {d["relpath"] for d in code_index.docs}
        assert relpaths == {
            "python/PhysicsModel.py",
            "python/text2workspace.py",
            "interface/AsymptoticLimits.h",
            "scripts/plotLimits.py",
            "scripts/helper.sh",
            "bin/combine.cpp",
        }

    async def test_search_finds_class_name(
        self, code_index: CodeIndex,
    ) -> None:
        await code_index.ensure_fresh()
        hits = code_index.search("PhysicsModel doParametersOfInterest", limit=5)
        assert hits
        assert hits[0]["path"] == "python/PhysicsModel.py"
        # URL uses the configured template.
        assert hits[0]["url"] == (
            "https://example.test/blob/v10.6.0/python/PhysicsModel.py"
        )

    async def test_search_filename_matches(
        self, code_index: CodeIndex,
    ) -> None:
        await code_index.ensure_fresh()
        hits = code_index.search("text2workspace", limit=5)
        # The filename itself is included in the tokenised corpus, so a
        # query that names the file should rank it high.
        assert hits[0]["path"] == "python/text2workspace.py"

    async def test_search_empty_query_returns_empty(
        self, code_index: CodeIndex,
    ) -> None:
        await code_index.ensure_fresh()
        assert code_index.search("", limit=5) == []

    async def test_get_file_by_relpath(
        self, code_index: CodeIndex,
    ) -> None:
        await code_index.ensure_fresh()
        doc = code_index.get_file("interface/AsymptoticLimits.h")
        assert doc is not None
        assert "class AsymptoticLimits" in doc["body"]
        assert doc["url"] == (
            "https://example.test/blob/v10.6.0/interface/AsymptoticLimits.h"
        )

    async def test_get_file_by_url(
        self, code_index: CodeIndex,
    ) -> None:
        await code_index.ensure_fresh()
        doc = code_index.get_file(
            "https://example.test/blob/v10.6.0/python/text2workspace.py",
        )
        assert doc is not None
        assert doc["relpath"] == "python/text2workspace.py"

    async def test_get_file_unknown_returns_none(
        self, code_index: CodeIndex,
    ) -> None:
        await code_index.ensure_fresh()
        assert code_index.get_file("python/Nonexistent.py") is None

    async def test_missing_local_root_raises(self, tmp_path: Path) -> None:
        idx = CodeIndex(
            local_root=tmp_path / "does-not-exist",
            include_globs=["**/*.py"],
            docs_site_url="https://example.test",
        )
        with pytest.raises(FileNotFoundError):
            await idx.ensure_fresh()
