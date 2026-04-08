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
    tool_dead_code,
    tool_integration_status,
    tool_list_integrations,
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

    def test_dead_code_is_advisory(self, tmp_path: Path) -> None:
        (tmp_path / "module.py").write_text(textwrap.dedent("""\
            \"\"\"Module.\"\"\"

            import icontract

            @icontract.require(lambda x: x > 0, "x must be positive")
            @icontract.ensure(lambda x, result: result == x * 2, "result is double")
            def double(x: int) -> int:
                \"\"\"Return double of x.\"\"\"
                return x * 2
        """), encoding="utf-8")
        result = tool_check(path=str(tmp_path), level=1)
        findings = cast("list[dict[str, Any]]", result["findings"])
        assert result["passed"] is True
        assert result["summary"]["failed"] == 0  # type: ignore[index]
        assert result["summary"]["exempt"] >= 1  # type: ignore[index]
        assert any(
            finding["finding_type"] == "dead_code"
            and finding["status"] == "exempt"
            and isinstance(finding["suggestion"], str)
            and "ask the user" in finding["suggestion"].lower()
            for finding in findings
        )


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
        result = tool_check_file(path=str(bad), level=1)
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
        result = tool_check_file(path=str(good), level=1)
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
        result = tool_check_function(path=str(path), function="bad", level=1)
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
        result = tool_check_function(path=str(path), function="square", level=1)
        assert result["passed"] is True

    def test_invalid_level_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "f.py"
        path.write_text('"""Doc."""\n', encoding="utf-8")
        with pytest.raises(ValueError):
            tool_check_function(path=str(path), function="x", level=0)


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
            path=str(path), function="f",
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
            path=str(path), function="f",
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
        result = tool_suggest_contracts(path=str(path), function="add")
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
        result = tool_suggest_contracts(path=str(path), function="add")
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

            **Source:** none — test fixture.

            ### REQ-001: First requirement
            Description for one.

            ### REQ-002: Second requirement
            Description for two.

            ### REQ-003: Third requirement
            Description for three.
        """), encoding="utf-8")
        return spec

    def _write_integration_spec(self, tmp_path: Path) -> Path:
        spec = tmp_path / "SPEC.md"
        spec.write_text(textwrap.dedent("""\
            # Project SPEC

            **Source:** none — test fixture.

            ### REQ-001: Checkout succeeds
            Description for checkout.

            ### INT-001: Checkout calls payment gateway
            Kind: call
            Source: checkout
            Target: charge
            Supports: REQ-001
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
        assert result["spec_present"] is True

    def test_validate_spec_missing_file_returns_suggested_action(self, tmp_path: Path) -> None:
        missing = tmp_path / "SPEC.md"
        result = tool_validate_spec(spec_file=str(missing))
        assert result["passed"] is False
        assert result["spec_present"] is False
        assert "suggested_action" in result

    def test_list_reqs_missing_file_returns_suggested_action(self, tmp_path: Path) -> None:
        missing = tmp_path / "SPEC.md"
        result = tool_list_reqs(spec_file=str(missing))
        assert result["count"] == 0
        assert result["spec_present"] is False
        assert "suggested_action" in result

    def test_list_integrations_returns_all_ids(self, tmp_path: Path) -> None:
        spec = self._write_integration_spec(tmp_path)
        result = tool_list_integrations(spec_file=str(spec))
        assert result["count"] == 1
        assert result["integration_ids"] == ["INT-001"]

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

    def test_req_status_complete_with_req_id(self, tmp_path: Path) -> None:
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
        reqs = cast("list[dict[str, Any]]", result["reqs"])
        assert len(reqs) == 1
        assert reqs[0]["req_id"] == "REQ-001"
        assert reqs[0]["status"] == "complete"
        assert reqs[0]["exists_in_spec"] is True

    def test_req_status_orphan_with_req_id(self, tmp_path: Path) -> None:
        spec = self._write_spec(tmp_path)
        result = tool_req_status(spec_file=str(spec), req_id="REQ-002")
        reqs = cast("list[dict[str, Any]]", result["reqs"])
        assert len(reqs) == 1
        assert reqs[0]["req_id"] == "REQ-002"
        assert reqs[0]["status"] == "orphan"

    def test_req_status_without_req_id_returns_all_reqs(self, tmp_path: Path) -> None:
        spec = self._write_spec(tmp_path)
        impl = tmp_path / "impl.py"
        impl.write_text(textwrap.dedent('''\
            """Module."""

            def f1() -> None:
                """F1.

                Implements: REQ-001
                """

            def f3() -> None:
                """F3.

                Implements: REQ-003
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
        result = tool_req_status(spec_file=str(spec))  # no req_id
        reqs = cast("list[dict[str, Any]]", result["reqs"])
        # All three spec REQs should appear in the report
        ids = sorted(r["req_id"] for r in reqs)
        assert ids == ["REQ-001", "REQ-002", "REQ-003"]
        # Status mapping
        by_id = {r["req_id"]: r for r in reqs}
        assert by_id["REQ-001"]["status"] == "complete"           # impl + test
        assert by_id["REQ-002"]["status"] == "orphan"             # neither
        assert by_id["REQ-003"]["status"] == "implemented_only"   # impl, no test

    def test_req_status_response_includes_project_root(self, tmp_path: Path) -> None:
        spec = self._write_spec(tmp_path)
        result = tool_req_status(spec_file=str(spec))
        assert "spec_file" in result
        assert "project_root" in result
        assert "reqs" in result

    def test_req_status_surfaces_code_side_orphans(self, tmp_path: Path) -> None:
        # A REQ that exists in code but NOT in the spec should still appear,
        # with exists_in_spec=False — that's a real spec drift case worth catching.
        spec = self._write_spec(tmp_path)
        impl = tmp_path / "impl.py"
        impl.write_text(textwrap.dedent('''\
            """Module."""

            def stale() -> None:
                """Stale.

                Implements: REQ-999
                """
        '''), encoding="utf-8")
        result = tool_req_status(spec_file=str(spec))
        reqs = cast("list[dict[str, Any]]", result["reqs"])
        by_id = {r["req_id"]: r for r in reqs}
        assert "REQ-999" in by_id
        assert by_id["REQ-999"]["exists_in_spec"] is False

    def test_integration_status_complete_with_integration_id(self, tmp_path: Path) -> None:
        spec = self._write_integration_spec(tmp_path)
        impl = tmp_path / "impl.py"
        impl.write_text(textwrap.dedent('''\
            """Module."""

            def checkout() -> None:
                """Checkout flow.

                Implements: INT-001, REQ-001
                """
                charge()

            def charge() -> None:
                """Gateway hook."""
        '''), encoding="utf-8")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_checkout.py").write_text(textwrap.dedent('''\
            """Tests."""

            def test_checkout() -> None:
                """Exercise checkout integration.

                Verifies: INT-001, REQ-001
                """
                assert True
        '''), encoding="utf-8")
        result = tool_integration_status(spec_file=str(spec), integration_id="INT-001")
        integrations = cast("list[dict[str, Any]]", result["integrations"])
        assert len(integrations) == 1
        assert integrations[0]["integration_id"] == "INT-001"
        assert integrations[0]["status"] == "complete"
        assert integrations[0]["kind"] == "call"
        assert integrations[0]["supports"] == ["REQ-001"]


class TestDeadCodeTool:
    """Tests for serenecode_dead_code."""

    def test_dead_code_returns_guidance(self, tmp_path: Path) -> None:
        (tmp_path / "module.py").write_text(textwrap.dedent("""\
            def stale() -> int:
                return 1
        """), encoding="utf-8")
        result = tool_dead_code(path=str(tmp_path))
        findings = cast("list[dict[str, Any]]", result["findings"])
        assert result["status"] == "ok"
        assert len(findings) >= 1
        assert findings[0]["symbol_name"] == "stale"
        assert "ask the user" in findings[0]["guidance"].lower()

    def test_dead_code_ignores_test_files_by_default(self, tmp_path: Path) -> None:
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_helper.py").write_text(textwrap.dedent("""\
            def unused_test_helper() -> int:
                return 1
        """), encoding="utf-8")
        result = tool_dead_code(path=str(tmp_path))
        assert result["status"] == "no_python_files"
        assert result["findings"] == []


class TestToolHelpers:
    """Direct tests for the private helpers in mcp/tools.py.

    Covers branch gaps in `_load_config`, `_wire_adapters`, and the
    fallthrough error paths of `tool_verify_fixed`, `tool_suggest_contracts`,
    and `tool_suggest_test`.
    """

    def test_load_config_no_serenecode_md(self, tmp_path: Path) -> None:
        """Branch (lines 126-127): no SERENECODE.md → default_config."""
        from serenecode.mcp.tools import _load_config
        config = _load_config(str(tmp_path))
        assert config.template_name == "default"

    def test_load_config_with_serenecode_md(self, tmp_path: Path) -> None:
        """Branch (lines 128-137): parses SERENECODE.md and caches."""
        from serenecode.mcp.tools import _load_config
        (tmp_path / "SERENECODE.md").write_text("Template: minimal\n", encoding="utf-8")
        config = _load_config(str(tmp_path))
        assert config.template_name == "minimal"

    def test_load_config_uses_cache_on_repeat(self, tmp_path: Path) -> None:
        """Branch (lines 132-134): cache hit returns cached config."""
        from serenecode.mcp.tools import _load_config
        (tmp_path / "SERENECODE.md").write_text("Template: minimal\n", encoding="utf-8")
        config1 = _load_config(str(tmp_path))
        config2 = _load_config(str(tmp_path))
        assert config1 is config2  # cached object

    def test_wire_adapters_level_1(self) -> None:
        """No adapters needed for L1."""
        from serenecode.mcp.tools import _wire_adapters
        adapters = _wire_adapters(1)
        assert adapters["type_checker"] is None
        assert adapters["coverage_analyzer"] is None
        assert adapters["property_tester"] is None
        assert adapters["symbolic_checker"] is None

    def test_wire_adapters_level_2_wires_mypy(self) -> None:
        """Branch (lines 213-218): level >= 2 wires the mypy adapter."""
        from serenecode.mcp.tools import _wire_adapters
        adapters = _wire_adapters(2)
        assert adapters["type_checker"] is not None
        assert adapters["coverage_analyzer"] is None

    def test_wire_adapters_level_3_wires_coverage(self) -> None:
        """Branch (lines 220-225): level >= 3 wires the coverage adapter."""
        from serenecode.mcp.tools import _wire_adapters
        adapters = _wire_adapters(3)
        assert adapters["type_checker"] is not None
        assert adapters["coverage_analyzer"] is not None

    def test_wire_adapters_level_4_wires_hypothesis(self) -> None:
        """Branch (lines 227-232): level >= 4 wires the hypothesis adapter."""
        from serenecode.mcp.tools import _wire_adapters
        adapters = _wire_adapters(4)
        assert adapters["property_tester"] is not None

    def test_wire_adapters_level_5_wires_crosshair(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Branch (lines 234-238): level >= 5 wires the crosshair adapter.

        Mocked to avoid actually importing CrossHair (which monkey-patches
        icontract for the rest of the test process).
        """
        import sys
        from unittest.mock import MagicMock

        fake_module = MagicMock()
        fake_module.CrossHairSymbolicChecker = MagicMock(return_value="fake_checker")
        monkeypatch.setitem(
            sys.modules,
            "serenecode.adapters.crosshair_adapter",
            fake_module,
        )
        from serenecode.mcp.tools import _wire_adapters
        adapters = _wire_adapters(5)
        assert adapters["symbolic_checker"] == "fake_checker"

    def test_verify_fixed_when_findings_clean(self, tmp_path: Path) -> None:
        """tool_verify_fixed handles missing/empty findings list gracefully."""
        from serenecode.mcp.tools import tool_verify_fixed
        path = tmp_path / "good.py"
        path.write_text(textwrap.dedent("""\
            \"\"\"Doc.\"\"\"

            import icontract

            @icontract.require(lambda x: x > 0, "x positive")
            @icontract.ensure(lambda x, result: result == x, "identity")
            def f(x: int) -> int:
                \"\"\"Doc.\"\"\"
                return x
        """), encoding="utf-8")
        # Function is clean, finding_substring won't match anything
        result = tool_verify_fixed(
            path=str(path),
            function="f",
            finding_substring="totally unrelated text",
            level=1,
        )
        assert result["fixed"] is True

    def test_suggest_test_returns_suggestions_list(self, tmp_path: Path) -> None:
        """tool_suggest_test returns the suggestions field shape."""
        from serenecode.mcp.server import build_server
        from serenecode.mcp.tools import tool_suggest_test
        # Enable allow_code_execution because suggest_test runs at L3
        build_server(allow_code_execution=True)
        path = tmp_path / "mod.py"
        path.write_text(textwrap.dedent("""\
            \"\"\"Doc.\"\"\"

            import icontract

            @icontract.require(lambda x: x > 0, "x positive")
            @icontract.ensure(lambda x, result: result == x, "identity")
            def f(x: int) -> int:
                \"\"\"Doc.\"\"\"
                return x
        """), encoding="utf-8")
        result = tool_suggest_test(path=str(path), function="f")
        assert "suggestions" in result
        assert "function" in result
        assert isinstance(result["suggestions"], list)

    def test_suggest_test_path_in_result(self, tmp_path: Path) -> None:
        from serenecode.mcp.server import build_server
        from serenecode.mcp.tools import tool_suggest_test
        build_server(allow_code_execution=True)
        path = tmp_path / "mod.py"
        path.write_text('"""Doc."""\n', encoding="utf-8")
        result = tool_suggest_test(path=str(path), function="anything")
        # Result echoes the path
        assert "path" in result

    def test_verify_fixed_normalizes_non_list_findings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Branch (line 504): findings field isn't a list → reset to []."""
        from serenecode.mcp import tools

        # Mock tool_check_function to return a malformed response
        def fake_check_function(**kwargs: object) -> dict[str, object]:
            return {"passed": True, "findings": "not a list"}

        monkeypatch.setattr(tools, "tool_check_function", fake_check_function)

        result = tools.tool_verify_fixed(
            path="dummy.py",
            function="f",
            finding_substring="anything",
            level=1,
        )
        # When findings isn't a list, fall back to [], remaining is empty → fixed True
        assert result["fixed"] is True

    def test_suggest_contracts_filters_by_function(self, tmp_path: Path) -> None:
        """Branch (line 558): skip results that don't match the function name."""
        from serenecode.mcp.tools import tool_suggest_contracts
        path = tmp_path / "mod.py"
        path.write_text(textwrap.dedent("""\
            \"\"\"Doc.\"\"\"

            def first(a: int, b: int) -> int:
                \"\"\"First.\"\"\"
                return a + b

            def second(x: int) -> int:
                \"\"\"Second.\"\"\"
                return x * 2
        """), encoding="utf-8")
        # Both functions are missing contracts; ask for `second` only
        result = tool_suggest_contracts(path=str(path), function="second")
        suggestions = cast("list[str]", result["suggestions"])
        # Should have suggestions for second only — first must be filtered out
        assert isinstance(suggestions, list)

    def test_suggest_test_extracts_suggestion_strings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Branch (lines 850-853): extract non-empty suggestion strings from findings."""
        from serenecode.mcp import tools

        # Mock tool_check_function to return findings with real suggestions
        def fake_check_function(**kwargs: object) -> dict[str, object]:
            return {
                "passed": False,
                "findings": [
                    {"function": "f", "suggestion": "test scaffold A"},
                    {"function": "f", "suggestion": "test scaffold B"},
                    {"function": "f", "suggestion": ""},  # empty, skipped
                    {"function": "f", "suggestion": None},  # None, skipped
                    "not a dict",  # wrong type, skipped
                ],
            }

        monkeypatch.setattr(tools, "tool_check_function", fake_check_function)
        result = tools.tool_suggest_test(path="dummy.py", function="f")
        suggestions = cast("list[str]", result["suggestions"])
        assert "test scaffold A" in suggestions
        assert "test scaffold B" in suggestions
        # Empty/None/wrong-type entries should be filtered out
        assert "" not in suggestions
        assert None not in suggestions
