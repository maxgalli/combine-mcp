"""Tests for the shared error formatter."""

from __future__ import annotations

from combine_mcp.tools._helpers import format_error


class TestFormatError:
    def test_basic(self) -> None:
        s = format_error(ValueError("nope"))
        assert s == "Error: nope"

    def test_with_recovery(self) -> None:
        s = format_error(
            ValueError("nope"),
            recovery=["step 1", "step 2"],
        )
        assert "Error: nope" in s
        assert "Recovery steps:" in s
        assert "- step 1" in s
        assert "- step 2" in s
