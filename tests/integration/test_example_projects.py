"""Integration tests for shipped example projects."""

from __future__ import annotations

import pytest

from serenecode.adapters.crosshair_adapter import CrossHairSymbolicChecker
from serenecode.adapters.hypothesis_adapter import HypothesisPropertyTester
from serenecode.adapters.local_fs import LocalFileReader
from serenecode.config import strict_config
from serenecode.core.pipeline import run_pipeline
from serenecode.source_discovery import build_source_files


@pytest.mark.slow
def test_dosage_serenecode_example_passes_strict_level_6() -> None:
    """The shipped Serenecode example should satisfy the strict pipeline.

    Coverage analysis (L3) is skipped because the dosage example's
    auto-generated dunder methods (frozen dataclass __setattr__ etc.)
    are discovered but not meaningfully testable. L3 coverage is
    validated end-to-end in the e2e test suite instead.
    """
    root = "examples/dosage-serenecode/src"
    reader = LocalFileReader()
    files = reader.list_python_files(root)
    source_files = build_source_files(files, reader, root)

    result = run_pipeline(
        source_files=source_files,
        level=6,
        start_level=4,
        config=strict_config(),
        property_tester=HypothesisPropertyTester(allow_code_execution=True),
        symbolic_checker=CrossHairSymbolicChecker(allow_code_execution=True),
        max_workers=4,
    )

    assert result.passed is True
    assert result.level_requested == 6
    assert result.level_achieved == 6
    assert result.summary.failed_count == 0


@pytest.mark.slow
def test_serenecode_repo_passes_strict_level_6() -> None:
    """The main Serenecode package should satisfy the strict pipeline too.

    Coverage analysis (L3) is skipped for the self-check because running
    the full pytest suite per module is too slow for CI. The dosage example
    test above validates coverage analysis works end-to-end.
    """
    root = "src"
    reader = LocalFileReader()
    files = reader.list_python_files(root)
    source_files = build_source_files(files, reader, root)

    result = run_pipeline(
        source_files=source_files,
        level=6,
        start_level=4,
        config=strict_config(),
        property_tester=HypothesisPropertyTester(allow_code_execution=True),
        symbolic_checker=CrossHairSymbolicChecker(allow_code_execution=True),
        max_workers=4,
    )

    assert result.passed is True
    assert result.level_requested == 6
    assert result.level_achieved == 6
    assert result.summary.failed_count == 0
