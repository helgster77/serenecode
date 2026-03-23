"""Unit tests for adapter internal functions.

Tests parsing logic, strategy derivation, and helper functions
without running external tools (mypy, hypothesis, crosshair).
"""

from __future__ import annotations

from functools import wraps
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import pytest

from serenecode.adapters.mypy_adapter import MypyTypeChecker, _MYPY_OUTPUT_PATTERN
from serenecode.core.exceptions import ToolNotInstalledError, UnsafeCodeExecutionError


class TestMypyOutputParsing:
    """Tests for mypy output line parsing."""

    def test_parses_error_with_code(self) -> None:
        line = "src/foo.py:42:5: error: Argument 1 has incompatible type [arg-type]"
        match = _MYPY_OUTPUT_PATTERN.match(line)
        assert match is not None
        assert match.group(1) == "src/foo.py"
        assert match.group(2) == "42"
        assert match.group(3) == "5"
        assert match.group(4) == "error"
        assert match.group(6) == "arg-type"

    def test_parses_error_without_code(self) -> None:
        line = "src/foo.py:10:1: error: Some error message"
        match = _MYPY_OUTPUT_PATTERN.match(line)
        assert match is not None
        assert match.group(6) is None

    def test_parses_warning(self) -> None:
        line = "src/foo.py:5:1: warning: Unused variable"
        match = _MYPY_OUTPUT_PATTERN.match(line)
        assert match is not None
        assert match.group(4) == "warning"

    def test_parses_note(self) -> None:
        line = "src/foo.py:5:1: note: See docs for help"
        match = _MYPY_OUTPUT_PATTERN.match(line)
        assert match is not None
        assert match.group(4) == "note"

    def test_no_match_on_summary(self) -> None:
        line = "Found 5 errors in 2 files (checked 3 source files)"
        match = _MYPY_OUTPUT_PATTERN.match(line)
        assert match is None

    def test_no_match_on_blank(self) -> None:
        match = _MYPY_OUTPUT_PATTERN.match("")
        assert match is None

    def test_parses_full_output(self) -> None:
        output = """\
src/a.py:10:5: error: Missing return [return]
src/a.py:15:1: error: Incompatible types [assignment]
src/b.py:3:1: note: See help
Found 2 errors in 2 files (checked 2 source files)
"""
        checker = MypyTypeChecker()
        issues = checker._parse_output(output)
        errors = [i for i in issues if i.severity == "error"]
        notes = [i for i in issues if i.severity == "note"]
        assert len(errors) == 2
        assert len(notes) == 1
        assert errors[0].file == "src/a.py"
        assert errors[0].line == 10
        assert errors[0].code == "return"

    def test_empty_input_returns_empty(self) -> None:
        checker = MypyTypeChecker()
        issues = checker._parse_output("")
        assert issues == []

    def test_check_raises_when_mypy_module_is_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        checker = MypyTypeChecker()

        monkeypatch.setattr(
            "serenecode.adapters.mypy_adapter.subprocess.run",
            lambda *args, **kwargs: SimpleNamespace(
                stdout="",
                stderr="python: No module named mypy\n",
                returncode=1,
            ),
        )

        with pytest.raises(ToolNotInstalledError):
            checker.check(["demo.py"])

    def test_check_returns_synthetic_error_for_stderr_only_failures(self, monkeypatch: pytest.MonkeyPatch) -> None:
        checker = MypyTypeChecker()

        monkeypatch.setattr(
            "serenecode.adapters.mypy_adapter.subprocess.run",
            lambda *args, **kwargs: SimpleNamespace(
                stdout="",
                stderr="mypy crashed before producing parseable output",
                returncode=2,
            ),
        )

        issues = checker.check(["demo.py"])

        assert len(issues) == 1
        assert issues[0].severity == "error"
        assert "crashed" in issues[0].message


