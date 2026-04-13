"""Tests for serenecode_module_health MCP tool.

Verifies: REQ-033, REQ-034
"""

from __future__ import annotations

import os
import tempfile

import pytest

from serenecode.mcp.tools import tool_module_health


class TestToolModuleHealth:
    """Verifies: INT-004"""

    def test_returns_metrics_for_known_file(self):
        """Verifies: REQ-033"""
        source = (
            "class Foo:\n"
            "    def a(self): pass\n"
            "    def b(self): pass\n"
            "\n"
            "def bar(x, y, z):\n"
            "    pass\n"
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(source)
            f.flush()
            try:
                result = tool_module_health(f.name)
                assert "file" in result
                assert "metrics" in result
                metrics = result["metrics"]
                assert metrics["line_count"] == 6
                assert metrics["function_count"] == 3  # a, b, bar
                assert metrics["class_count"] == 1
            finally:
                os.unlink(f.name)

    def test_status_reflects_thresholds(self):
        """Verifies: REQ-033"""
        source = "def foo():\n    pass\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(source)
            f.flush()
            try:
                result = tool_module_health(f.name)
                status = result["status"]
                assert status["file_length"] == "ok"
                assert status["function_length"] == "ok"
                assert status["parameter_count"] == "ok"
                assert status["class_method_count"] == "ok"
            finally:
                os.unlink(f.name)

    def test_empty_file(self):
        """Verifies: REQ-033"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("")
            f.flush()
            try:
                result = tool_module_health(f.name)
                assert result["metrics"]["line_count"] == 0
                assert result["metrics"]["function_count"] == 0
            finally:
                os.unlink(f.name)

    def test_file_not_found(self):
        """Verifies: REQ-034"""
        result = tool_module_health("/nonexistent/path.py")
        assert "error" in result

    def test_does_not_run_pipeline(self):
        """Verifies: REQ-034"""
        source = "def foo():\n    pass\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(source)
            f.flush()
            try:
                result = tool_module_health(f.name)
                # No pipeline-specific keys like 'passed', 'findings', 'verdict'
                assert "passed" not in result
                assert "findings" not in result
                assert "verdict" not in result
            finally:
                os.unlink(f.name)
