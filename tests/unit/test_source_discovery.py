"""Tests for shared source discovery helpers."""

from __future__ import annotations

from pathlib import Path

import icontract
import pytest

from serenecode.adapters.local_fs import LocalFileReader
from serenecode.core.exceptions import ConfigurationError
from serenecode.source_discovery import build_source_files, discover_test_file_stems, find_serenecode_md
from tests.conftest import icontract_enabled


class TestBuildSourceFiles:
    """Tests for building SourceFile objects from discovered paths."""

    def test_absolute_standalone_file_keeps_file_reference_for_importing(self, tmp_path: Path) -> None:
        test_file = tmp_path / "sample.py"
        test_file.write_text('"""Module docstring."""\n', encoding="utf-8")

        source_files = build_source_files(
            [str(test_file)],
            LocalFileReader(),
            str(test_file),
        )

        assert len(source_files) == 1
        assert source_files[0].module_path == "sample.py"
        assert source_files[0].importable_module == "sample"
        assert source_files[0].import_search_paths == (str(tmp_path),)

    def test_src_layout_uses_package_module_name_and_src_import_root(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src" / "pkg"
        src_dir.mkdir(parents=True)
        (src_dir / "__init__.py").write_text("", encoding="utf-8")
        module_file = src_dir / "mod.py"
        module_file.write_text('"""Module docstring."""\n', encoding="utf-8")

        source_files = build_source_files(
            [str(module_file)],
            LocalFileReader(),
            str(tmp_path),
        )

        assert len(source_files) == 1
        assert source_files[0].module_path == "pkg/mod.py"
        assert source_files[0].importable_module == "pkg.mod"
        assert source_files[0].import_search_paths == (str(tmp_path / "src"),)

    def test_single_file_in_src_package_keeps_package_context(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src" / "pkg"
        src_dir.mkdir(parents=True)
        (src_dir / "__init__.py").write_text("", encoding="utf-8")
        module_file = src_dir / "mod.py"
        module_file.write_text('"""Module docstring."""\n', encoding="utf-8")

        source_files = build_source_files(
            [str(module_file)],
            LocalFileReader(),
            str(module_file),
        )

        assert len(source_files) == 1
        assert source_files[0].module_path == "pkg/mod.py"
        assert source_files[0].importable_module == "pkg.mod"
        assert source_files[0].import_search_paths == (str(tmp_path / "src"),)

    def test_scoped_core_directory_keeps_core_prefix(self, tmp_path: Path) -> None:
        core_dir = tmp_path / "src" / "core"
        core_dir.mkdir(parents=True)
        module_file = core_dir / "engine.py"
        module_file.write_text('"""Core module."""\n', encoding="utf-8")

        source_files = build_source_files(
            [str(module_file)],
            LocalFileReader(),
            str(core_dir),
        )

        assert len(source_files) == 1
        assert source_files[0].module_path == "core/engine.py"
        assert source_files[0].importable_module == "core.engine"
        assert source_files[0].import_search_paths == (str(tmp_path / "src"),)

    def test_rejects_empty_discovered_paths(self) -> None:
        if icontract_enabled():
            with pytest.raises(icontract.ViolationError):
                build_source_files([""], LocalFileReader(), ".")
        else:
            with pytest.raises(ConfigurationError):
                build_source_files([""], LocalFileReader(), ".")


class TestFindSerenecodeMd:
    """Tests for SERENECODE.md discovery from file and directory paths."""

    def test_finds_config_from_python_file_path(self, tmp_path: Path) -> None:
        config_file = tmp_path / "SERENECODE.md"
        config_file.write_text("# config\n", encoding="utf-8")
        source_file = tmp_path / "app.py"
        source_file.write_text("print('hi')\n", encoding="utf-8")

        found = find_serenecode_md(str(source_file), LocalFileReader())

        assert found == str(config_file)

    def test_finds_config_more_than_ten_levels_up(self, tmp_path: Path) -> None:
        config_file = tmp_path / "SERENECODE.md"
        config_file.write_text("# config\n", encoding="utf-8")

        nested_dir = tmp_path
        for index in range(11):
            nested_dir = nested_dir / f"level_{index}"
            nested_dir.mkdir()

        source_file = nested_dir / "app.py"
        source_file.write_text("print('hi')\n", encoding="utf-8")

        found = find_serenecode_md(str(source_file), LocalFileReader())

        assert found == str(config_file)

    def test_build_source_files_preserves_deep_project_root_context(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\nversion='0.0.0'\n", encoding="utf-8")

        nested_dir = tmp_path
        for index in range(11):
            nested_dir = nested_dir / f"level_{index}"
            nested_dir.mkdir()

        source_file = nested_dir / "app.py"
        source_file.write_text('"""Module docstring."""\n', encoding="utf-8")

        source_files = build_source_files(
            [str(source_file)],
            LocalFileReader(),
            str(source_file),
        )

        assert len(source_files) == 1
        assert source_files[0].module_path.endswith("app.py")
        assert source_files[0].importable_module is not None
        assert source_files[0].import_search_paths == (str(tmp_path),)


class TestDiscoverTestFileStems:
    """Tests for test file stem discovery."""

    def test_discovers_test_files_in_tests_dir(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\nversion='0.0.0'\n", encoding="utf-8")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_engine.py").write_text("", encoding="utf-8")
        (tests_dir / "test_models.py").write_text("", encoding="utf-8")
        (tests_dir / "conftest.py").write_text("", encoding="utf-8")

        stems = discover_test_file_stems(str(tmp_path), LocalFileReader())

        assert stems == frozenset({"test_engine", "test_models"})

    def test_discovers_nested_test_files(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\nversion='0.0.0'\n", encoding="utf-8")
        unit_dir = tmp_path / "tests" / "unit"
        unit_dir.mkdir(parents=True)
        (unit_dir / "test_core.py").write_text("", encoding="utf-8")
        integration_dir = tmp_path / "tests" / "integration"
        integration_dir.mkdir()
        (integration_dir / "test_adapter.py").write_text("", encoding="utf-8")

        stems = discover_test_file_stems(str(tmp_path), LocalFileReader())

        assert "test_core" in stems
        assert "test_adapter" in stems

    def test_returns_empty_when_no_tests_dir(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\nversion='0.0.0'\n", encoding="utf-8")

        stems = discover_test_file_stems(str(tmp_path), LocalFileReader())

        assert stems == frozenset()

    def test_ignores_non_test_files(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\nversion='0.0.0'\n", encoding="utf-8")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "conftest.py").write_text("", encoding="utf-8")
        (tests_dir / "helpers.py").write_text("", encoding="utf-8")
        (tests_dir / "__init__.py").write_text("", encoding="utf-8")

        stems = discover_test_file_stems(str(tmp_path), LocalFileReader())

        assert stems == frozenset()

    def test_works_from_src_subpath(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\nversion='0.0.0'\n", encoding="utf-8")
        src_dir = tmp_path / "src" / "pkg"
        src_dir.mkdir(parents=True)
        (src_dir / "__init__.py").write_text("", encoding="utf-8")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_pkg.py").write_text("", encoding="utf-8")

        # Passing src/ subpath should still find tests/ at project root
        stems = discover_test_file_stems(str(tmp_path / "src"), LocalFileReader())

        assert stems == frozenset({"test_pkg"})
