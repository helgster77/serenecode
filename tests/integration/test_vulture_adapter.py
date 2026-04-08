"""Integration tests for the Vulture-backed dead-code adapter."""

from __future__ import annotations

import textwrap
from pathlib import Path

from serenecode.adapters.vulture_adapter import VultureDeadCodeAnalyzer


class TestVultureDeadCodeAnalyzer:
    """Tests for VultureDeadCodeAnalyzer."""

    def test_reports_unused_function(self, tmp_path: Path) -> None:
        path = tmp_path / "module.py"
        path.write_text(textwrap.dedent("""\
            def stale() -> int:
                return 1
        """), encoding="utf-8")

        analyzer = VultureDeadCodeAnalyzer()
        findings = analyzer.analyze_paths((str(path),))

        assert len(findings) >= 1
        assert findings[0].symbol_name == "stale"
        assert findings[0].symbol_type == "function"

    def test_allow_unused_comment_suppresses_finding(self, tmp_path: Path) -> None:
        path = tmp_path / "module.py"
        path.write_text(textwrap.dedent("""\
            # allow-unused: CLI entrypoint
            def stale() -> int:
                return 1
        """), encoding="utf-8")

        analyzer = VultureDeadCodeAnalyzer()
        findings = analyzer.analyze_paths((str(path),))

        assert findings == []
