"""Vulture-backed dead-code analyzer adapter.

This adapter integrates the third-party Vulture static analyzer and
projects its findings into Serenecode's dead-code analyzer port.

This module is an adapter layer — it performs file I/O and depends on
an external library.
"""

from __future__ import annotations

from pathlib import Path

import icontract

from serenecode.ports.dead_code_analyzer import DeadCodeAnalyzer, DeadCodeFinding

try:
    from vulture import Vulture  # type: ignore[import-untyped]
except ImportError as exc:  # pragma: no cover - import-time dependency gate
    raise ImportError(
        "The 'vulture' package is not installed. Install with: "
        "uv add vulture or pip install vulture",
    ) from exc


# no-invariant: stateless adapter over the Vulture library
class VultureDeadCodeAnalyzer(DeadCodeAnalyzer):
    """Dead-code analyzer backed by the Vulture library."""

    @icontract.require(
        lambda paths: isinstance(paths, tuple) and len(paths) > 0,
        "paths must be a non-empty tuple",
    )
    @icontract.require(
        lambda min_confidence: isinstance(min_confidence, int) and 0 <= min_confidence <= 100,
        "min_confidence must be between 0 and 100",
    )
    @icontract.ensure(
        lambda result: isinstance(result, list),
        "result must be a list",
    )
    def analyze_paths(
        self,
        paths: tuple[str, ...],
        min_confidence: int = 60,
    ) -> list[DeadCodeFinding]:
        """Analyze source paths for likely dead code."""
        analyzer = Vulture()
        analyzer.scavenge(list(paths))
        findings: list[DeadCodeFinding] = []

        # Loop invariant: findings contains unsuppressed items from unused_code[0..i]
        for item in analyzer.get_unused_code():
            file_path = str(getattr(item, "filename", ""))
            line_no = int(getattr(item, "first_lineno", 1))
            confidence = int(getattr(item, "confidence", 0))
            if confidence < min_confidence:
                continue
            if not file_path:
                continue
            if _is_allowlisted(file_path, line_no):
                continue

            symbol_name = str(getattr(item, "name", "<unknown>"))
            symbol_type = str(getattr(item, "typ", "symbol"))
            message = str(getattr(item, "message", "")).strip()
            if not message:
                message = (
                    f"Unused {symbol_type} '{symbol_name}' "
                    f"({confidence}% confidence)"
                )
            findings.append(DeadCodeFinding(
                symbol_name=symbol_name,
                file_path=file_path,
                line=line_no,
                symbol_type=symbol_type,
                confidence=confidence,
                message=message,
            ))

        return findings


@icontract.require(lambda file_path: isinstance(file_path, str) and len(file_path) > 0, "file_path must be non-empty")
@icontract.require(lambda line_no: isinstance(line_no, int) and line_no >= 1, "line_no must be >= 1")
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a boolean")
def _is_allowlisted(file_path: str, line_no: int) -> bool:
    """Return True if a nearby allow-unused comment suppresses the finding."""
    try:
        lines = Path(file_path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return False

    candidate_indexes = (
        line_no - 1,
        line_no - 2,
    )
    # Loop invariant: no checked candidate line contained an allow-unused marker
    for index in candidate_indexes:
        if not 0 <= index < len(lines):
            continue
        if "allow-unused:" in lines[index]:
            return True
    return False
