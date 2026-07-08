"""Coverage-gap tests for private helpers in serenecode.cli_helpers.

Targets: _echo_spec_traceability_hints, _print_spec_status_for_doctor,
_env_int_or, _maybe_make_dead_code_analyzer, _resolve_effective_level,
and _wire_adapters (including the ImportError warning branches).
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from serenecode.adapters.local_fs import LocalFileReader
from serenecode.cli_helpers import (
    _echo_spec_traceability_hints,
    _env_int_or,
    _maybe_make_dead_code_analyzer,
    _print_spec_status_for_doctor,
    _resolve_effective_level,
    _wire_adapters,
)
from serenecode.config import default_config


class TestEchoSpecTraceabilityHints:
    """Tests for _echo_spec_traceability_hints."""

    def test_explicit_spec_path_returns_after_hint(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (tmp_path / "SERENECODE.md").write_text("# SERENECODE\n", encoding="utf-8")
        (tmp_path / "FEATURE_SPEC.md").write_text("narrative\n", encoding="utf-8")
        _echo_spec_traceability_hints(
            str(tmp_path), LocalFileReader(), spec_explicit_path="SPEC.md"
        )
        err = capsys.readouterr().err
        assert "Preparing a SereneCode-Ready Spec" in err
        # Early return: candidates are never listed for an explicit path.
        assert "Source-like files in project root:" not in err
        assert "FEATURE_SPEC.md" not in err

    def test_lists_narrative_candidates_when_present(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (tmp_path / "SERENECODE.md").write_text("# SERENECODE\n", encoding="utf-8")
        (tmp_path / "FEATURE_SPEC.md").write_text("narrative\n", encoding="utf-8")
        (tmp_path / "PRD.md").write_text("prd\n", encoding="utf-8")
        _echo_spec_traceability_hints(
            str(tmp_path), LocalFileReader(), spec_explicit_path=None
        )
        err = capsys.readouterr().err
        assert "Source-like files in project root:" in err
        assert "FEATURE_SPEC.md" in err
        assert "PRD.md" in err

    def test_no_candidate_listing_when_root_is_clean(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (tmp_path / "SERENECODE.md").write_text("# SERENECODE\n", encoding="utf-8")
        _echo_spec_traceability_hints(
            str(tmp_path), LocalFileReader(), spec_explicit_path=None
        )
        err = capsys.readouterr().err
        assert "Preparing a SereneCode-Ready Spec" in err
        assert "Source-like files in project root:" not in err


class TestPrintSpecStatusForDoctor:
    """Tests for _print_spec_status_for_doctor."""

    def test_reports_spec_and_narrative_files(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        (tmp_path / "SERENECODE.md").write_text("# SERENECODE\n", encoding="utf-8")
        (tmp_path / "SPEC.md").write_text("## REQ-001\n", encoding="utf-8")
        (tmp_path / "FEATURE_SPEC.md").write_text("narrative\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        _print_spec_status_for_doctor(LocalFileReader())
        out = capsys.readouterr().out
        assert "Spec status (project root):" in out
        assert "SPEC.md (traceability):" in out
        assert "not found" not in out
        assert "Narrative / source-like files at root:" in out
        assert "FEATURE_SPEC.md" in out

    def test_reports_missing_spec_and_no_narrative(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        (tmp_path / "SERENECODE.md").write_text("# SERENECODE\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        _print_spec_status_for_doctor(LocalFileReader())
        out = capsys.readouterr().out
        assert "SPEC.md (traceability): not found" in out
        assert "Narrative / source-like files at root: none detected" in out


class TestEnvIntOr:
    """Tests for _env_int_or."""

    ENV_NAME = "SERENECODE_L3COV_TEST_INT"

    def test_unset_uses_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(self.ENV_NAME, raising=False)
        assert _env_int_or(self.ENV_NAME, 7) == 7

    def test_blank_uses_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(self.ENV_NAME, "   ")
        assert _env_int_or(self.ENV_NAME, 9) == 9

    def test_valid_value_is_used(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(self.ENV_NAME, " 42 ")
        assert _env_int_or(self.ENV_NAME, 5) == 42

    def test_value_below_one_is_clamped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(self.ENV_NAME, "0")
        assert _env_int_or(self.ENV_NAME, 5) == 1

    def test_non_integer_uses_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(self.ENV_NAME, "not-an-int")
        assert _env_int_or(self.ENV_NAME, 13) == 13


class TestMaybeMakeDeadCodeAnalyzer:
    """Tests for _maybe_make_dead_code_analyzer."""

    def test_returns_vulture_analyzer_when_available(self) -> None:
        from serenecode.adapters.vulture_adapter import VultureDeadCodeAnalyzer

        analyzer = _maybe_make_dead_code_analyzer()
        assert isinstance(analyzer, VultureDeadCodeAnalyzer)

    def test_returns_unavailable_analyzer_on_import_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from serenecode.adapters.unavailable_dead_code_adapter import (
            UnavailableDeadCodeAnalyzer,
        )

        # A None entry in sys.modules makes `from ... import ...` raise ImportError.
        monkeypatch.setitem(
            sys.modules, "serenecode.adapters.vulture_adapter", None
        )
        analyzer = _maybe_make_dead_code_analyzer()
        assert isinstance(analyzer, UnavailableDeadCodeAnalyzer)
        assert analyzer.reason == "vulture is not installed"


class TestResolveEffectiveLevel:
    """Tests for _resolve_effective_level."""

    def test_structural_forces_level_one(self) -> None:
        config = default_config()
        assert _resolve_effective_level(6, True, True, config) == (1, 1)

    def test_explicit_level_without_verify(self) -> None:
        config = default_config()
        assert _resolve_effective_level(5, False, False, config) == (5, 1)

    def test_explicit_low_level_with_verify_is_raised_to_three(self) -> None:
        config = default_config()
        assert _resolve_effective_level(2, False, True, config) == (3, 3)

    def test_explicit_high_level_with_verify_is_kept(self) -> None:
        config = default_config()
        assert _resolve_effective_level(6, False, True, config) == (6, 3)

    def test_default_level_without_verify_uses_recommended(self) -> None:
        config = default_config()
        assert _resolve_effective_level(None, False, False, config) == (
            config.recommended_level,
            1,
        )

    def test_default_level_with_verify_starts_at_three(self) -> None:
        config = default_config()
        effective, start = _resolve_effective_level(None, False, True, config)
        assert effective == max(config.recommended_level, 3)
        assert start == 3


class _FakeCrossHairSymbolicChecker:
    """Stand-in for CrossHairSymbolicChecker.

    Importing the real adapter imports CrossHair, which monkey-patches
    icontract process-wide and breaks later contract tests (see
    tests/conftest.py). A stub module keeps _wire_adapters' Level 5
    wiring code executable without that side effect.
    """

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


def _stub_crosshair_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = types.ModuleType("serenecode.adapters.crosshair_adapter")
    fake.CrossHairSymbolicChecker = _FakeCrossHairSymbolicChecker  # type: ignore[attr-defined]
    monkeypatch.setitem(
        sys.modules, "serenecode.adapters.crosshair_adapter", fake
    )


class TestWireAdapters:
    """Tests for _wire_adapters."""

    def test_level_six_wires_all_adapters(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _stub_crosshair_adapter(monkeypatch)
        type_checker, coverage, prop, symbolic, dead = _wire_adapters(6, 1, 1, 1, 60)
        assert type_checker is not None
        assert coverage is not None
        assert prop is not None
        assert isinstance(symbolic, _FakeCrossHairSymbolicChecker)
        assert symbolic.kwargs == {
            "per_condition_timeout": 1,
            "per_path_timeout": 1,
            "module_timeout": 1,
            "allow_code_execution": True,
        }
        assert dead is not None
        assert "Warning:" not in capsys.readouterr().err

    def test_level_one_skips_level_gated_adapters(self) -> None:
        type_checker, coverage, prop, symbolic, dead = _wire_adapters(1, 1, 1, 1, 60)
        assert type_checker is None
        assert coverage is None
        assert prop is None
        assert symbolic is None
        # Dead-code analysis is not level-gated.
        assert dead is not None

    def test_missing_backends_warn_and_return_none(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        for module in (
            "serenecode.adapters.mypy_adapter",
            "serenecode.adapters.coverage_adapter",
            "serenecode.adapters.hypothesis_adapter",
            "serenecode.adapters.crosshair_adapter",
            "serenecode.adapters.vulture_adapter",
        ):
            monkeypatch.setitem(sys.modules, module, None)

        type_checker, coverage, prop, symbolic, dead = _wire_adapters(6, 1, 1, 1, 60)
        assert type_checker is None
        assert coverage is None
        assert prop is None
        assert symbolic is None
        assert dead is None

        err = capsys.readouterr().err
        assert "Warning: mypy not available for Level 2 checks." in err
        assert "Warning: coverage not available for Level 3 checks." in err
        assert "Warning: Hypothesis not available for Level 4 checks." in err
        assert "Warning: CrossHair not available for Level 5 checks." in err
        assert "Warning: vulture not available for dead-code analysis." in err
