"""Integration tests for the module loader adapter.

Tests the module loading, caching, cleanup, and finder/loader internals
that are not covered by higher-level integration tests.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest

from serenecode.adapters.module_loader import (
    _FreshSourceFinder,
    _FreshSourceLoader,
    _as_existing_python_file,
    _infer_import_root,
    _temporary_module_refresh,
    load_python_module,
)


class TestLoadPythonModule:
    """Tests for the top-level load_python_module function."""

    def test_load_from_absolute_file_path(self, tmp_path: Path) -> None:
        """Loading via an absolute .py path exercises _load_module_from_file."""
        module_file = tmp_path / "standalone.py"
        module_file.write_text("ANSWER = 42\n", encoding="utf-8")

        loaded = load_python_module(str(module_file))

        assert loaded.ANSWER == 42

    def test_load_nonexistent_module_raises(self) -> None:
        with pytest.raises(ImportError, match="Cannot load"):
            load_python_module("absolutely_no_such_module_exists_xyz")

    def test_load_module_that_raises_during_exec(self, tmp_path: Path) -> None:
        """If the loaded module raises, the error propagates."""
        module_file = tmp_path / "broken.py"
        module_file.write_text("raise RuntimeError('boom')\n", encoding="utf-8")

        with pytest.raises(RuntimeError, match="boom"):
            load_python_module(str(module_file))

    def test_sys_modules_not_polluted_after_exec_failure(self, tmp_path: Path) -> None:
        """A failed load must not leave the broken module in sys.modules."""
        module_file = tmp_path / "broken_mod.py"
        module_file.write_text("raise RuntimeError('fail')\n", encoding="utf-8")

        with pytest.raises(RuntimeError):
            load_python_module(str(module_file))

        # The broken module should not be importable after the failure
        matching = [k for k in sys.modules if "broken_mod" in k]
        assert not matching, f"broken module left in sys.modules: {matching}"


class TestAsExistingPythonFile:
    """Tests for _as_existing_python_file."""

    def test_returns_none_for_relative_path(self) -> None:
        assert _as_existing_python_file("relative/module.py") is None

    def test_returns_none_for_nonexistent_absolute_path(self, tmp_path: Path) -> None:
        assert _as_existing_python_file(str(tmp_path / "nope.py")) is None

    def test_returns_none_for_non_py_file(self, tmp_path: Path) -> None:
        txt_file = tmp_path / "data.txt"
        txt_file.write_text("hello", encoding="utf-8")
        assert _as_existing_python_file(str(txt_file)) is None

    def test_returns_path_for_existing_py_file(self, tmp_path: Path) -> None:
        py_file = tmp_path / "real.py"
        py_file.write_text("X = 1\n", encoding="utf-8")
        result = _as_existing_python_file(str(py_file))
        assert result is not None
        assert result == py_file


class TestInferImportRoot:
    """Tests for _infer_import_root — walks above __init__.py chains."""

    def test_single_package(self, tmp_path: Path) -> None:
        pkg = tmp_path / "mypkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        module_file = pkg / "mod.py"
        module_file.write_text("X = 1\n", encoding="utf-8")

        root = _infer_import_root(module_file)

        # Should walk above mypkg/ to tmp_path
        assert root == str(tmp_path)

    def test_nested_packages(self, tmp_path: Path) -> None:
        outer = tmp_path / "outer"
        inner = outer / "inner"
        inner.mkdir(parents=True)
        (outer / "__init__.py").write_text("", encoding="utf-8")
        (inner / "__init__.py").write_text("", encoding="utf-8")
        module_file = inner / "deep.py"
        module_file.write_text("X = 1\n", encoding="utf-8")

        root = _infer_import_root(module_file)

        # Should walk above outer/ to tmp_path
        assert root == str(tmp_path)

    def test_no_init_files(self, tmp_path: Path) -> None:
        module_file = tmp_path / "standalone.py"
        module_file.write_text("X = 1\n", encoding="utf-8")

        root = _infer_import_root(module_file)

        # No __init__.py to walk above, stays at parent dir
        assert root == str(tmp_path)

    def test_walk_terminates_at_filesystem_root(self, tmp_path: Path) -> None:
        """Branch (line 444): if parent == current → break out of walk.

        Triggered when the package walk reaches the filesystem root (where
        Path('/').parent == Path('/')). We force it by mocking is_file()
        to always return True for __init__.py probes.
        """
        from unittest.mock import patch

        original_is_file = Path.is_file

        def fake_is_file(self: Path) -> bool:
            if self.name == "__init__.py":
                return True
            return original_is_file(self)

        with patch.object(Path, "is_file", new=fake_is_file):
            module_file = tmp_path / "deep.py"
            result = _infer_import_root(module_file)
            # The walk should terminate at the filesystem root
            assert Path(result).parent == Path(result)


class TestFreshSourceFinder:
    """Tests for _FreshSourceFinder.find_spec."""

    def test_returns_none_for_unrelated_module(self, tmp_path: Path) -> None:
        finder = _FreshSourceFinder(
            search_paths=(str(tmp_path),),
            module_roots=("mypkg",),
        )

        result = finder.find_spec("otherpkg.something")

        assert result is None

    def test_finds_package_init(self, tmp_path: Path) -> None:
        pkg = tmp_path / "mypkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("PKG = True\n", encoding="utf-8")

        finder = _FreshSourceFinder(
            search_paths=(str(tmp_path),),
            module_roots=("mypkg",),
        )

        spec = finder.find_spec("mypkg")

        assert spec is not None
        assert spec.submodule_search_locations is not None

    def test_finds_module_file(self, tmp_path: Path) -> None:
        pkg = tmp_path / "mypkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "sub.py").write_text("VAL = 1\n", encoding="utf-8")

        finder = _FreshSourceFinder(
            search_paths=(str(tmp_path),),
            module_roots=("mypkg",),
        )

        spec = finder.find_spec("mypkg.sub")

        assert spec is not None
        assert "sub.py" in str(spec.origin)

    def test_returns_none_when_file_missing(self, tmp_path: Path) -> None:
        finder = _FreshSourceFinder(
            search_paths=(str(tmp_path),),
            module_roots=("mypkg",),
        )

        result = finder.find_spec("mypkg.nonexistent")

        assert result is None


class TestFreshSourceLoader:
    """Tests for _FreshSourceLoader."""

    def test_exec_module_runs_source(self, tmp_path: Path) -> None:
        module_file = tmp_path / "example.py"
        module_file.write_text("LOADED = True\n", encoding="utf-8")

        loader = _FreshSourceLoader("example", module_file)
        module = ModuleType("example")
        loader.exec_module(module)

        assert module.LOADED is True

    def test_exec_module_sees_updated_source(self, tmp_path: Path) -> None:
        module_file = tmp_path / "example.py"
        module_file.write_text("VERSION = 1\n", encoding="utf-8")

        loader = _FreshSourceLoader("example", module_file)
        mod1 = ModuleType("example")
        loader.exec_module(mod1)
        assert mod1.VERSION == 1

        module_file.write_text("VERSION = 2\n", encoding="utf-8")
        mod2 = ModuleType("example")
        loader.exec_module(mod2)
        assert mod2.VERSION == 2


class TestTemporaryModuleRefresh:
    """Tests for _temporary_module_refresh context manager."""

    def test_restores_modules_after_context(self, tmp_path: Path) -> None:
        """Modules evicted during refresh must be restored after the block."""
        # Use a synthetic dotted-name module so the key is predictable
        mod_file = tmp_path / "refresh_test_mod.py"
        mod_file.write_text("V = 1\n", encoding="utf-8")
        original = load_python_module("refresh_test_mod", (str(tmp_path),))
        mod_key = "refresh_test_mod"
        sys.modules[mod_key] = original

        with _temporary_module_refresh((mod_key,)):
            assert mod_key not in sys.modules

        assert sys.modules[mod_key] is original

        # Cleanup
        sys.modules.pop(mod_key, None)

    def test_empty_prefixes_is_noop(self) -> None:
        snapshot = dict(sys.modules)

        with _temporary_module_refresh(()):
            pass

        # sys.modules should be unchanged
        assert "serenecode.models" in sys.modules
