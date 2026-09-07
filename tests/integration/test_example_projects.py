"""Integration tests for shipped example projects."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from serenecode.adapters.crosshair_adapter import CrossHairSymbolicChecker
from serenecode.adapters.hypothesis_adapter import HypothesisPropertyTester
from serenecode.adapters.local_fs import LocalFileReader
from serenecode.config import strict_config
from serenecode.core.pipeline import run_pipeline
from serenecode.source_discovery import build_source_files
from serenecode.models import CheckResult

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLES_DIR = _REPO_ROOT / "examples"
_BUNDLED_SERENECODE_EXAMPLE_SRC = next(
    (
        p / "src"
        for p in sorted(_EXAMPLES_DIR.iterdir())
        if p.is_dir() and p.name.endswith("-serenecode") and (p / "src").is_dir()
    ),
    None,
)


def _failure_details(result: CheckResult) -> str:
    """Retain actionable failure details without hundreds of successful records."""
    return json.dumps({
        "level_achieved": result.level_achieved,
        "summary": result.summary.to_dict(),
        "findings": [r.to_dict() for r in result.results if r.status.value in ("failed", "skipped")],
    }, indent=2)


@pytest.mark.slow
def test_bundled_example_project_passes_strict_level_6() -> None:
    """The bundled reference example should satisfy the strict pipeline.

    This exercises L4–L6 only. It does not establish strict structural,
    type, or coverage compliance. The CLI example check separately runs L1–L6.
    """
    assert _BUNDLED_SERENECODE_EXAMPLE_SRC is not None, "expected *-serenecode under examples/"
    root = str(_BUNDLED_SERENECODE_EXAMPLE_SRC)
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

    assert result.passed is True, _failure_details(result)
    assert result.level_requested == 6
    assert result.level_achieved == 6
    assert result.summary.failed_count == 0


@pytest.mark.slow
def test_serenecode_repo_passes_strict_level_6() -> None:
    """The main Serenecode package should satisfy the strict pipeline too.

    This exercises L4–L6 only. Starting at L4 avoids recursively running
    pytest under L3 coverage from within pytest. The separate CLI self-check
    runs L1–L6 with the repository's default configuration.
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

    assert result.passed is True, _failure_details(result)
    assert result.level_requested == 6
    assert result.level_achieved == 6
    assert result.summary.failed_count == 0
