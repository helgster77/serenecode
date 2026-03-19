"""Tests for the Serenecode public library API (__init__.py)."""

from __future__ import annotations

from pathlib import Path

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
from serenecode.models import CheckResult


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


class TestLibraryApiStubs:
    """Tests for stub API functions (Levels 2-5)."""

    def test_check_types_returns_check_result(self) -> None:
        result = check_types(".")
        assert isinstance(result, CheckResult)
        assert result.passed is True

    def test_check_properties_returns_check_result(self) -> None:
        result = check_properties(".")
        assert isinstance(result, CheckResult)
        assert result.passed is True

    def test_check_symbolic_returns_check_result(self) -> None:
        result = check_symbolic(".")
        assert isinstance(result, CheckResult)
        assert result.passed is True

    def test_check_compositional_returns_check_result(self) -> None:
        result = check_compositional(".")
        assert isinstance(result, CheckResult)
        assert result.passed is True


class TestLibraryApiCheck:
    """Tests for the check() function."""

    def test_check_returns_check_result(self, tmp_path: Path) -> None:
        source = '"""Module doc."""\n'
        (tmp_path / "test.py").write_text(source, encoding="utf-8")
        result = check(str(tmp_path))
        assert isinstance(result, CheckResult)

    def test_check_with_level(self, tmp_path: Path) -> None:
        source = '"""Module doc."""\n'
        (tmp_path / "test.py").write_text(source, encoding="utf-8")
        result = check(str(tmp_path), level=1)
        assert isinstance(result, CheckResult)


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