class TestHypothesisAdapterInternals:
    """Tests for Hypothesis adapter internal functions."""

    def test_strategy_for_int(self) -> None:
        from serenecode.adapters.hypothesis_adapter import _get_strategy_for_annotation
        strategy = _get_strategy_for_annotation(int)
        assert strategy is not None

    def test_callable_stub_can_return_check_result(self) -> None:
        from serenecode.adapters.hypothesis_adapter import _make_callable_stub
        from serenecode.models import CheckResult

        stub = _make_callable_stub(Callable[[str], CheckResult])
        result = stub("demo")

        assert isinstance(result, CheckResult)

    def test_strategy_for_float(self) -> None:
        from serenecode.adapters.hypothesis_adapter import _get_strategy_for_annotation
        strategy = _get_strategy_for_annotation(float)
        assert strategy is not None

    def test_strategy_for_str(self) -> None:
        from serenecode.adapters.hypothesis_adapter import _get_strategy_for_annotation
        strategy = _get_strategy_for_annotation(str)
        assert strategy is not None

    def test_strategy_for_bool(self) -> None:
        from serenecode.adapters.hypothesis_adapter import _get_strategy_for_annotation
        strategy = _get_strategy_for_annotation(bool)
        assert strategy is not None

    def test_strategy_for_bytes(self) -> None:
        from serenecode.adapters.hypothesis_adapter import _get_strategy_for_annotation
        strategy = _get_strategy_for_annotation(bytes)
        assert strategy is not None

    def test_no_strategy_for_none(self) -> None:
        from serenecode.adapters.hypothesis_adapter import _get_strategy_for_annotation
        strategy = _get_strategy_for_annotation(None)
        assert strategy is None

    def test_strategy_for_list_int(self) -> None:
        from serenecode.adapters.hypothesis_adapter import _get_strategy_for_annotation
        strategy = _get_strategy_for_annotation(list[int])
        assert strategy is not None

    def test_strategy_for_protocol_stub(self) -> None:
        from serenecode.adapters.hypothesis_adapter import _get_strategy_for_annotation
        from serenecode.ports.file_system import FileReader

        strategy = _get_strategy_for_annotation(FileReader)

        assert strategy is not None

    def test_strategy_for_ast_module(self) -> None:
        from serenecode.adapters.hypothesis_adapter import _get_strategy_for_annotation
        import ast

        strategy = _get_strategy_for_annotation(ast.Module)

        assert strategy is not None

    def test_strategy_for_check_result(self) -> None:
        from serenecode.adapters.hypothesis_adapter import _get_strategy_for_annotation
        from serenecode.models import CheckResult

        strategy = _get_strategy_for_annotation(CheckResult)

        assert strategy is not None

    def test_build_strategies_multiple_params(self) -> None:
        from serenecode.adapters.hypothesis_adapter import _build_strategies_from_signature
        def func(x: int, y: str, z: float) -> bool:
            return True
        strategies = _build_strategies_from_signature(func)
        assert strategies is not None
        assert "x" in strategies
        assert "y" in strategies
        assert "z" in strategies

    def test_has_icontract_decorators_true(self) -> None:
        from serenecode.adapters.hypothesis_adapter import _has_icontract_decorators
        import icontract
        @icontract.require(lambda x: x > 0, "positive")
        def func(x: int) -> int:
            return x
        assert _has_icontract_decorators(func) is True

    def test_has_icontract_decorators_false(self) -> None:
        from serenecode.adapters.hypothesis_adapter import _has_icontract_decorators
        def func(x: int) -> int:
            return x
        assert _has_icontract_decorators(func) is False

    def test_has_icontract_decorators_false_for_plain_wrapped_function(self) -> None:
        from serenecode.adapters.hypothesis_adapter import _has_icontract_decorators

        def decorator(fn):
            @wraps(fn)
            def inner(*args, **kwargs):
                return fn(*args, **kwargs)
            return inner

        @decorator
        def func(x: int) -> int:
            return x

        assert _has_icontract_decorators(func) is False

    def test_check_preconditions_passing(self) -> None:
        from serenecode.adapters.hypothesis_adapter import _check_preconditions
        import icontract
        @icontract.require(lambda x: x > 0, "positive")
        def func(x: int) -> int:
            return x
        assert _check_preconditions(func, {"x": 5}) is True

    def test_check_preconditions_failing(self) -> None:
        from serenecode.adapters.hypothesis_adapter import _check_preconditions
        import icontract
        @icontract.require(lambda x: x > 0, "positive")
        def func(x: int) -> int:
            return x
        assert _check_preconditions(func, {"x": -1}) is False

    def test_check_preconditions_requires_all_conditions(self) -> None:
        from serenecode.adapters.hypothesis_adapter import _check_preconditions
        import icontract

        @icontract.require(lambda x: x > 0, "positive")
        @icontract.require(lambda x: x < 10, "single digit")
        def func(x: int) -> int:
            return x

        assert _check_preconditions(func, {"x": 5}) is True
        assert _check_preconditions(func, {"x": 50}) is False

    def test_find_nested_violation(self) -> None:
        from serenecode.adapters.hypothesis_adapter import _find_nested_violation
        import icontract
        exc = icontract.ViolationError("test violation")
        result = _find_nested_violation(exc)
        assert result is exc

    def test_find_nested_violation_wrapped(self) -> None:
        from serenecode.adapters.hypothesis_adapter import _find_nested_violation
        import icontract
        inner = icontract.ViolationError("inner")
        outer = RuntimeError("outer")
        outer.__cause__ = inner
        result = _find_nested_violation(outer)
        assert result is inner

    def test_find_nested_violation_none(self) -> None:
        from serenecode.adapters.hypothesis_adapter import _find_nested_violation
        exc = RuntimeError("no violation")
        result = _find_nested_violation(exc)
        assert result is None

    def test_property_tester_requires_explicit_code_execution_consent(self) -> None:
        from serenecode.adapters.hypothesis_adapter import HypothesisPropertyTester

        tester = HypothesisPropertyTester()

        with pytest.raises(UnsafeCodeExecutionError):
            tester.test_module("tests.fixtures.valid.simple_function")


