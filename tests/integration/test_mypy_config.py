"""Tests for finding the mypy settings that make the type checker run code."""

from __future__ import annotations

from pathlib import Path

import pytest

from serenecode.adapters.mypy_adapter import MypyTypeChecker
from serenecode.adapters.mypy_config import find_executing_mypy_settings
from serenecode.checker.types import transform_type_results
from serenecode.models import CheckStatus


def _repository(tmp_path: Path) -> Path:
    """Create a directory that mypy's upward search treats as a repository root."""
    (tmp_path / ".git").mkdir()
    return tmp_path


class TestFindExecutingMypySettings:
    """The applicable project configuration is found the way mypy finds it."""

    def test_no_configuration(self, tmp_path: Path) -> None:
        """Verifies: REQ-066"""
        assert find_executing_mypy_settings(str(_repository(tmp_path))) is None

    def test_configuration_without_executing_settings(self, tmp_path: Path) -> None:
        """Verifies: REQ-066"""
        root = _repository(tmp_path)
        (root / "mypy.ini").write_text("[mypy]\nstrict = True\n", encoding="utf-8")
        assert find_executing_mypy_settings(str(root)) is None

    def test_plugins_in_mypy_ini(self, tmp_path: Path) -> None:
        """Verifies: REQ-066"""
        root = _repository(tmp_path)
        (root / "mypy.ini").write_text("[mypy]\nplugins = a.plugin,\n    b.plugin\n", encoding="utf-8")

        found = find_executing_mypy_settings(str(root))

        assert found is not None
        assert found.config_file == str(root / "mypy.ini")
        assert found.settings == ("plugins = a.plugin, b.plugin",)

    def test_plugins_in_pyproject(self, tmp_path: Path) -> None:
        """Verifies: REQ-066"""
        root = _repository(tmp_path)
        (root / "pyproject.toml").write_text(
            '[tool.mypy]\nplugins = ["a.plugin", "b.plugin"]\n', encoding="utf-8",
        )

        found = find_executing_mypy_settings(str(root))

        assert found is not None
        assert found.settings == ("plugins = a.plugin, b.plugin",)

    def test_python_executable_in_setup_cfg(self, tmp_path: Path) -> None:
        """Verifies: REQ-066"""
        root = _repository(tmp_path)
        (root / "setup.cfg").write_text("[mypy]\npython_executable = ./run.sh\n", encoding="utf-8")

        found = find_executing_mypy_settings(str(root))

        assert found is not None
        assert found.settings == ("python_executable = ./run.sh",)

    def test_shared_files_without_a_mypy_section_are_skipped(self, tmp_path: Path) -> None:
        """A pyproject.toml with no [tool.mypy] is not the mypy configuration.

        Verifies: REQ-066
        """
        root = _repository(tmp_path)
        (root / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
        (root / "setup.cfg").write_text("[mypy]\nplugins = late.plugin\n", encoding="utf-8")

        found = find_executing_mypy_settings(str(root))

        assert found is not None
        assert found.config_file == str(root / "setup.cfg")

    def test_first_configuration_wins(self, tmp_path: Path) -> None:
        """mypy reads only the first configuration file it finds.

        Verifies: REQ-066
        """
        root = _repository(tmp_path)
        (root / "mypy.ini").write_text("[mypy]\nstrict = True\n", encoding="utf-8")
        (root / "setup.cfg").write_text("[mypy]\nplugins = ignored.plugin\n", encoding="utf-8")

        assert find_executing_mypy_settings(str(root)) is None

    def test_ancestor_configuration_applies(self, tmp_path: Path) -> None:
        """Verifies: REQ-066"""
        root = _repository(tmp_path)
        (root / "mypy.ini").write_text("[mypy]\nplugins = a.plugin\n", encoding="utf-8")
        nested = root / "pkg" / "sub"
        nested.mkdir(parents=True)

        found = find_executing_mypy_settings(str(nested))

        assert found is not None
        assert found.config_file == str(root / "mypy.ini")

    def test_search_stops_at_the_repository_root(self, tmp_path: Path) -> None:
        """Verifies: REQ-066"""
        (tmp_path / "mypy.ini").write_text("[mypy]\nplugins = outside.plugin\n", encoding="utf-8")
        (tmp_path / "repo").mkdir()
        root = _repository(tmp_path / "repo")

        assert find_executing_mypy_settings(str(root)) is None

    def test_unparsable_configuration_is_not_a_configuration(self, tmp_path: Path) -> None:
        root = _repository(tmp_path)
        (root / "pyproject.toml").write_text("[tool.mypy\nplugins = [", encoding="utf-8")
        assert find_executing_mypy_settings(str(root)) is None


class TestMypyRefusesProjectCode:
    """Level 2 does not start mypy when the project would make it run code."""

    def test_plugins_without_consent_are_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The plugin never runs, and the refusal is a FAILED Level 2 record.

        Verifies: REQ-066, REQ-063
        """
        root = _repository(tmp_path)
        marker = root / "PLUGIN_RAN"
        (root / "plug.py").write_text(
            "import pathlib\n"
            f"pathlib.Path({str(marker)!r}).write_text('ran')\n"
            "from mypy.plugin import Plugin\n"
            "def plugin(version):\n"
            "    return Plugin\n",
            encoding="utf-8",
        )
        (root / "mypy.ini").write_text("[mypy]\nplugins = plug.py\n", encoding="utf-8")
        (root / "mod.py").write_text("X: int = 1\n", encoding="utf-8")
        monkeypatch.chdir(root)

        issues = MypyTypeChecker(timeout=60).check([str(root / "mod.py")])

        assert not marker.exists()
        assert [(issue.file, issue.line) for issue in issues] == [("<mypy>", 0)]
        assert "plugins = plug.py" in issues[0].message
        assert "--allow-code-execution" in issues[0].message

        result = transform_type_results(issues, 0.0)
        assert [(r.function, r.line, r.status) for r in result.results] == [
            ("<mypy>", 1, CheckStatus.FAILED),
        ]

    @pytest.mark.slow
    def test_plugins_with_consent_run(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verifies: REQ-066"""
        root = _repository(tmp_path)
        marker = root / "PLUGIN_RAN"
        (root / "plug.py").write_text(
            "import pathlib\n"
            f"pathlib.Path({str(marker)!r}).write_text('ran')\n"
            "from mypy.plugin import Plugin\n"
            "def plugin(version):\n"
            "    return Plugin\n",
            encoding="utf-8",
        )
        (root / "mypy.ini").write_text("[mypy]\nplugins = plug.py\n", encoding="utf-8")
        (root / "mod.py").write_text("X: int = 1\n", encoding="utf-8")
        monkeypatch.chdir(root)

        issues = MypyTypeChecker(timeout=60, allow_plugins=True).check([str(root / "mod.py")])

        assert marker.exists()
        assert [issue for issue in issues if issue.severity == "error"] == []
