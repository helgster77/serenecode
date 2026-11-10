"""Tests for core.pipeline_helpers module."""

from __future__ import annotations

from serenecode.config import default_config
from serenecode.core.pipeline import SourceFile
from serenecode.core.pipeline_helpers import check_test_existence
from serenecode.models import CheckStatus


def _source_file(file_path: str, module_path: str) -> SourceFile:
    return SourceFile(file_path=file_path, module_path=module_path, source="X = 1\n")


def test_production_module_under_a_tests_ancestor_still_needs_a_test_file() -> None:
    """A checkout located below a directory named tests is not a test suite.

    Verifies: REQ-055
    """
    source_file = _source_file("/home/ci/tests/proj/src/pkg/calc.py", "pkg/calc.py")

    results = check_test_existence((source_file,), frozenset({"test_other"}), default_config())

    assert [result.status for result in results] == [CheckStatus.FAILED]
    assert "No test file found for 'calc'" in results[0].details[0].message


def test_project_test_files_are_skipped_by_the_presence_check() -> None:
    """Files inside the project's own tests directory need no test file.

    Verifies: REQ-055
    """
    source_file = _source_file("tests/unit/test_calc.py", "tests/unit/test_calc.py")

    results = check_test_existence((source_file,), frozenset({"test_other"}), default_config())

    assert results == []


def test_module_with_matching_test_file_passes() -> None:
    """A module whose test_<stem> file exists gets a passing record."""
    source_file = _source_file("src/pkg/calc.py", "pkg/calc.py")

    results = check_test_existence((source_file,), frozenset({"test_calc"}), default_config())

    assert [result.status for result in results] == [CheckStatus.PASSED]
