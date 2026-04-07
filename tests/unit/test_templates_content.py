"""Tests for serenecode.templates.content selectors.

Direct unit tests for `get_template` and `get_template_with_options`,
which select between the default/strict/minimal SERENECODE.md templates.
"""

from __future__ import annotations

import pytest

from serenecode.templates.content import get_template, get_template_with_options


class TestGetTemplate:
    @pytest.mark.parametrize("name", ["default", "strict", "minimal"])
    def test_returns_non_empty_string(self, name: str) -> None:
        result = get_template(name)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_default_template_contains_default_marker(self) -> None:
        result = get_template("default")
        assert "Project Conventions" in result or "## Contract Standards" in result

    def test_strict_template_is_strict(self) -> None:
        result = get_template("strict")
        assert "Strict" in result

    def test_minimal_template_is_minimal(self) -> None:
        result = get_template("minimal")
        assert "Minimal" in result

    def test_each_template_distinct(self) -> None:
        a = get_template("default")
        b = get_template("strict")
        c = get_template("minimal")
        assert a != b
        assert a != c
        assert b != c


class TestGetTemplateWithOptions:
    @pytest.mark.parametrize("name", ["default", "strict", "minimal"])
    def test_without_spec_traceability(self, name: str) -> None:
        result = get_template_with_options(name, include_spec_traceability=False)
        assert isinstance(result, str)
        assert len(result) > 0
        assert "## Spec Traceability" not in result or "REQ-" not in result

    @pytest.mark.parametrize("name", ["default", "strict", "minimal"])
    def test_with_spec_traceability(self, name: str) -> None:
        result = get_template_with_options(name, include_spec_traceability=True)
        assert isinstance(result, str)
        assert "Spec Traceability" in result
        assert "REQ-" in result

    def test_with_options_preserves_template_body(self) -> None:
        plain = get_template("default")
        with_opts = get_template_with_options("default", include_spec_traceability=True)
        # The plain body must still be present somewhere in the composed result
        # (the spec section is appended to the end)
        assert plain.split("\n", 1)[0] in with_opts

    def test_default_options_omits_spec(self) -> None:
        # Default value of include_spec_traceability is False
        result = get_template_with_options("default")
        plain = get_template("default")
        assert result == plain

    def test_read_the_full_output_directive_present(self) -> None:
        # All three templates should include the new "read full output" directive
        for name in ("default", "strict", "minimal"):
            result = get_template(name)
            assert "never truncate" in result.lower() or "Reading" in result
