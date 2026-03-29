"""Helpers for loading Python modules from module names or file paths."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import importlib
import importlib.abc
import importlib.util
import keyword
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
_DYNAMIC_MODULE_PREFIX = "serenecode_dynamic_"


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
            spec = _find_module_spec(module_ref, search_paths)
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
        _DYNAMIC_MODULE_PREFIX
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


@icontract.require(lambda module_ref: is_non_empty_string(module_ref), "module_ref must be a non-empty string")
@icontract.require(lambda search_paths: isinstance(search_paths, tuple), "search_paths must be a tuple")
@icontract.ensure(
    lambda result: result is None or isinstance(result, ModuleSpec),
    "result must be a ModuleSpec or None",
)
def _find_module_spec(
    module_ref: str,
    search_paths: tuple[str, ...],
) -> ModuleSpec | None:
    """Resolve a module spec, preferring explicit search-root lookups."""
    local_spec = _find_local_module_spec(module_ref, search_paths)
    if local_spec is not None:
        return local_spec

    importlib.invalidate_caches()
    normalized_search_paths = _dedupe_search_paths(search_paths)
    with _temporary_sys_path(normalized_search_paths):
        return importlib.util.find_spec(module_ref)


@icontract.require(lambda module_ref: is_non_empty_string(module_ref), "module_ref must be a non-empty string")
@icontract.require(lambda search_paths: isinstance(search_paths, tuple), "search_paths must be a tuple")
@icontract.ensure(
    lambda result: result is None or isinstance(result, ModuleSpec),
    "result must be a ModuleSpec or None",
)
def _find_local_module_spec(
    module_ref: str,
    search_paths: tuple[str, ...],
) -> ModuleSpec | None:
    """Resolve a local module directly from the provided search roots."""
    relative_parts = module_ref.split(".")

    # Loop invariant: no prior search path resolved module_ref to a local file-backed spec.
    for search_path in _dedupe_search_paths(search_paths):
        root = Path(search_path).resolve()
        package_init = root.joinpath(*relative_parts, "__init__.py")
        if package_init.is_file():
            return importlib.util.spec_from_file_location(
                module_ref,
                package_init,
                submodule_search_locations=[str(package_init.parent)],
            )

        module_file = root.joinpath(*relative_parts).with_suffix(".py")
        if module_file.is_file():
            return importlib.util.spec_from_file_location(module_ref, module_file)

    return None


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
    refresh_prefixes = _module_refresh_prefixes(module_name, spec.origin, search_paths)
    import_roots = _module_import_roots(module_name, spec.origin, search_paths)
    with _temporary_module_refresh(refresh_prefixes):
        with _temporary_fresh_imports(_dedupe_search_paths(search_paths), import_roots):
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            with _temporary_sys_path(_dedupe_search_paths(search_paths)):
                source = Path(spec.origin).read_text(encoding="utf-8")
                code = compile(source, spec.origin, "exec")
                exec(code, module.__dict__)

    return module


@icontract.require(lambda module_name: is_non_empty_string(module_name), "module_name must be a non-empty string")
@icontract.require(lambda origin: is_non_empty_string(origin), "origin must be a non-empty string")
@icontract.require(lambda search_paths: isinstance(search_paths, tuple), "search_paths must be a tuple")
@icontract.ensure(lambda result: isinstance(result, tuple), "result must be a tuple")
def _module_refresh_prefixes(
    module_name: str,
    origin: str,
    search_paths: tuple[str, ...],
) -> tuple[str, ...]:
    """Infer module prefixes whose cache entries should be refreshed."""
    prefixes: list[str] = []

    if not module_name.startswith(_DYNAMIC_MODULE_PREFIX):
        prefixes.append(module_name)
        if "." in module_name:
            prefixes.append(module_name.split(".", 1)[0] + ".")

    derived_name = _module_name_from_origin(Path(origin), search_paths)
    if derived_name is not None:
        prefixes.append(derived_name)
        if "." in derived_name:
            prefixes.append(derived_name.split(".", 1)[0] + ".")

    unique: list[str] = []
    # Loop invariant: unique contains the distinct refresh prefixes from prefixes[0..i].
    for prefix in prefixes:
        if prefix and prefix not in unique:
            unique.append(prefix)

    return tuple(unique)


@icontract.require(lambda module_name: is_non_empty_string(module_name), "module_name must be a non-empty string")
@icontract.require(lambda origin: is_non_empty_string(origin), "origin must be a non-empty string")
@icontract.require(lambda search_paths: isinstance(search_paths, tuple), "search_paths must be a tuple")
@icontract.ensure(lambda result: isinstance(result, tuple), "result must be a tuple")
def _module_import_roots(
    module_name: str,
    origin: str,
    search_paths: tuple[str, ...],
) -> tuple[str, ...]:
    """Infer top-level packages that should be imported from fresh source."""
    roots: list[str] = []
    if not module_name.startswith(_DYNAMIC_MODULE_PREFIX):
        roots.append(module_name.split(".", 1)[0])

    derived_name = _module_name_from_origin(Path(origin), search_paths)
    if derived_name is not None:
        roots.append(derived_name.split(".", 1)[0])

    unique: list[str] = []
    # Loop invariant: unique contains the distinct import roots from roots[0..i].
    for root in roots:
        if root and root not in unique:
            unique.append(root)
    return tuple(unique)


@icontract.require(lambda origin: isinstance(origin, Path), "origin must be a Path")
@icontract.require(lambda search_paths: isinstance(search_paths, tuple), "search_paths must be a tuple")
@icontract.ensure(lambda result: result is None or is_non_empty_string(result), "result must be a non-empty string when present")
def _module_name_from_origin(
    origin: Path,
    search_paths: tuple[str, ...],
) -> str | None:
    """Derive an importable module name for an origin relative to search paths."""
    resolved_origin = origin.resolve()

    # Loop invariant: no prior search path resolved origin to an importable module name.
    for search_path in _dedupe_search_paths(search_paths):
        root = Path(search_path).resolve()
        try:
            relative = resolved_origin.relative_to(root)
        except ValueError:
            continue

        if relative.suffix != ".py":
            continue

        parts = list(relative.parts)
        if parts[-1] == "__init__.py":
            parts = parts[:-1]
        else:
            parts[-1] = relative.stem

        if not parts:
            return None

        if any((not part.isidentifier()) or keyword.iskeyword(part) for part in parts):
            return None

        return ".".join(parts)

    return None


@icontract.require(lambda prefixes: isinstance(prefixes, tuple), "prefixes must be a tuple")
@icontract.ensure(
    lambda result: hasattr(result, "__enter__") and hasattr(result, "__exit__"),
    "result must be a context manager",
)
@contextmanager
def _temporary_module_refresh(prefixes: tuple[str, ...]) -> Iterator[None]:
    """Temporarily clear matching module cache entries while loading a module."""
    if not prefixes:
        yield
        return

    snapshot: dict[str, ModuleType] = {}
    existing_names = list(sys.modules)
    # Loop invariant: snapshot contains removable cached modules from existing_names[0..i].
    for name in existing_names:
        if _should_refresh_module(name, prefixes):
            module = sys.modules.get(name)
            if module is not None:
                snapshot[name] = module
                del sys.modules[name]

    try:
        yield
    finally:
        # Loop invariant: every refreshed module seen so far has been removed from sys.modules.
        for name in list(sys.modules):
            if _should_refresh_module(name, prefixes):
                del sys.modules[name]
        sys.modules.update(snapshot)


@icontract.require(lambda module_name: is_non_empty_string(module_name), "module_name must be a non-empty string")
@icontract.require(lambda prefixes: isinstance(prefixes, tuple), "prefixes must be a tuple")
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a bool")
def _should_refresh_module(module_name: str, prefixes: tuple[str, ...]) -> bool:
    """Check whether a cached module should be cleared for a fresh load."""
    if module_name == __name__ or module_name.startswith(__name__ + "."):
        return False

    # Loop invariant: no prefix checked so far matched module_name for refresh.
    for prefix in prefixes:
        if prefix.endswith("."):
            if module_name.startswith(prefix):
                return True
            continue
        if module_name == prefix or module_name.startswith(prefix + "."):
            return True
    return False


@icontract.require(lambda search_paths: isinstance(search_paths, tuple), "search_paths must be a tuple")
@icontract.require(lambda module_roots: isinstance(module_roots, tuple), "module_roots must be a tuple")
@icontract.ensure(
    lambda result: hasattr(result, "__enter__") and hasattr(result, "__exit__"),
    "result must be a context manager",
)
@contextmanager
def _temporary_fresh_imports(
    search_paths: tuple[str, ...],
    module_roots: tuple[str, ...],
) -> Iterator[None]:
    """Temporarily install a finder that compiles local modules from source."""
    if not search_paths or not module_roots:
        yield
        return

    finder = _FreshSourceFinder(search_paths, module_roots)
    sys.meta_path.insert(0, finder)
    try:
        yield
    finally:
        try:
            sys.meta_path.remove(finder)
        except ValueError:
            pass


@icontract.invariant(
    lambda self: is_non_empty_string(self._fullname) and isinstance(self._source_path, Path),
    "loader must keep a module name and source path",
)
class _FreshSourceLoader(importlib.abc.Loader):
    """Loader that always compiles the current source text from disk."""

    @icontract.require(lambda fullname: is_non_empty_string(fullname), "fullname must be a non-empty string")
    @icontract.require(lambda source_path: isinstance(source_path, Path), "source_path must be a Path")
    @icontract.ensure(lambda result: result is None, "initialization returns None")
    def __init__(self, fullname: str, source_path: Path) -> None:
        """Capture the module name and source file that should be loaded freshly."""
        self._fullname = fullname
        self._source_path = source_path

    @icontract.require(lambda spec: isinstance(spec, ModuleSpec), "spec must be a ModuleSpec")
    @icontract.ensure(
        lambda result: result is None or isinstance(result, ModuleType),
        "result must be a module or None",
    )
    def create_module(self, spec: ModuleSpec) -> ModuleType | None:
        """Delegate module object creation to Python's default import machinery."""
        return None

    @icontract.require(lambda module: isinstance(module, ModuleType), "module must be a module")
    @icontract.ensure(lambda result: result is None, "exec_module returns None")
    def exec_module(self, module: ModuleType) -> None:
        """Execute the current source text into the provided module namespace."""
        source = self._source_path.read_text(encoding="utf-8")
        code = compile(source, str(self._source_path), "exec")
        exec(code, module.__dict__)


