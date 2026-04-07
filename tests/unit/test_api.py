"""Tests for the Serenecode public library API (__init__.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

import serenecode
from serenecode import (
    check,
    check_compositional,
    check_properties,
    check_structural,
    check_symbolic,
    check_types,
    init,
    status,
)
from serenecode.core.exceptions import UnsafeCodeExecutionError
from serenecode.models import CheckResult, make_check_result


class TestLibraryApiStructural:
    """Tests for the check_structural library function."""

    def test_check_structural_on_valid_file(self, tmp_path: Path) -> None:
        source = '''\
"""Module docstring."""

import icontract


@icontract.require(lambda x: x >= 0, "x non-neg")
@icontract.ensure(lambda result: result >= 0, "result non-neg")
def square(x: float) -> float:
    """Square a number."""
    return x * x
'''
        (tmp_path / "test.py").write_text(source, encoding="utf-8")
        result = check_structural(str(tmp_path / "test.py"))
        assert isinstance(result, CheckResult)
        assert result.passed is True

    def test_check_structural_on_invalid_file(self, tmp_path: Path) -> None:
        source = '''\
"""Module docstring."""

def broken(x: int, y: int) -> int:
    """No contracts."""
    return x + y
'''
        (tmp_path / "test.py").write_text(source, encoding="utf-8")
        result = check_structural(str(tmp_path / "test.py"))
        assert isinstance(result, CheckResult)
        assert result.passed is False

    def test_check_structural_on_directory(self, tmp_path: Path) -> None:
        source = '"""Empty module."""\n'
        (tmp_path / "a.py").write_text(source, encoding="utf-8")
        result = check_structural(str(tmp_path))
        assert isinstance(result, CheckResult)


class TestLibraryApiLevelWrappers:
    """Tests for API helper wrappers around _run_check()."""

    @pytest.mark.parametrize(
        ("func", "expected_level"),
        [
            (check_types, 2),
            (check_properties, 4),
            (check_symbolic, 5),
            (check_compositional, 6),
        ],
    )
    def test_level_wrapper_calls_run_check(
        self,
        monkeypatch: pytest.MonkeyPatch,
        func: object,
        expected_level: int,
    ) -> None:
        captured: dict[str, object] = {}

        def fake_run_check(
            path: str,
            level: int,
            allow_code_execution: bool = False,
        ) -> CheckResult:
            captured["path"] = path
            captured["level"] = level
            captured["allow_code_execution"] = allow_code_execution
            return make_check_result((), level_requested=level, duration_seconds=0.0)

        monkeypatch.setattr(serenecode, "_run_check", fake_run_check)

        result = func("demo.py")
        assert isinstance(result, CheckResult)
        assert captured == {
            "path": "demo.py",
            "level": expected_level,
            "allow_code_execution": False,
        }


class TestLibraryApiCheck:
    """Tests for the check() function."""

    def test_check_returns_check_result(self, tmp_path: Path) -> None:
        source = '"""Module doc."""\n'
        (tmp_path / "test.py").write_text(source, encoding="utf-8")
        result = check(str(tmp_path), level=1)
        assert isinstance(result, CheckResult)

    def test_check_with_level(self, tmp_path: Path) -> None:
        source = '"""Module doc."""\n'
        (tmp_path / "test.py").write_text(source, encoding="utf-8")
        result = check(str(tmp_path), level=1)
        assert isinstance(result, CheckResult)

    def test_deep_check_requires_explicit_code_execution_consent(self, tmp_path: Path) -> None:
        source = '"""Module doc."""\n'
        (tmp_path / "test.py").write_text(source, encoding="utf-8")

        with pytest.raises(UnsafeCodeExecutionError):
            check(str(tmp_path), level=3)


class TestLibraryApiStatus:
    """Tests for the status() function."""

    def test_status_returns_check_result(self, tmp_path: Path) -> None:
        source = '"""Module doc."""\n'
        (tmp_path / "test.py").write_text(source, encoding="utf-8")
        result = status(str(tmp_path))
        assert isinstance(result, CheckResult)


class TestLibraryApiInit:
    """Tests for the init() function."""

    def test_init_creates_files(self, tmp_path: Path) -> None:
        result = init(str(tmp_path), template="default")
        assert result.serenecode_md_created is True
        assert (tmp_path / "SERENECODE.md").exists()
        assert (tmp_path / "CLAUDE.md").exists()

    def test_init_strict_template(self, tmp_path: Path) -> None:
        result = init(str(tmp_path), template="strict")
        assert result.template_used == "strict"

    def test_init_minimal_template(self, tmp_path: Path) -> None:
        result = init(str(tmp_path), template="minimal")
        assert result.template_used == "minimal"


class TestLibraryApiCheckCoverage:
    """Tests for check_coverage — covers __init__.check_coverage line 137."""

    def test_check_coverage_requires_explicit_consent(self, tmp_path: Path) -> None:
        source = '"""Module doc."""\n'
        (tmp_path / "test.py").write_text(source, encoding="utf-8")
        with pytest.raises(UnsafeCodeExecutionError):
            serenecode.check_coverage(str(tmp_path))

    def test_check_coverage_with_consent_returns_l3_result(self, tmp_path: Path) -> None:
        # Minimal project that will run through L3 cleanly
        (tmp_path / "module.py").write_text(
            '"""Module."""\n'
            'import icontract\n'
            '@icontract.require(lambda x: x > 0, "x positive")\n'
            '@icontract.ensure(lambda x, result: result == x * 2, "double")\n'
            'def double(x: int) -> int:\n'
            '    """Return double of x."""\n'
            '    return x * 2\n',
            encoding="utf-8",
        )
        result = serenecode.check_coverage(str(tmp_path), allow_code_execution=True)
        assert isinstance(result, CheckResult)
        assert result.level_requested == 3


class TestRunCheckBranches:
    """Tests targeting uncovered branches in serenecode._run_check."""

    def test_run_check_loads_serenecode_md_when_present(self, tmp_path: Path) -> None:
        """Branch (lines 243-244): SERENECODE.md exists → parse_serenecode_md."""
        (tmp_path / "SERENECODE.md").write_text("Template: minimal\n", encoding="utf-8")
        (tmp_path / "module.py").write_text('"""Doc."""\n', encoding="utf-8")
        result = check(str(tmp_path), level=1)
        # Just confirm the call returned a result — config was parsed from SERENECODE.md
        assert isinstance(result, CheckResult)

    def test_run_check_wires_mypy_at_level_2(self, tmp_path: Path) -> None:
        """Branch (lines 259-262): level >= 2 wires MypyTypeChecker."""
        (tmp_path / "SERENECODE.md").write_text("Template: minimal\n", encoding="utf-8")
        (tmp_path / "module.py").write_text(
            '"""Module."""\n'
            'def f(x: int) -> int:\n'
            '    """Doc."""\n'
            '    return x\n',
            encoding="utf-8",
        )
        result = check(str(tmp_path), level=2)
        assert isinstance(result, CheckResult)
        assert result.level_requested == 2

    def test_run_check_wires_coverage_at_level_3(self, tmp_path: Path) -> None:
        """Branch (lines 266-269): level >= 3 wires CoverageAnalyzerAdapter."""
        (tmp_path / "SERENECODE.md").write_text("Template: minimal\n", encoding="utf-8")
        (tmp_path / "module.py").write_text(
            '"""Module."""\n'
            'def f() -> int:\n'
            '    """Doc."""\n'
            '    return 1\n',
            encoding="utf-8",
        )
        result = check(str(tmp_path), level=3, allow_code_execution=True)
        assert isinstance(result, CheckResult)
        assert result.level_requested == 3

    def test_run_check_wires_hypothesis_at_level_4(self, tmp_path: Path) -> None:
        """Branch (lines 273-276): level >= 4 wires HypothesisPropertyTester."""
        (tmp_path / "SERENECODE.md").write_text("Template: minimal\n", encoding="utf-8")
        (tmp_path / "module.py").write_text(
            '"""Module."""\n'
            'def f() -> int:\n'
            '    """Doc."""\n'
            '    return 1\n',
            encoding="utf-8",
        )
        result = check(str(tmp_path), level=4, allow_code_execution=True)
        assert isinstance(result, CheckResult)
        assert result.level_requested == 4

    def test_run_check_wires_crosshair_at_level_5(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Branch (lines 280-283): level >= 5 wires CrossHairSymbolicChecker.

        Importing CrossHair monkey-patches icontract for the entire process,
        which pollutes other tests. We mock the adapter import so the wiring
        branch executes without actually loading CrossHair.
        """
        from unittest.mock import MagicMock
        import sys

        # Stub the crosshair adapter module so the lazy import inside _run_check
        # doesn't actually load CrossHair.
        fake_module = MagicMock()
        fake_module.CrossHairSymbolicChecker = MagicMock(return_value=MagicMock())
        monkeypatch.setitem(
            sys.modules,
            "serenecode.adapters.crosshair_adapter",
            fake_module,
        )

        (tmp_path / "SERENECODE.md").write_text("Template: minimal\n", encoding="utf-8")
        (tmp_path / "module.py").write_text(
            '"""Module."""\n'
            'def f() -> int:\n'
            '    """Doc."""\n'
            '    return 1\n',
            encoding="utf-8",
        )
        result = check(str(tmp_path), level=5, allow_code_execution=True)
        assert isinstance(result, CheckResult)
        assert result.level_requested == 5
        # Confirm the stubbed adapter was instantiated
        fake_module.CrossHairSymbolicChecker.assert_called()
