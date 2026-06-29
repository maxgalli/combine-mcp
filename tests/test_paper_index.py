"""Tests for the local-paper BM25 backend."""

from __future__ import annotations

from pathlib import Path

import pytest

from combine_mcp.tools._paper_index import (
    PaperIndex,
    _find_headings,
    _is_plausible_number,
    _slugify,
    _split_into_sections,
)

# A small fixture paper that exercises all three heading shapes:
#  - Inline numbered:  "4.2.1 Template-based shape analyses"
#  - Split numbered:   "4.1" / blank / "Counting analyses"
#  - Plain chapter:    "Installation" surrounded by blanks
SAMPLE_PAPER = """\
Preamble paragraph that introduces the paper. It must be long enough to
survive the minimum-section-size filter, so here is a second sentence
with some extra text so we cross the threshold comfortably.

Installation

The Combine package depends on ROOT and RooFit and a few additional
libraries such as the GNU scientific library GSL and EIGEN. The package
may be compiled either within a CMSSW environment or as a standalone
package.

The statistical model

The primary task of Combine is to produce a statistical model that
encodes the probability density of the data parameterized by the model
parameters. The parameters are split into parameters of interest and
nuisance parameters.

4.1

Counting analyses

A counting analysis is one for which the statistical model has only one
primary observable, namely the total event count in a single channel.
The primary observable is the integer count of events selected by the
analysis selection.

4.2

Shape analyses

For shape analyses, the statistical model is factorised into shape and
normalisation terms that provide significant gains in computation time.
This section introduces both template-based and parametric shape
analyses.

4.2.1 Template-based shape analyses

Template-based shape analyses model each process with a histogram of
the discriminating observable. Systematic shape uncertainties are
introduced by interpolating between alternate-template histograms.

0.25 CLs

This is a false-positive numbered heading from a figure caption and
must NOT be treated as a section.
"""


# ---------------------------------------------------------------------------
# Heading detection
# ---------------------------------------------------------------------------


class TestIsPlausibleNumber:
    def test_one_dot_one(self) -> None:
        assert _is_plausible_number("4.1")

    def test_three_components(self) -> None:
        assert _is_plausible_number("4.2.1")

    def test_zero_leading_rejected(self) -> None:
        assert not _is_plausible_number("0.25")

    def test_excessive_depth_rejected(self) -> None:
        assert not _is_plausible_number("1.2.3.4.5")

    def test_non_numeric_rejected(self) -> None:
        assert not _is_plausible_number("abc")


class TestSlugify:
    def test_basic(self) -> None:
        assert _slugify("The statistical model") == "the-statistical-model"

    def test_strips_punctuation(self) -> None:
        assert _slugify("Build & Tools") == "build-tools"


class TestFindHeadings:
    def test_finds_inline_numbered(self) -> None:
        ids = [h[1] for h in _find_headings(SAMPLE_PAPER)]
        assert "4-2-1" in ids

    def test_finds_split_numbered(self) -> None:
        ids = [h[1] for h in _find_headings(SAMPLE_PAPER)]
        assert "4-1" in ids
        assert "4-2" in ids

    def test_finds_plain_chapters(self) -> None:
        ids = [h[1] for h in _find_headings(SAMPLE_PAPER)]
        assert "installation" in ids
        assert "the-statistical-model" in ids

    def test_rejects_figure_caption_number(self) -> None:
        ids = [h[1] for h in _find_headings(SAMPLE_PAPER)]
        assert "0-25" not in ids

    def test_returns_sorted_by_offset(self) -> None:
        headings = _find_headings(SAMPLE_PAPER)
        offsets = [h[0] for h in headings]
        assert offsets == sorted(offsets)


