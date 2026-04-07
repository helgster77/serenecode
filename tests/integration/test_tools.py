"""Integration tests for the MCP tool functions in serenecode.mcp.tools.

Tests instantiate the tool functions directly and assert they wire
through to the right pipeline functions and return the expected
JSON-friendly response shape.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any, cast

import pytest

from serenecode.core.exceptions import UnsafeCodeExecutionError
from serenecode.mcp.tools import (
    get_state,
    reset_state,
    tool_check,
    tool_check_file,
    tool_check_function,
    tool_list_reqs,
    tool_orphans,
    tool_req_status,
    tool_suggest_contracts,
    tool_validate_spec,
    tool_verify_fixed,
)


@pytest.fixture(autouse=True)
def _reset_mcp_state() -> None:
    """Ensure each test starts with clean MCP server state."""
    reset_state()


# ---------------------------------------------------------------------------
# tool_check
# ---------------------------------------------------------------------------


class TestToolCheck:
    """Tests for serenecode_check (project-wide verification)."""

    def test_clean_project_passes(self, tmp_path: Path) -> None:
        (tmp_path / "module.py").write_text(textwrap.dedent("""\
            \"\"\"Clean module.\"\"\"

            import icontract

            @icontract.require(lambda x: x > 0, "x must be positive")
            @icontract.ensure(lambda x, result: result == x * 2, "result is double")
            def double(x: int) -> int:
                \"\"\"Return double of x.\"\"\"
                return x * 2
        """), encoding="utf-8")
        result = tool_check(path=str(tmp_path), level=1)
        assert result["passed"] is True
        assert result["summary"]["failed"] == 0  # type: ignore[index]

    def test_violations_reported_in_findings(self, tmp_path: Path) -> None:
        (tmp_path / "bad.py").write_text(textwrap.dedent("""\
            \"\"\"Module with violations.\"\"\"

            def f(x=[]):
                return x
        """), encoding="utf-8")
        result = tool_check(path=str(tmp_path), level=1)
        assert result["passed"] is False
        findings = cast("list[dict[str, Any]]", result["findings"])
        assert any("mutable" in f["message"].lower() for f in findings)

    def test_invalid_level_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            tool_check(path=str(tmp_path), level=0)
        with pytest.raises(ValueError):
            tool_check(path=str(tmp_path), level=7)

    def test_level_3_without_allow_code_execution_raises(self, tmp_path: Path) -> None:
        reset_state()  # ensure allow_code_execution=False
        with pytest.raises(UnsafeCodeExecutionError):
            tool_check(path=str(tmp_path), level=3)

    def test_last_check_state_populated(self, tmp_path: Path) -> None:
        (tmp_path / "m.py").write_text('"""Doc."""\n', encoding="utf-8")
        tool_check(path=str(tmp_path), level=1)
        state = get_state()
        assert state.last_check is not None


# ---------------------------------------------------------------------------
# tool_check_file
# ---------------------------------------------------------------------------


class TestToolCheckFile:
    """Tests for serenecode_check_file."""

    def test_returns_findings_for_named_file(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.py"
        bad.write_text(textwrap.dedent("""\
            \"\"\"Bad module.\"\"\"

            def f(x=[]):
                return x
        """), encoding="utf-8")
        result = tool_check_file(file=str(bad), level=1)
        assert result["passed"] is False
        findings = cast("list[dict[str, Any]]", result["findings"])
        assert any("mutable" in f["message"].lower() for f in findings)

    def test_clean_file_passes(self, tmp_path: Path) -> None:
        good = tmp_path / "good.py"
        good.write_text(textwrap.dedent("""\
            \"\"\"Good module.\"\"\"

            import icontract

            @icontract.require(lambda x: x >= 0, "x non-negative")
            @icontract.ensure(lambda x, result: result == x + 1, "increments")
            def inc(x: int) -> int:
                \"\"\"Return x + 1.\"\"\"
                return x + 1
        """), encoding="utf-8")
        result = tool_check_file(file=str(good), level=1)
        assert result["passed"] is True


# ---------------------------------------------------------------------------
# tool_check_function
# ---------------------------------------------------------------------------


class TestToolCheckFunction:
    """Tests for serenecode_check_function."""

    def test_filters_to_named_function(self, tmp_path: Path) -> None:
        path = tmp_path / "many.py"
        path.write_text(textwrap.dedent("""\
            \"\"\"Doc.\"\"\"

            def good(x: int) -> int:
                \"\"\"Doc.\"\"\"
                return x

            def bad(x=[]):
                return x
        """), encoding="utf-8")
        result = tool_check_function(file=str(path), function="bad", level=1)
        findings = cast("list[dict[str, Any]]", result["findings"])
        for f in findings:
            assert f["function"] == "bad"

    def test_no_findings_when_function_clean(self, tmp_path: Path) -> None:
        path = tmp_path / "ok.py"
        path.write_text(textwrap.dedent("""\
            \"\"\"Doc.\"\"\"

            import icontract

            @icontract.require(lambda x: x > 0, "x positive")
            @icontract.ensure(lambda x, result: result == x * x, "square")
            def square(x: int) -> int:
                \"\"\"Return x * x.\"\"\"
                return x * x
        """), encoding="utf-8")
        result = tool_check_function(file=str(path), function="square", level=1)
        assert result["passed"] is True

    def test_invalid_level_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "f.py"
        path.write_text('"""Doc."""\n', encoding="utf-8")
        with pytest.raises(ValueError):
            tool_check_function(file=str(path), function="x", level=0)


# ---------------------------------------------------------------------------
# tool_verify_fixed
# ---------------------------------------------------------------------------


class TestToolVerifyFixed:
    """Tests for serenecode_verify_fixed."""

    def test_unfixed_finding_still_present(self, tmp_path: Path) -> None:
        path = tmp_path / "still.py"
        path.write_text(textwrap.dedent("""\
            \"\"\"Doc.\"\"\"

            def f(x=[]):
                return x
        """), encoding="utf-8")
        result = tool_verify_fixed(
            file=str(path), function="f",
            finding_substring="mutable default", level=1,
        )
        assert result["fixed"] is False
        assert len(cast("list[Any]", result["remaining_findings"])) >= 1

    def test_fixed_finding_returns_true(self, tmp_path: Path) -> None:
        path = tmp_path / "fixed.py"
        path.write_text(textwrap.dedent("""\
            \"\"\"Doc.\"\"\"

            import icontract

            @icontract.require(lambda x: x is None or isinstance(x, list), "x ok")
            @icontract.ensure(lambda result: len(result) >= 0, "non-negative length")
            def f(x: list[int] | None = None) -> list[int]:
                \"\"\"Doc.\"\"\"
                if x is None:
                    x = []
                return x
        """), encoding="utf-8")
        result = tool_verify_fixed(
            file=str(path), function="f",
            finding_substring="mutable default", level=1,
        )
        assert result["fixed"] is True


# ---------------------------------------------------------------------------
# tool_suggest_contracts
# ---------------------------------------------------------------------------


class TestToolSuggestContracts:
    """Tests for serenecode_suggest_contracts."""

    def test_function_missing_contracts_yields_suggestions(self, tmp_path: Path) -> None:
        path = tmp_path / "no_contracts.py"
        path.write_text(textwrap.dedent("""\
            \"\"\"Doc.\"\"\"

            def add(a: int, b: int) -> int:
                \"\"\"Add two ints.\"\"\"
                return a + b
        """), encoding="utf-8")
        result = tool_suggest_contracts(file=str(path), function="add")
        suggestions = cast("list[str]", result["suggestions"])
        assert len(suggestions) >= 1
        assert any("require" in s.lower() or "ensure" in s.lower() for s in suggestions)

    def test_function_with_contracts_yields_no_suggestions(self, tmp_path: Path) -> None:
        path = tmp_path / "has_contracts.py"
        path.write_text(textwrap.dedent("""\
            \"\"\"Doc.\"\"\"

            import icontract

            @icontract.require(lambda a, b: a >= 0 and b >= 0, "both non-negative")
            @icontract.ensure(lambda a, b, result: result == a + b, "sum")
            def add(a: int, b: int) -> int:
                \"\"\"Add two non-negative ints.\"\"\"
                return a + b
        """), encoding="utf-8")
        result = tool_suggest_contracts(file=str(path), function="add")
        suggestions = cast("list[str]", result["suggestions"])
        assert len(suggestions) == 0


# ---------------------------------------------------------------------------
# Spec / REQ tools
# ---------------------------------------------------------------------------


class TestSpecTools:
    """Tests for serenecode_validate_spec / list_reqs / req_status / orphans."""

    def _write_spec(self, tmp_path: Path) -> Path:
        spec = tmp_path / "SPEC.md"
        spec.write_text(textwrap.dedent("""\
            # Project SPEC

            ### REQ-001: First requirement
            Description for one.

            ### REQ-002: Second requirement
            Description for two.

            ### REQ-003: Third requirement
            Description for three.
        """), encoding="utf-8")
        return spec

    def test_list_reqs_returns_all_ids(self, tmp_path: Path) -> None:
        spec = self._write_spec(tmp_path)
        result = tool_list_reqs(spec_file=str(spec))
        assert result["count"] == 3
        assert result["req_ids"] == ["REQ-001", "REQ-002", "REQ-003"]

    def test_validate_spec_passes_for_clean_spec(self, tmp_path: Path) -> None:
        spec = self._write_spec(tmp_path)
        result = tool_validate_spec(spec_file=str(spec))
        assert result["passed"] is True

    def test_orphans_lists_unimplemented_and_untested(self, tmp_path: Path) -> None:
        spec = self._write_spec(tmp_path)
        impl = tmp_path / "impl.py"
        impl.write_text(textwrap.dedent('''\
            """Module."""

            def feature_one() -> None:
                """Feature one.

                Implements: REQ-001
                """
        '''), encoding="utf-8")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        test_file = tests_dir / "test_features.py"
        test_file.write_text(textwrap.dedent('''\
            """Tests."""

            def test_feature_two() -> None:
                """Test for feature two.

                Verifies: REQ-002
                """
                assert True
        '''), encoding="utf-8")
        result = tool_orphans(spec_file=str(spec))
        unimplemented = cast("list[str]", result["unimplemented"])
        untested = cast("list[str]", result["untested"])
        assert "REQ-002" in unimplemented
        assert "REQ-003" in unimplemented
        assert "REQ-001" not in unimplemented
        assert "REQ-001" in untested
        assert "REQ-003" in untested
        assert "REQ-002" not in untested

    def test_req_status_complete(self, tmp_path: Path) -> None:
        spec = self._write_spec(tmp_path)
        impl = tmp_path / "impl.py"
        impl.write_text(textwrap.dedent('''\
            """Module."""

            def f1() -> None:
                """F1.

                Implements: REQ-001
                """
        '''), encoding="utf-8")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_f.py").write_text(textwrap.dedent('''\
            """Tests."""

            def test_f1() -> None:
                """Test.

                Verifies: REQ-001
                """
                assert True
        '''), encoding="utf-8")
        result = tool_req_status(spec_file=str(spec), req_id="REQ-001")
        assert result["status"] == "complete"
        assert result["exists_in_spec"] is True

    def test_req_status_orphan(self, tmp_path: Path) -> None:
        spec = self._write_spec(tmp_path)
        result = tool_req_status(spec_file=str(spec), req_id="REQ-002")
        assert result["status"] == "orphan"
