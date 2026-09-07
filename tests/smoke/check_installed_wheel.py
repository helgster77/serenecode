"""Run with a clean wheel installation to verify the documented end-user setup."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


def main() -> None:
    """Verify installed runtime dependencies and run a real passing Level 3 check."""
    for module in ("pytest", "pytest_cov", "mcp"):
        assert importlib.util.find_spec(module) is not None, f"Missing dependency: {module}"
    with TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "pyproject.toml").write_text('[project]\nname="wheel-smoke"\nversion="0.0.0"\n')
        (root / "src").mkdir()
        (root / "tests").mkdir()
        (root / "src" / "arithmetic.py").write_text('''"""Arithmetic."""
import icontract
@icontract.require(lambda x: x >= 0, "non-negative input")
@icontract.ensure(lambda x, result: result == x * x, "result is squared")
def square(x: int) -> int:
    """Square a value."""
    return x * x
''')
        (root / "tests" / "test_arithmetic.py").write_text(
            "from arithmetic import square\n\ndef test_square():\n    assert square(3) == 9\n"
        )
        command = [str(Path(sys.executable).with_name("serenecode")), "check", "src",
                   "--level", "3", "--allow-code-execution", "--format", "json"]
        result = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=90)
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout)["passed"] is True
    print("Clean wheel installation passed a real Level 3 verification.")


if __name__ == "__main__":
    main()