class TestSplitIntoSections:
    def test_preamble_captured(self) -> None:
        sections = _split_into_sections(SAMPLE_PAPER)
        ids = [s["id"] for s in sections]
        assert "preamble" in ids
        preamble = next(s for s in sections if s["id"] == "preamble")
        assert "Preamble paragraph" in preamble["body"]

    def test_section_bodies_are_disjoint(self) -> None:
        sections = _split_into_sections(SAMPLE_PAPER)
        body_4_1 = next(s for s in sections if s["id"] == "4-1")["body"]
        body_4_2 = next(s for s in sections if s["id"] == "4-2")["body"]
        # Each body contains its own heading and excludes the other.
        assert "Counting analyses" in body_4_1
        assert "Shape analyses" not in body_4_1
        assert "Shape analyses" in body_4_2
        assert "Counting analyses" not in body_4_2

    def test_titles_preserve_numbering(self) -> None:
        sections = _split_into_sections(SAMPLE_PAPER)
        s_4_1 = next(s for s in sections if s["id"] == "4-1")
        assert s_4_1["title"].startswith("4.1 ")


# ---------------------------------------------------------------------------
# PaperIndex
# ---------------------------------------------------------------------------


@pytest.fixture
def paper_file(tmp_path: Path) -> Path:
    p = tmp_path / "paper.txt"
    p.write_text(SAMPLE_PAPER, encoding="utf-8")
    return p


@pytest.fixture
def paper_index(paper_file: Path) -> PaperIndex:
    return PaperIndex(
        local_path=paper_file,
        docs_site_url="https://arxiv.org/abs/2404.06614v2",
    )


class TestPaperIndex:
    async def test_ensure_fresh_loads_sections(
        self, paper_index: PaperIndex,
    ) -> None:
        await paper_index.ensure_fresh()
        assert paper_index.is_loaded
        ids = {s["id"] for s in paper_index.sections}
        assert {"installation", "the-statistical-model", "4-1", "4-2", "4-2-1"} <= ids

    async def test_search_returns_expected_section(
        self, paper_index: PaperIndex,
    ) -> None:
        await paper_index.ensure_fresh()
        hits = paper_index.search(
            "nuisance parameters statistical model", limit=5,
        )
        assert hits
        # The statistical-model section should rank top for that query.
        assert hits[0]["path"] == "the-statistical-model"
        assert "arxiv.org" in hits[0]["url"]
        assert "#sec-the-statistical-model" in hits[0]["url"]
        assert hits[0]["snippet"]

    async def test_search_empty_query_returns_empty(
        self, paper_index: PaperIndex,
    ) -> None:
        await paper_index.ensure_fresh()
        assert paper_index.search("", limit=5) == []

    async def test_search_no_match_returns_empty(
        self, paper_index: PaperIndex,
    ) -> None:
        await paper_index.ensure_fresh()
        assert paper_index.search("xylophone gibberish", limit=5) == []

    async def test_get_section_by_bare_id(
        self, paper_index: PaperIndex,
    ) -> None:
        await paper_index.ensure_fresh()
        sec = paper_index.get_section("4-2-1")
        assert sec is not None
        assert sec["title"].startswith("4.2.1")
        assert "Template-based" in sec["body"]

    async def test_get_section_by_url_with_anchor(
        self, paper_index: PaperIndex,
    ) -> None:
        await paper_index.ensure_fresh()
        sec = paper_index.get_section(
            "https://arxiv.org/abs/2404.06614v2#sec-installation",
        )
        assert sec is not None
        assert sec["title"] == "Installation"

    async def test_get_section_unknown_returns_none(
        self, paper_index: PaperIndex,
    ) -> None:
        await paper_index.ensure_fresh()
        assert paper_index.get_section("nonexistent") is None

    async def test_mtime_triggers_reload(
        self,
        paper_index: PaperIndex,
        paper_file: Path,
    ) -> None:
        import os

        await paper_index.ensure_fresh()
        initial_ids = {s["id"] for s in paper_index.sections}

        # Edit the file and bump the mtime forward so `is_stale` flips.
        # The new chapter title must start with at least two letters
        # (real paper headings always do; matches the detector regex).
        new_text = SAMPLE_PAPER + "\n\nBrand New Chapter\n\n" + (
            "Some content for the new chapter. " * 4
        )
        paper_file.write_text(new_text, encoding="utf-8")
        future_mtime = paper_index._loaded_mtime + 10  # noqa: SLF001
        os.utime(paper_file, (future_mtime, future_mtime))

        await paper_index.ensure_fresh()
        new_ids = {s["id"] for s in paper_index.sections}
        assert new_ids != initial_ids
        assert "brand-new-chapter" in new_ids