@pytest.mark.slow
class TestCrossHairAdapterInternals:
    """Tests for CrossHair adapter internal functions.

    Marked as slow because importing CrossHair monkey-patches
    icontract internals, which breaks invariant checking in
    the same process.
    """

    def test_parse_counterexample_with_args(self) -> None:
        from serenecode.adapters.crosshair_adapter import _parse_counterexample
        msg = "when calling func(x=5, y=-1)"
        result = _parse_counterexample(msg)
        assert result is not None
        assert result["x"] == "5"
        assert result["y"] == "-1"

    def test_parse_counterexample_no_match(self) -> None:
        from serenecode.adapters.crosshair_adapter import _parse_counterexample
        msg = "Postcondition violated"
        result = _parse_counterexample(msg)
        assert result is None

    def test_parse_cli_output_empty(self) -> None:
        from serenecode.adapters.crosshair_adapter import _parse_cli_output
        findings = _parse_cli_output("test_module", "", "")
        assert len(findings) == 1
        assert findings[0].outcome == "verified"

    def test_parse_cli_output_error(self) -> None:
        from serenecode.adapters.crosshair_adapter import _parse_cli_output
        findings = _parse_cli_output(
            "test_module",
            "test.py:10: error: Postcondition failed\n",
            "",
        )
        assert len(findings) >= 1
        assert findings[0].outcome == "counterexample"

    def test_parse_cli_output_stderr(self) -> None:
        from serenecode.adapters.crosshair_adapter import _parse_cli_output
        findings = _parse_cli_output("test_module", "", "Some error occurred")
        assert len(findings) == 1
        assert findings[0].outcome == "error"

    def test_verify_module_uses_instance_timeouts_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from serenecode.adapters.crosshair_adapter import CrossHairSymbolicChecker

        checker = CrossHairSymbolicChecker(
            per_condition_timeout=111,
            per_path_timeout=222,
            allow_code_execution=True,
        )
        captured: dict[str, object] = {}

        def fake_verify(
            module_path: str,
            per_condition_timeout: int,
            per_path_timeout: int,
            search_paths: tuple[str, ...] = (),
        ):
            captured["module_path"] = module_path
            captured["per_condition_timeout"] = per_condition_timeout
            captured["per_path_timeout"] = per_path_timeout
            captured["search_paths"] = search_paths
            return []

        monkeypatch.setattr(
            "serenecode.adapters.crosshair_adapter._CROSSHAIR_API_AVAILABLE",
            True,
        )
        monkeypatch.setattr(
            "serenecode.adapters.crosshair_adapter._check_crosshair_cli",
            lambda: False,
        )
        monkeypatch.setattr(checker, "_verify_via_api", fake_verify)

        checker.verify_module("demo.module")

        assert captured["module_path"] == "demo.module"
        assert captured["per_condition_timeout"] == 111
        assert captured["per_path_timeout"] == 222
        assert captured["search_paths"] == ()

    def test_verify_module_prefers_cli_when_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from serenecode.adapters.crosshair_adapter import CrossHairSymbolicChecker

        checker = CrossHairSymbolicChecker(
            per_condition_timeout=33,
            per_path_timeout=44,
            allow_code_execution=True,
        )
        captured: dict[str, object] = {}

        def fake_verify(
            module_path: str,
            per_condition_timeout: int,
            per_path_timeout: int,
            search_paths: tuple[str, ...] = (),
        ):
            captured["module_path"] = module_path
            captured["per_condition_timeout"] = per_condition_timeout
            captured["per_path_timeout"] = per_path_timeout
            captured["search_paths"] = search_paths
            return []

        monkeypatch.setattr(
            "serenecode.adapters.crosshair_adapter._CROSSHAIR_API_AVAILABLE",
            True,
        )
        monkeypatch.setattr(
            "serenecode.adapters.crosshair_adapter._check_crosshair_cli",
            lambda: True,
        )
        monkeypatch.setattr(checker, "_verify_via_cli", fake_verify)

        checker.verify_module("demo.module")

        assert captured["module_path"] == "demo.module"
        assert captured["per_condition_timeout"] == 33
        assert captured["per_path_timeout"] == 44
        assert captured["search_paths"] == ()

    def test_verify_module_requires_explicit_code_execution_consent(self) -> None:
        from serenecode.adapters.crosshair_adapter import CrossHairSymbolicChecker

        checker = CrossHairSymbolicChecker()

        with pytest.raises(UnsafeCodeExecutionError):
            checker.verify_module("demo.module")

    def test_cli_backend_returns_no_findings_for_modules_without_targets(self) -> None:
        from serenecode.adapters.crosshair_adapter import CrossHairSymbolicChecker

        checker = CrossHairSymbolicChecker(allow_code_execution=True)

        findings = checker._verify_via_cli("serenecode.adapters.local_fs", 1, 1)

        assert findings == []

    def test_cli_backend_respects_module_timeout_budget(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from serenecode.adapters.crosshair_adapter import CrossHairSymbolicChecker

        checker = CrossHairSymbolicChecker(
            per_condition_timeout=10,
            per_path_timeout=10,
            module_timeout=1,
            allow_code_execution=True,
        )
        calls: list[tuple[str, float]] = []
        times = iter([0.0, 0.0, 1.1])

        monkeypatch.setattr(
            "serenecode.adapters.crosshair_adapter._discover_cli_targets",
            lambda module_path, search_paths=(): [
                ("demo.module.first", "first"),
                ("demo.module.second", "second"),
            ],
        )
        monkeypatch.setattr(
            "serenecode.adapters.crosshair_adapter.time.monotonic",
            lambda: next(times),
        )

        def fake_run(
            cmd: list[str],
            capture_output: bool,
            text: bool,
            timeout: float,
            env: dict[str, str],
        ) -> SimpleNamespace:
            calls.append((cmd[4], timeout))
            return SimpleNamespace(stdout="", stderr="", returncode=0)

        monkeypatch.setattr(
            "serenecode.adapters.crosshair_adapter.subprocess.run",
            fake_run,
        )

        findings = checker._verify_via_cli("demo.module", 10, 10)

        assert calls == [("demo.module.first", 1.0)]
        assert findings[-1].function_name == "<module>"
        assert findings[-1].outcome == "timeout"

    def test_symbolic_target_rejects_container_of_project_types(self) -> None:
        from serenecode.adapters.crosshair_adapter import _is_symbolic_friendly_target
        from serenecode.ports.type_checker import TypeIssue

        def transform(issues: list[TypeIssue]) -> int:
            return len(issues)

        assert _is_symbolic_friendly_target(transform) is False

    def test_property_target_rejects_result_model_parameters(self) -> None:
        from serenecode.adapters.hypothesis_adapter import _uses_result_model_annotation
        from serenecode.models import CheckResult

        assert _uses_result_model_annotation(CheckResult) is True

    def test_external_detail_annotation_is_reported_as_skipped(self, tmp_path: Path) -> None:
        from serenecode.adapters.hypothesis_adapter import HypothesisPropertyTester

        module_file = tmp_path / "detail_case.py"
        module_file.write_text(
            '''from __future__ import annotations
import icontract


@icontract.require(lambda item: True, "accept any item")
@icontract.ensure(lambda result: result >= 0, "result must be non-negative")
def render(item: myapp.Detail) -> int:
    """Render an external detail model."""
    return 0
''',
            encoding="utf-8",
        )

        tester = HypothesisPropertyTester(max_examples=5, allow_code_execution=True)
        findings = tester.test_module("detail_case", search_paths=(str(tmp_path),))

        assert len(findings) == 1
        assert findings[0].function_name == "render"
        assert findings[0].finding_type == "skipped"

    def test_discover_cli_targets_use_file_line_targets_for_standalone_files(self, tmp_path: Path) -> None:
        from serenecode.adapters.crosshair_adapter import _discover_cli_targets

        module_file = tmp_path / "bad-name.py"
        module_file.write_text(
            '''"""Standalone verification module."""
import icontract


@icontract.require(lambda x: x >= 0, "x must be non-negative")
@icontract.ensure(lambda result: result >= 0, "result must be non-negative")
def identity(x: int) -> int:
    """Return the input unchanged."""
    return x
''',
            encoding="utf-8",
        )

        targets = _discover_cli_targets(str(module_file))

        assert len(targets) == 1
        target, function_name = targets[0]
        assert function_name == "identity"
        assert target.startswith(f"{module_file}:")
        assert not target.endswith(".identity")


class TestModuleLoader:
    """Tests for dynamic module loading behavior."""

    def test_load_python_module_refreshes_updated_module(self, tmp_path: Path) -> None:
        from serenecode.adapters.module_loader import load_python_module

        module_file = tmp_path / "sample.py"
        module_file.write_text("VALUE = 1\n", encoding="utf-8")

        first = load_python_module("sample", (str(tmp_path),))

        module_file.write_text("VALUE = 2\n", encoding="utf-8")
        second = load_python_module("sample", (str(tmp_path),))

        assert first.VALUE == 1
        assert second.VALUE == 2

    def test_load_python_module_refreshes_updated_dependency(self, tmp_path: Path) -> None:
        from serenecode.adapters.module_loader import load_python_module

        package_dir = tmp_path / "pkg"
        package_dir.mkdir()
        (package_dir / "__init__.py").write_text("", encoding="utf-8")
        (package_dir / "dependency.py").write_text("VALUE = 1\n", encoding="utf-8")
        (package_dir / "consumer.py").write_text(
            "from pkg.dependency import VALUE\n\n"
            "def read() -> int:\n"
            "    return VALUE\n",
            encoding="utf-8",
        )

        first = load_python_module("pkg.consumer", (str(tmp_path),))

        (package_dir / "dependency.py").write_text("VALUE = 2\n", encoding="utf-8")
        second = load_python_module("pkg.consumer", (str(tmp_path),))

        assert first.read() == 1
        assert second.read() == 2

    def test_load_python_module_restores_canonical_module_binding(self) -> None:
        import sys

        import serenecode.models as models
        from serenecode.adapters.module_loader import load_python_module

        loaded = load_python_module("serenecode.models")

        assert loaded is not models
        assert sys.modules["serenecode.models"] is models
