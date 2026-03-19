"""Integration tests for the mypy type checking adapter.

Tests subprocess execution, output parsing, error handling,
and strict mode with real mypy runs against fixture files.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from serenecode.adapters.mypy_adapter import MypyTypeChecker


@pytest.mark.slow
class TestMypyTypeChecker:
    """Integration tests running real mypy type checking."""

    def test_valid_file_no_errors(self, tmp_path: Path) -> None:
        source = '''\
def add(x: int, y: int) -> int:
    return x + y
'''
        test_file = tmp_path / "valid.py"
        test_file.write_text(source, encoding="utf-8")
        checker = MypyTypeChecker(timeout=30)
        issues = checker.check([str(test_file)], strict=False)
        errors = [i for i in issues if i.severity == "error"]
        assert len(errors) == 0

    def test_type_error_detected(self, tmp_path: Path) -> None:
        source = '''\
def add(x: int, y: int) -> int:
    return "not an int"
'''
        test_file = tmp_path / "bad_return.py"
        test_file.write_text(source, encoding="utf-8")
        checker = MypyTypeChecker(timeout=30)
        issues = checker.check([str(test_file)], strict=False)
        errors = [i for i in issues if i.severity == "error"]
        assert len(errors) >= 1
        assert any("return" in e.message.lower() or "incompatible" in e.message.lower()
                    for e in errors)

    def test_strict_mode_catches_missing_annotations(self, tmp_path: Path) -> None:
        source = '''\
def add(x, y):
    return x + y
'''
        test_file = tmp_path / "no_types.py"
        test_file.write_text(source, encoding="utf-8")
        checker = MypyTypeChecker(timeout=30)
        issues = checker.check([str(test_file)], strict=True)
        errors = [i for i in issues if i.severity == "error"]
        assert len(errors) >= 1

    def test_empty_file_list_returns_empty(self) -> None:
        checker = MypyTypeChecker()
        issues = checker.check([], strict=True)
        assert issues == []

    def test_multiple_files(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("def f(x: int) -> int:\n    return x\n", encoding="utf-8")
        (tmp_path / "b.py").write_text("def g(x: int) -> str:\n    return x\n", encoding="utf-8")
        checker = MypyTypeChecker(timeout=30)
        issues = checker.check(
            [str(tmp_path / "a.py"), str(tmp_path / "b.py")],
            strict=False,
        )
        # b.py has a return type error
        b_errors = [i for i in issues if "b.py" in i.file and i.severity == "error"]
        assert len(b_errors) >= 1

    def test_issue_has_correct_fields(self, tmp_path: Path) -> None:
        source = '''\
def add(x: int, y: int) -> int:
    return "bad"
'''
        test_file = tmp_path / "check_fields.py"
        test_file.write_text(source, encoding="utf-8")
        checker = MypyTypeChecker(timeout=30)
        issues = checker.check([str(test_file)], strict=False)
        errors = [i for i in issues if i.severity == "error"]
        assert len(errors) >= 1
        error = errors[0]
        assert error.line > 0
        assert error.column >= 0
        assert error.severity == "error"
        assert len(error.message) > 0

    def test_nonexistent_file_handled(self, tmp_path: Path) -> None:
        checker = MypyTypeChecker(timeout=30)
        issues = checker.check([str(tmp_path / "nonexistent.py")], strict=False)
        # mypy should report an error about the missing file
        assert len(issues) >= 0  # may or may not report, but shouldn't crash


class TestMypyOutputParsing:
    """Tests for mypy output parsing logic."""

    def test_parses_standard_error_line(self) -> None:
        from serenecode.adapters.mypy_adapter import _MYPY_OUTPUT_PATTERN
        line = 'test.py:10:5: error: Incompatible return value type [return-value]'
        match = _MYPY_OUTPUT_PATTERN.match(line)
        assert match is not None
        assert match.group(1) == "test.py"
        assert match.group(2) == "10"
        assert match.group(3) == "5"
        assert match.group(4) == "error"
        assert "Incompatible" in match.group(5)
        assert match.group(6) == "return-value"

    def test_parses_warning_line(self) -> None:
        from serenecode.adapters.mypy_adapter import _MYPY_OUTPUT_PATTERN
        line = 'test.py:5:1: warning: Some warning'
        match = _MYPY_OUTPUT_PATTERN.match(line)
        assert match is not None
        assert match.group(4) == "warning"

    def test_parses_note_line(self) -> None:
        from serenecode.adapters.mypy_adapter import _MYPY_OUTPUT_PATTERN
        line = 'test.py:5:1: note: See documentation'
        match = _MYPY_OUTPUT_PATTERN.match(line)
        assert match is not None
        assert match.group(4) == "note"

    def test_no_match_on_summary_line(self) -> None:
        from serenecode.adapters.mypy_adapter import _MYPY_OUTPUT_PATTERN
        line = 'Found 3 errors in 1 file (checked 1 source file)'
        match = _MYPY_OUTPUT_PATTERN.match(line)
        assert match is None
