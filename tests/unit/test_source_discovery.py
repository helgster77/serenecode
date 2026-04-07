"""Tests for shared source discovery helpers."""

from __future__ import annotations

import os
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


class TestSourceDiscoveryHelpers:
    """Direct tests for the small helpers in source_discovery.py.

    Covers the branches L3 reports as below threshold for `_walk_package_root`,
    `_derive_module_reference`, `_module_reference_from_relative`,
    `_infer_import_root`, and `_relative_to_root`.
    """

    def test_walk_package_root_no_init(self, tmp_path: Path) -> None:
        from serenecode.source_discovery import _walk_package_root
        # tmp_path has no __init__.py — function returns the path itself
        result = _walk_package_root(str(tmp_path))
        assert result == str(tmp_path)

    def test_walk_package_root_single_package(self, tmp_path: Path) -> None:
        """Branch (lines 247-251): walk up one level past __init__.py."""
        from serenecode.source_discovery import _walk_package_root
        pkg = tmp_path / "mypkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        result = _walk_package_root(str(pkg))
        assert result == str(tmp_path)

    def test_walk_package_root_nested_packages(self, tmp_path: Path) -> None:
        """Walks up through multiple package levels."""
        from serenecode.source_discovery import _walk_package_root
        outer = tmp_path / "outer"
        inner = outer / "inner"
        inner.mkdir(parents=True)
        (outer / "__init__.py").write_text("", encoding="utf-8")
        (inner / "__init__.py").write_text("", encoding="utf-8")
        result = _walk_package_root(str(inner))
        assert result == str(tmp_path)

    def test_walk_package_root_filesystem_root(self, tmp_path: Path) -> None:
        """Branch (line 253): walk reaches filesystem root via parent==current."""
        from unittest.mock import patch
        from serenecode.source_discovery import _walk_package_root

        original_isfile = os.path.isfile

        def fake_isfile(path: str) -> bool:
            if path.endswith("__init__.py"):
                return True
            return original_isfile(path)

        with patch("serenecode.source_discovery.os.path.isfile", new=fake_isfile):
            result = _walk_package_root(str(tmp_path))
            # The walk eventually terminates at the filesystem root
            assert os.path.dirname(result) == result

    def test_derive_module_reference_non_py_returns_none(self, tmp_path: Path) -> None:
        from serenecode.source_discovery import _derive_module_reference
        result = _derive_module_reference("foo.txt", str(tmp_path))
        assert result is None

    def test_derive_module_reference_src_layout(self, tmp_path: Path) -> None:
        from serenecode.source_discovery import _derive_module_reference
        pkg_root = tmp_path / "src" / "mypkg"
        pkg_root.mkdir(parents=True)
        (pkg_root / "__init__.py").write_text("", encoding="utf-8")
        module_file = pkg_root / "mod.py"
        module_file.write_text("", encoding="utf-8")
        result = _derive_module_reference(str(module_file), str(tmp_path))
        assert result == "mypkg.mod"

    def test_derive_module_reference_falls_back_to_absolute_path(
        self, tmp_path: Path,
    ) -> None:
        """Branch (line 295): non-package layout returns absolute file path."""
        from serenecode.source_discovery import _derive_module_reference
        odd_dir = tmp_path / "not-a-valid-package"
        odd_dir.mkdir()
        module_file = odd_dir / "mod.py"
        module_file.write_text("", encoding="utf-8")
        result = _derive_module_reference(str(module_file), str(tmp_path))
        # Falls back to absolute file path
        assert result is not None
        assert result.endswith("mod.py")

    def test_module_reference_from_relative_basic(self) -> None:
        from serenecode.source_discovery import _module_reference_from_relative
        assert _module_reference_from_relative("pkg/mod.py") == "pkg.mod"

    def test_module_reference_from_relative_init(self) -> None:
        from serenecode.source_discovery import _module_reference_from_relative
        assert _module_reference_from_relative("pkg/__init__.py") == "pkg"

    def test_module_reference_from_relative_non_py(self) -> None:
        """Branch (line 336): doesn't end with .py → None."""
        from serenecode.source_discovery import _module_reference_from_relative
        assert _module_reference_from_relative("pkg/mod.txt") is None

    def test_module_reference_from_relative_empty_after_strip(self) -> None:
        """Branch (line 343): module_part is empty after stripping → None."""
        from serenecode.source_discovery import _module_reference_from_relative
        # "/__init__.py" → strip ".py" → "/__init__" → strip "/__init__" → ""
        assert _module_reference_from_relative("/__init__.py") is None

    def test_module_reference_from_relative_invalid_identifier(self) -> None:
        """Branch (lines 347, 352): segment is not a valid identifier."""
        from serenecode.source_discovery import _module_reference_from_relative
        assert _module_reference_from_relative("pkg/has-dash/mod.py") is None

    def test_module_reference_from_relative_keyword_segment(self) -> None:
        from serenecode.source_discovery import _module_reference_from_relative
        # Python keyword like "class" is invalid as a module segment
        assert _module_reference_from_relative("class/mod.py") is None

    def test_infer_import_root_no_packages(self, tmp_path: Path) -> None:
        from serenecode.source_discovery import _infer_import_root
        f = tmp_path / "standalone.py"
        f.write_text("", encoding="utf-8")
        result = _infer_import_root(str(f))
        assert result == str(tmp_path)

    def test_infer_import_root_with_package(self, tmp_path: Path) -> None:
        """Branch (line 367): walk up past one package level."""
        from serenecode.source_discovery import _infer_import_root
        pkg = tmp_path / "mypkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        f = pkg / "mod.py"
        f.write_text("", encoding="utf-8")
        result = _infer_import_root(str(f))
        assert result == str(tmp_path)

    def test_infer_import_root_filesystem_root(self, tmp_path: Path) -> None:
        """Branch (line 367 break): walk hits parent==current at fs root."""
        from unittest.mock import patch
        from serenecode.source_discovery import _infer_import_root

        original_isfile = os.path.isfile

        def fake_isfile(path: str) -> bool:
            if path.endswith("__init__.py"):
                return True
            return original_isfile(path)

        with patch("serenecode.source_discovery.os.path.isfile", new=fake_isfile):
            f = tmp_path / "deep.py"
            result = _infer_import_root(str(f))
            assert os.path.dirname(result) == result

    def test_relative_to_root_inside_root(self, tmp_path: Path) -> None:
        from serenecode.source_discovery import _relative_to_root
        f = tmp_path / "pkg" / "mod.py"
        f.parent.mkdir()
        f.write_text("", encoding="utf-8")
        result = _relative_to_root(str(f), str(tmp_path))
        assert result.endswith("mod.py")
        assert "pkg" in result

    def test_relative_to_root_outside_root(self, tmp_path: Path) -> None:
        """Branch (line 388): file's commonpath with root is not the root → fallback to relpath from cwd."""
        from serenecode.source_discovery import _relative_to_root
        unrelated_root = tmp_path / "unrelated"
        unrelated_root.mkdir()
        f = unrelated_root / "mod.py"
        f.write_text("", encoding="utf-8")
        other_root = tmp_path / "different"
        other_root.mkdir()
        result = _relative_to_root(str(f), str(other_root))
        assert isinstance(result, str)
        assert len(result) > 0

    def test_relative_to_root_commonpath_value_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Branch (lines 382-383): os.path.commonpath raises ValueError → fallback."""
        from serenecode.source_discovery import _relative_to_root
        f = tmp_path / "mod.py"
        f.write_text("", encoding="utf-8")

        # Mock commonpath to raise ValueError (which it does on mixed drives on Windows)
        def raising_commonpath(paths: list[str]) -> str:
            raise ValueError("Paths don't have the same drive")

        monkeypatch.setattr("serenecode.source_discovery.os.path.commonpath", raising_commonpath)
        result = _relative_to_root(str(f), str(tmp_path))
        # Should fall back to relpath from cwd, not crash
        assert isinstance(result, str)
