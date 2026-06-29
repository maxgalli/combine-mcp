"""Tests for config.py: parse_repo_path and source loading."""

from __future__ import annotations

import pytest

from combine_mcp.config import (
    get_default_sources,
    load_sources,
    parse_repo_path,
)

# ---------------------------------------------------------------------------
# parse_repo_path
# ---------------------------------------------------------------------------


class TestParseRepoPath:
    def test_typical_github(self) -> None:
        path = parse_repo_path(
            "https://github.com/cms-analysis/HiggsAnalysis-CombinedLimit",
        )
        assert path == "cms-analysis/HiggsAnalysis-CombinedLimit"

    def test_short_two_segment_path(self) -> None:
        assert parse_repo_path("https://github.com/foo/bar") == "foo/bar"

    def test_strips_trailing_dot_git(self) -> None:
        assert parse_repo_path(
            "https://github.com/foo/bar.git",
        ) == "foo/bar"

    def test_trailing_slash_stripped(self) -> None:
        assert parse_repo_path(
            "https://github.com/foo/bar/",
        ) == "foo/bar"

    def test_no_host_raises(self) -> None:
        with pytest.raises(ValueError, match="no host"):
            parse_repo_path("not-a-url")

    def test_empty_path_raises(self) -> None:
        with pytest.raises(ValueError, match="no path"):
            parse_repo_path("https://github.com/")


# ---------------------------------------------------------------------------
# load_sources
# ---------------------------------------------------------------------------


class TestLoadSourcesSchema:
    def test_mkdocs_default(self, tmp_path: pytest.TempPathFactory) -> None:
        p = tmp_path / "s.json"  # type: ignore[attr-defined]
        p.write_text(
            '{"sources":[{"id":"a","name":"A","repo_url":"https://github.com/x/y",'
            '"docs_site_url":"https://x","search_index_url":"https://x/s.json"}]}'
        )
        sources = load_sources(p)
        assert sources["a"].source_type == "mkdocs"
        assert sources["a"].default_branch == "main"

    def test_non_mkdocs_source_type_rejected(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        p = tmp_path / "s.json"  # type: ignore[attr-defined]
        p.write_text(
            '{"sources":[{"id":"x","name":"X","repo_url":"https://github.com/a/b",'
            '"docs_site_url":"https://x","source_type":"sphinx"}]}'
        )
        with pytest.raises(ValueError, match="source_type"):
            load_sources(p)

    def test_mkdocs_requires_search_index_url(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        p = tmp_path / "s.json"  # type: ignore[attr-defined]
        p.write_text(
            '{"sources":[{"id":"x","name":"X","repo_url":"https://github.com/a/b",'
            '"docs_site_url":"https://x"}]}'
        )
        with pytest.raises(ValueError, match="search_index_url"):
            load_sources(p)


class TestVcsProvider:
    def test_github_is_default(self, tmp_path: pytest.TempPathFactory) -> None:
        p = tmp_path / "s.json"  # type: ignore[attr-defined]
        p.write_text(
            '{"sources":[{"id":"a","name":"A","repo_url":"https://github.com/x/y",'
            '"docs_site_url":"https://x","search_index_url":"https://x/s.json"}]}'
        )
        sources = load_sources(p)
        assert sources["a"].vcs_provider == "github"

    def test_unknown_vcs_provider_rejected(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        p = tmp_path / "s.json"  # type: ignore[attr-defined]
        p.write_text(
            '{"sources":[{"id":"x","name":"X","repo_url":"https://a/b/c",'
            '"docs_site_url":"https://x","search_index_url":"https://x/s.json",'
            '"vcs_provider":"bitbucket"}]}'
        )
        with pytest.raises(ValueError, match="vcs_provider"):
            load_sources(p)


class TestDefaultSourcesIncludeCombineDocs:
    """The bundled ``docs_sources.json`` should register the Combine docs."""

    def test_combine_docs_registered(self) -> None:
        sources = get_default_sources()
        assert "combine-docs" in sources

    def test_combine_docs_uses_github_provider(self) -> None:
        src = get_default_sources()["combine-docs"]
        assert src.vcs_provider == "github"
        assert "github.com/cms-analysis/HiggsAnalysis-CombinedLimit" in src.repo_url
        assert src.docs_site_url.startswith(
            "https://cms-analysis.github.io/HiggsAnalysis-CombinedLimit",
        )

    def test_default_sources_present(self) -> None:
        """The bundled config ships the docs and paper sources."""
        ids = set(get_default_sources().keys())
        assert "combine-docs" in ids
        assert "combine-paper" in ids


class TestLocalPaperSource:
    def test_local_paper_loads(self, tmp_path: pytest.TempPathFactory) -> None:
        p = tmp_path / "s.json"  # type: ignore[attr-defined]
        p.write_text(
            '{"sources":[{"id":"p","name":"P","repo_url":"https://arxiv.org/abs/X",'
            '"docs_site_url":"https://arxiv.org/abs/X","source_type":"local-paper",'
            '"local_path":"/tmp/paper.txt"}]}'
        )
        sources = load_sources(p)
        assert sources["p"].source_type == "local-paper"
        assert sources["p"].local_path == "/tmp/paper.txt"

    def test_local_paper_requires_local_path(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        p = tmp_path / "s.json"  # type: ignore[attr-defined]
        p.write_text(
            '{"sources":[{"id":"p","name":"P","repo_url":"https://arxiv.org/abs/X",'
            '"docs_site_url":"https://arxiv.org/abs/X","source_type":"local-paper"}]}'
        )
        with pytest.raises(ValueError, match="local_path"):
            load_sources(p)


class TestLocalPathResolution:
    def test_absolute_local_path_passes_through(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        cfg = tmp_path / "s.json"  # type: ignore[attr-defined]
        cfg.write_text(
            '{"sources":[{"id":"p","name":"P","repo_url":"https://arxiv.org/abs/X",'
            '"docs_site_url":"https://arxiv.org/abs/X","source_type":"local-paper",'
            '"local_path":"/var/tmp/paper.txt"}]}'
        )
        sources = load_sources(cfg)
        assert sources["p"].local_path == "/var/tmp/paper.txt"

    def test_relative_local_path_resolved_against_config_dir(
        self, tmp_path: pytest.TempPathFactory,
    ) -> None:
        # Place the JSON in a subdir of tmp_path and use a relative
        # local_path that points one level up to a file we create.
        cfg_dir = tmp_path / "conf"  # type: ignore[attr-defined]
        cfg_dir.mkdir()
        (tmp_path / "paper.txt").write_text("hi")  # type: ignore[attr-defined]
        cfg = cfg_dir / "s.json"
        cfg.write_text(
            '{"sources":[{"id":"p","name":"P","repo_url":"https://arxiv.org/abs/X",'
            '"docs_site_url":"https://arxiv.org/abs/X","source_type":"local-paper",'
            '"local_path":"../paper.txt"}]}'
        )
        sources = load_sources(cfg)
        resolved = sources["p"].local_path
        assert resolved is not None
        # Resolved path is absolute and points at the existing file.
        from pathlib import Path as _P
        assert _P(resolved).is_absolute()
        assert _P(resolved) == (tmp_path / "paper.txt").resolve()  # type: ignore[attr-defined]

    def test_bundled_paper_path_resolves_to_existing_file(self) -> None:
        """The bundled docs_sources.json ships a relative path; it must
        resolve to a file that actually exists in the repo."""
        from pathlib import Path as _P
        sources = get_default_sources()
        local_path = sources["combine-paper"].local_path
        assert local_path is not None
        assert _P(local_path).is_file()
        assert _P(local_path).name == "paper_clean.txt"
