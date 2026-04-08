"""Integration tests for the unavailable dead-code adapter."""

from __future__ import annotations

import pytest

from serenecode.adapters.unavailable_dead_code_adapter import UnavailableDeadCodeAnalyzer


class TestUnavailableDeadCodeAnalyzer:
    """Tests for UnavailableDeadCodeAnalyzer."""

    def test_analyze_paths_raises_reason(self) -> None:
        analyzer = UnavailableDeadCodeAnalyzer("vulture is not installed")
        with pytest.raises(RuntimeError, match="vulture is not installed"):
            analyzer.analyze_paths(("src/example.py",))
