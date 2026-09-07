"""Regression tests for symbol identity, blockers, and project traceability scope."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from serenecode.cli import main
from serenecode.mcp.tools import reset_state, tool_check, tool_check_file, tool_check_function, tool_verify_fixed


SOURCE = '''"""Arithmetic helpers."""
import icontract

@icontract.require(lambda x: x >= 0, "x is non-negative")
@icontract.ensure(lambda x, result: result == x * x, "result is the square")
def square(x: int) -> int:
    """Square the input."""
    return x * x
'''


@pytest.fixture
def project(tmp_path: Path) -> Path:
    reset_state()
    (tmp_path / "pyproject.toml").write_text('[project]\nname="scope-fixture"\nversion="0.0.0"\n')
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "math_ops.py").write_text(SOURCE)
    return tmp_path


def test_missing_function_cannot_pass_or_confirm_a_fix(project: Path) -> None:
    path = str(project / "src" / "math_ops.py")
    checked = tool_check_function(path, "missing")
    fixed = tool_verify_fixed(path, "missing", "postcondition")
    assert checked["passed"] is False
    assert checked["findings"][0]["finding_type"] == "invalid_target"
    assert fixed["fixed"] is False


def test_empty_and_invalid_source_are_explicit_target_failures(project: Path) -> None:
    path = project / "src" / "math_ops.py"
    for source in ("", "def broken("):
        path.write_text(source)
        response = tool_check_function(str(path), "square")
        assert response["passed"] is False
        assert response["findings"][0]["finding_type"] == "invalid_target"


def test_qualified_method_selects_the_correct_definition(project: Path) -> None:
    path = project / "src" / "math_ops.py"
    path.write_text('"""Methods."""\nimport icontract\n\n' + '''
# no-invariant: stateless namespace
class Good:
    """A valid method."""
    @staticmethod
    @icontract.require(lambda x: x >= 0, "non-negative")
    @icontract.ensure(lambda result: result >= 0, "non-negative")
    def square(x: int) -> int:
        """Square the input."""
        return x * x

# no-invariant: stateless namespace
class Bad:
    """A method with missing contracts."""
    @staticmethod
    def square(x: int) -> int:
        """Square the input."""
        return x * x
''')
    ambiguous = tool_check_function(str(path), "square")
    assert ambiguous["passed"] is False
    assert "ambiguous" in ambiguous["findings"][0]["message"]
    result = tool_check_function(str(path), "Bad.square")
    assert result["passed"] is False
    assert any("missing @icontract.ensure" in f["message"] for f in result["findings"])


def test_type_error_remains_visible_in_function_response(project: Path) -> None:
    path = project / "src" / "math_ops.py"
    path.write_text(SOURCE.replace("return x * x", 'return "wrong type"'))
    response = tool_check_function(str(path), "square", level=2)
    assert response["passed"] is False
    assert any("return-value" in f["message"] for f in response["findings"])


def test_function_with_its_own_coverage_evidence_passes(project: Path) -> None:
    from serenecode.mcp.tools import get_state

    tests = project / "tests"
    tests.mkdir()
    (tests / "test_math_ops.py").write_text(
        "from math_ops import square\n\ndef test_square():\n    assert square(3) == 9\n"
    )
    get_state().allow_code_execution = True
    response = tool_check_function(str(project / "src" / "math_ops.py"), "square", level=3)
    assert response["passed"] is True, response
    assert response["level_achieved"] == 3
    assert response["summary"]["passed"] >= 2


def test_sibling_failure_explains_why_target_verification_stopped(project: Path) -> None:
    path = project / "src" / "math_ops.py"
    path.write_text(SOURCE + '\ndef broken(x):\n    return x\n')
    response = tool_check_function(str(path), "square", level=2)
    assert response["passed"] is False
    assert any(f["function"] == "broken" and f["status"] == "failed" for f in response["findings"])
    assert tool_verify_fixed(str(path), "square", "return-value", level=2)["fixed"] is False


@pytest.mark.parametrize("foreign_file", [False, True])
def test_per_function_check_does_not_borrow_another_targets_deep_evidence(project: Path, monkeypatch: pytest.MonkeyPatch, foreign_file: bool) -> None:
    from serenecode.mcp import tools
    from serenecode.models import CheckStatus, FunctionResult, make_check_result

    path = str(project / "src" / "math_ops.py")
    other_path = str(project / "src" / "other_ops.py") if foreign_file else path
    other_name = "square" if foreign_file else "elsewhere"
    results = (FunctionResult("square", path, 6, 1, 1, CheckStatus.PASSED),) + tuple(
        FunctionResult(other_name, other_path, 1, level, level, CheckStatus.PASSED) for level in (3, 4)
    )
    monkeypatch.setattr(tools, "_wire_adapters", lambda level: {})
    monkeypatch.setattr(tools, "run_pipeline", lambda **kwargs: make_check_result(results, 4, 0.0, level_achieved=4))
    tools.get_state().allow_code_execution = True
    response = tool_check_function(path, "square", level=4)
    assert response["passed"] is False
    assert response["level_achieved"] == 2
    assert any(f["finding_type"] == "not_exercised" for f in response["findings"])


def test_exempt_function_does_not_inherit_a_passing_file_level(project: Path) -> None:
    adapters = project / "src" / "adapters"
    adapters.mkdir()
    path = adapters / "arithmetic.py"
    path.write_text(SOURCE)
    result = tool_check_function(str(path), "square")
    assert result["passed"] is False
    assert result["level_achieved"] == 0
    assert any(f["finding_type"] == "not_exercised" for f in result["findings"])


def test_scoped_checks_use_all_project_implementations(project: Path) -> None:
    (project / "SPEC.md").write_text(
        "**Source:** none — authoritative fixture.\n\n"
        "### REQ-001: Square values\n\n### REQ-002: Square other values\n"
    )
    tests = project / "tests"
    tests.mkdir()
    for index, module in enumerate(("math_ops", "other_ops"), 1):
        (project / "src" / f"{module}.py").write_text(SOURCE.replace("Square the input.", f"Square the input.\n\n    Implements: REQ-00{index}"))
        (tests / f"test_{module}.py").write_text(
            f'from {module} import square\n\ndef test_square():\n'
            f'    """Verifies: REQ-00{index}"""\n    assert square(2) == 4\n'
        )
    path = str(project / "src" / "math_ops.py")
    responses = [tool_check(str(project / "src")), tool_check_file(path), tool_check_function(path, "square"), tool_check(str(project))]
    assert all(r["passed"] for r in responses), responses
    for scope in (str(project), path):
        result = CliRunner().invoke(main, ["check", scope, "--structural"])
        assert result.exit_code == 0, result.output


def test_test_sources_still_receive_assertion_checks(project: Path) -> None:
    tests = project / "tests"
    tests.mkdir()
    path = tests / "test_math_ops.py"
    path.write_text('def test_square():\n    value = 4\n')
    response = tool_check_file(str(path))
    messages = [f["message"] for f in response["findings"]]
    assert any("assert" in message.lower() for message in messages)
    assert not any("test_test_" in message or "icontract.ensure" in message for message in messages)
