"""Integration tests for shipped example projects."""

from __future__ import annotations

import pytest

from serenecode.adapters.crosshair_adapter import CrossHairSymbolicChecker
from serenecode.adapters.hypothesis_adapter import HypothesisPropertyTester
from serenecode.adapters.local_fs import LocalFileReader
from serenecode.adapters.mypy_adapter import MypyTypeChecker
from serenecode.config import strict_config
from serenecode.core.pipeline import run_pipeline
from serenecode.source_discovery import build_source_files


@pytest.mark.slow
def test_dosage_serenecode_example_passes_strict_level_5() -> None:
    """The shipped Serenecode example should satisfy the strict pipeline too."""
    root = "examples/dosage-serenecode/src"
    reader = LocalFileReader()
    files = reader.list_python_files(root)
    source_files = build_source_files(files, reader, root)

    result = run_pipeline(
        source_files=source_files,
        level=5,
        start_level=1,
        config=strict_config(),
        type_checker=MypyTypeChecker(),
        property_tester=HypothesisPropertyTester(allow_code_execution=True),
        symbolic_checker=CrossHairSymbolicChecker(allow_code_execution=True),
        max_workers=4,
    )

    assert result.passed is True
    assert result.level_requested == 5
    assert result.level_achieved == 5
    assert result.summary.failed_count == 0


@pytest.mark.slow
def test_serenecode_repo_passes_strict_level_5() -> None:
    """The main Serenecode package should satisfy the strict pipeline too."""
    root = "src"
    reader = LocalFileReader()
    files = reader.list_python_files(root)
    source_files = build_source_files(files, reader, root)

    result = run_pipeline(
        source_files=source_files,
        level=5,
        start_level=1,
        config=strict_config(),
        type_checker=MypyTypeChecker(),
        property_tester=HypothesisPropertyTester(allow_code_execution=True),
        symbolic_checker=CrossHairSymbolicChecker(allow_code_execution=True),
        max_workers=4,
    )

    assert result.passed is True
    assert result.level_requested == 5
    assert result.level_achieved == 5
    assert result.summary.failed_count == 0
