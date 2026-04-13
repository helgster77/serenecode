"""Minimal tests for checker.crosshair_parsing module."""

from __future__ import annotations

from serenecode.support.crosshair_parsing import _parse_counterexample


def test_parse_counterexample_with_valid_message() -> None:
    """Extracts key=value pairs from a CrossHair counterexample message."""
    msg = "error when calling foo(x=1, y=-3)"
    result = _parse_counterexample(msg)
    assert result is not None
    assert result["x"] == "1"
    assert result["y"] == "-3"


def test_parse_counterexample_no_match() -> None:
    """Returns None when the message has no counterexample."""
    assert _parse_counterexample("some random error") is None
