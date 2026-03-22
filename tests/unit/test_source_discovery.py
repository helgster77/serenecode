"""Tests for shared source discovery helpers."""

from __future__ import annotations

from pathlib import Path

import icontract
import pytest

from serenecode.adapters.local_fs import LocalFileReader
from serenecode.core.exceptions import ConfigurationError
from serenecode.source_discovery import build_source_files, find_serenecode_md
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
