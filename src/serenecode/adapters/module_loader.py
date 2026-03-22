"""Helpers for loading Python modules from module names or file paths."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import importlib
import importlib.util
import sys
import threading
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType
from typing import Iterator

import icontract

from serenecode.contracts.predicates import is_non_empty_string, is_valid_file_path_string

_MODULE_LOAD_LOCK = threading.RLock()
_UNSUPPORTED_RELATIVE_MODULE_PREFIXES = (".",)


@icontract.require(lambda module_ref: is_non_empty_string(module_ref), "module_ref must be a non-empty string")
@icontract.require(
    lambda module_ref: is_valid_file_path_string(module_ref),
    "module_ref must be a syntactically valid module reference",
)
@icontract.require(
    lambda module_ref: not module_ref.startswith(_UNSUPPORTED_RELATIVE_MODULE_PREFIXES),
    "relative module names are not supported",
)
@icontract.ensure(lambda result: isinstance(result, ModuleType), "result must be a module")
def load_python_module(
    module_ref: str,
    search_paths: tuple[str, ...] = (),
) -> ModuleType:
    """Load a Python module from a dotted name or an absolute file path.

    Always refreshes the target module so repeated verification runs see
    the latest source instead of a cached import from an earlier check.
    """
    with _MODULE_LOAD_LOCK:
        module_file = _as_existing_python_file(module_ref)
        if module_file is None:
            importlib.invalidate_caches()
            with _temporary_sys_path(search_paths):
                spec = importlib.util.find_spec(module_ref)
            if spec is None or spec.origin is None:
                raise ImportError(f"Cannot load module '{module_ref}'")
            return _load_module_from_spec(module_ref, spec, search_paths)
        return _load_module_from_file(module_file, search_paths)


@icontract.require(lambda module_ref: is_non_empty_string(module_ref), "module_ref must be a non-empty string")
@icontract.ensure(lambda result: result is None or isinstance(result, Path), "result must be a path or None")
def _as_existing_python_file(module_ref: str) -> Path | None:
    """Interpret a module reference as an absolute Python file when possible."""
    path = Path(module_ref)
    if not path.is_absolute():
        return None
    if not path.is_file() or path.suffix != ".py":
        return None
    return path


@icontract.require(lambda module_file: isinstance(module_file, Path), "module_file must be a Path")
@icontract.ensure(lambda result: isinstance(result, ModuleType), "result must be a module")
def _load_module_from_file(
    module_file: Path,
    search_paths: tuple[str, ...] = (),
) -> ModuleType:
    """Load a Python module directly from a source file."""
    resolved = module_file.resolve()
    module_name = (
        "serenecode_dynamic_"
        + hashlib.sha256(str(resolved).encode("utf-8")).hexdigest()[:16]
    )

    module_dir = str(resolved.parent)
    effective_search_paths = _dedupe_search_paths(
        (*search_paths, _infer_import_root(resolved), module_dir)
    )

    spec = importlib.util.spec_from_file_location(module_name, resolved)
    if spec is None:
        raise ImportError(f"Cannot load module from '{resolved}'")

    return _load_module_from_spec(module_name, spec, effective_search_paths)


@icontract.require(lambda module_name: is_non_empty_string(module_name), "module_name must be a non-empty string")
@icontract.require(lambda spec: isinstance(spec, ModuleSpec), "spec must be a ModuleSpec")
@icontract.ensure(lambda result: isinstance(result, ModuleType), "result must be a module")
def _load_module_from_spec(
    module_name: str,
    spec: ModuleSpec,
    search_paths: tuple[str, ...] = (),
) -> ModuleType:
    """Load a module from an import spec using fresh source execution."""
    if spec.origin is None:
        raise ImportError(f"Cannot load module '{module_name}' without origin")

    importlib.invalidate_caches()
    previous_module = sys.modules.get(module_name)

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module

    try:
        with _temporary_sys_path(_dedupe_search_paths(search_paths)):
            source = Path(spec.origin).read_text(encoding="utf-8")
            code = compile(source, spec.origin, "exec")
            exec(code, module.__dict__)
    except Exception:
        if previous_module is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous_module
        raise
    else:
        if previous_module is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous_module

    return module


@icontract.require(lambda module_file: isinstance(module_file, Path), "module_file must be a Path")
@icontract.ensure(lambda result: is_non_empty_string(result), "result must be a non-empty string")
def _infer_import_root(module_file: Path) -> str:
    """Infer the sys.path entry needed for importing sibling packages."""
    current = module_file.parent.resolve()

    # Loop invariant: current is the directory above the deepest package chain seen so far
    while (current / "__init__.py").is_file():
        parent = current.parent
        if parent == current:
            break
        current = parent

    return str(current)


@icontract.require(lambda search_paths: isinstance(search_paths, tuple), "search_paths must be a tuple")
@icontract.ensure(lambda result: isinstance(result, tuple), "result must be a tuple")
def _dedupe_search_paths(search_paths: tuple[str, ...]) -> tuple[str, ...]:
    """Normalize and deduplicate search paths while preserving order."""
    unique: list[str] = []

    # Loop invariant: unique contains normalized, unique paths from search_paths[0..i]
    for path in search_paths:
        if not path:
            continue
        normalized = str(Path(path).resolve())
        if normalized not in unique:
            unique.append(normalized)

    return tuple(unique)


@icontract.require(lambda search_paths: isinstance(search_paths, tuple), "search_paths must be a tuple")
@icontract.ensure(
    lambda result: hasattr(result, "__enter__") and hasattr(result, "__exit__"),
    "result must be a context manager",
)
@contextmanager
def _temporary_sys_path(search_paths: tuple[str, ...]) -> Iterator[None]:
    """Temporarily prepend search paths to sys.path during module loading."""
    inserted: list[str] = []

    # Loop invariant: inserted contains search paths added from search_paths[n-i..n]
    # Loop invariant: inserted contains search paths added from search_paths[0..i]
    for path in reversed(search_paths):
        if path in sys.path:
            continue
        sys.path.insert(0, path)
        inserted.append(path)

    try:
        yield
    finally:
        # Loop invariant: sys.path entries added in inserted[0..i] are removed at most once
        for path in inserted:
            try:
                sys.path.remove(path)
            except ValueError:
                pass