@icontract.invariant(
    lambda self: isinstance(self._search_paths, tuple) and isinstance(self._module_roots, tuple),
    "finder must keep immutable search paths and module roots",
)
class _FreshSourceFinder(importlib.abc.MetaPathFinder):
    """Finder that resolves selected local modules directly to source files."""

    @icontract.require(lambda search_paths: isinstance(search_paths, tuple), "search_paths must be a tuple")
    @icontract.require(lambda module_roots: isinstance(module_roots, tuple), "module_roots must be a tuple")
    @icontract.ensure(lambda result: result is None, "initialization returns None")
    def __init__(self, search_paths: tuple[str, ...], module_roots: tuple[str, ...]) -> None:
        """Store the search roots and package names that should bypass bytecode caches."""
        self._search_paths = tuple(Path(path).resolve() for path in search_paths)
        self._module_roots = module_roots

    @icontract.require(lambda fullname: is_non_empty_string(fullname), "fullname must be a non-empty string")
    @icontract.ensure(
        lambda result: result is None or isinstance(result, ModuleSpec),
        "result must be a ModuleSpec or None",
    )
    def find_spec(
        self,
        fullname: str,
        path: object = None,
        target: ModuleType | None = None,
    ) -> ModuleSpec | None:
        """Resolve eligible local modules to specs backed by fresh source execution."""
        if not any(fullname == root or fullname.startswith(root + ".") for root in self._module_roots):
            return None

        relative_parts = fullname.split(".")
        # Loop invariant: no prior search path has resolved fullname to a package or module source file.
        for search_path in self._search_paths:
            package_init = search_path.joinpath(*relative_parts, "__init__.py")
            if package_init.is_file():
                loader = _FreshSourceLoader(fullname, package_init)
                return importlib.util.spec_from_file_location(
                    fullname,
                    package_init,
                    loader=loader,
                    submodule_search_locations=[str(package_init.parent)],
                )

            module_file = search_path.joinpath(*relative_parts).with_suffix(".py")
            if module_file.is_file():
                loader = _FreshSourceLoader(fullname, module_file)
                return importlib.util.spec_from_file_location(
                    fullname,
                    module_file,
                    loader=loader,
                )

        return None


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

    # Loop invariant: inserted contains search paths added from reversed(search_paths)[0..i]
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
