"""Suppression of dead-code advisories that static call graphs cannot see.

Vulture decides a symbol is unused by looking for its name elsewhere in the
scanned sources. Two common patterns defeat that: functions handed to a
registry by a decorator (`@app.get`, `@router.post`, `@asynccontextmanager`)
and methods invoked through a base-class reference. Both are called, just never
by the name Vulture is looking for.

This is a core module — no I/O operations are permitted. Source code is
received as strings, not read from files.
"""

from __future__ import annotations

import ast

import icontract

from serenecode.contracts.predicates import is_non_empty_string


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


# Decorators that annotate a function without handing it to anything else, so
# a function wearing only these is still dead if nothing names it. Matched on
# the final dotted segment, so `icontract.require` and `require` both hit.
_INERT_DECORATORS = frozenset({
    "abstractmethod",
    "cache",
    "cached_property",
    "classmethod",
    "dataclass",
    "deleter",
    "ensure",
    "final",
    "getter",
    "invariant",
    "lru_cache",
    "overload",
    "property",
    "require",
    "setter",
    "snapshot",
    "staticmethod",
    "wraps",
})

# Decorator that declares the function replaces a base-class method.
_OVERRIDE_DECORATORS = frozenset({"override"})

# Dead-code symbol types this module can reason about. Variables, attributes,
# imports and classes are left to the advisory as reported.
_SUPPRESSIBLE_SYMBOL_TYPES = frozenset({"function", "method"})

# Bound on base-class chain walking, so a malformed or cyclic hierarchy in the
# scanned sources cannot spin.
_MAX_BASE_DEPTH = 20


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@icontract.require(
    lambda sources: isinstance(sources, tuple),
    "sources must be a tuple of (file_path, source) pairs",
)
@icontract.ensure(lambda result: isinstance(result, frozenset), "result must be a frozenset")
def registered_symbol_sites(
    sources: tuple[tuple[str, str], ...],
) -> frozenset[tuple[str, str, int]]:
    """Locate definitions whose callers a name-based scan cannot find.

    Implements: REQ-045

    Args:
        sources: Tuple of (file_path, source) pairs for the scanned set.

    Returns:
        Frozenset of (normalized file path, symbol name, line) sites to
        suppress. One entry is produced per line a dead-code backend might
        report for the definition — each decorator line plus the `def` line —
        because backends differ on which line they attribute to a decorated
        function.
    """
    trees = _parse_sources(sources)
    base_methods = _inherited_method_names(trees)
    sites: set[tuple[str, str, int]] = set()

    # Loop invariant: sites holds suppression entries for trees[0..i]
    for file_path, tree in trees:
        for class_name, node in _iter_definitions(tree):
            if not _is_registered_or_override(node, class_name, base_methods):
                continue
            for line in _definition_lines(node):
                sites.add((normalize_path(file_path), node.name, line))

    return frozenset(sites)


@icontract.require(lambda file_path: is_non_empty_string(file_path), "file_path must be non-empty")
@icontract.require(lambda symbol_name: is_non_empty_string(symbol_name), "symbol_name must be non-empty")
@icontract.require(lambda line: isinstance(line, int) and line >= 1, "line must be >= 1")
@icontract.require(lambda symbol_type: isinstance(symbol_type, str), "symbol_type must be a string")
@icontract.require(lambda sites: isinstance(sites, frozenset), "sites must be a frozenset")
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a bool")
def is_suppressed_dead_code(
    file_path: str,
    symbol_name: str,
    line: int,
    symbol_type: str,
    sites: frozenset[tuple[str, str, int]],
) -> bool:
    """Return True when a dead-code finding names a registered definition.

    Implements: REQ-045
    """
    if symbol_type not in _SUPPRESSIBLE_SYMBOL_TYPES:
        return False
    return (normalize_path(file_path), symbol_name, line) in sites



@icontract.require(lambda path: isinstance(path, str), "path must be a string")
@icontract.ensure(
    lambda result: "\\" not in result and not result.startswith("./"),
    "result must use forward separators and carry no leading ./",
)
def normalize_path(path: str) -> str:
    """Normalize separators and drop a leading './' for path comparison.

    Implements: REQ-045

    A core module cannot touch the filesystem, so comparison is textual.
    """
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


@icontract.require(lambda reported_path: is_non_empty_string(reported_path), "reported_path must be non-empty")
@icontract.require(lambda known_paths: isinstance(known_paths, tuple), "known_paths must be a tuple")
@icontract.ensure(lambda result: is_non_empty_string(result), "result must be a non-empty string")
def resolve_report_path(reported_path: str, known_paths: tuple[str, ...]) -> str:
    """Map a backend's absolute path back to the project's own spelling.

    Implements: REQ-047

    Vulture absolutizes every path it is given, so its findings would
    otherwise carry absolute paths while structural findings carry the paths
    the caller passed in — one report, two spellings of the same file.

    Args:
        reported_path: Path as the dead-code backend reported it.
        known_paths: Paths of the scanned source files, as the caller spells
            them.

    Returns:
        The matching entry from `known_paths`, or `reported_path` unchanged
        when nothing matches. Empty entries are ignored: an empty path is a
        suffix of everything and would otherwise swallow every finding.
    """
    normalized_report = normalize_path(reported_path)

    # Loop invariant: no known path in [0..i] denotes the reported file
    for known in known_paths:
        normalized_known = normalize_path(known)
        if not normalized_known:
            continue
        if normalized_report == normalized_known:
            return known
        if normalized_report.endswith("/" + normalized_known):
            return known

    return reported_path


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


@icontract.require(lambda sources: isinstance(sources, tuple), "sources must be a tuple")
@icontract.ensure(lambda result: isinstance(result, list), "result must be a list")
def _parse_sources(
    sources: tuple[tuple[str, str], ...],
) -> list[tuple[str, ast.Module]]:
    """Parse each source, skipping any file that does not compile."""
    trees: list[tuple[str, ast.Module]] = []

    # Loop invariant: trees holds parsed modules for sources[0..i]
    for file_path, source in sources:
        # silent-except: suppression is best-effort; an unparseable file yields no suppression sites and is reported by other checks
        try:
            trees.append((file_path, ast.parse(source)))
        except (SyntaxError, TypeError, ValueError):
            continue

    return trees


@icontract.require(lambda tree: isinstance(tree, ast.Module), "tree must be an ast.Module")
@icontract.ensure(lambda result: isinstance(result, list), "result must be a list")
def _iter_definitions(
    tree: ast.Module,
) -> list[tuple[str | None, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Return top-level functions and methods with their owning class name."""
    definitions: list[tuple[str | None, ast.FunctionDef | ast.AsyncFunctionDef]] = []

    # Loop invariant: definitions holds defs from top-level nodes[0..i]
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            definitions.append((None, node))
        elif isinstance(node, ast.ClassDef):
            # Loop invariant: definitions holds methods from class body[0..j]
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    definitions.append((node.name, child))

    return definitions


@icontract.require(
    lambda node: isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)),
    "node must be a function definition",
)
@icontract.ensure(lambda result: isinstance(result, list), "result must be a list")
def _definition_lines(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[int]:
    """Return every line a backend might attribute to this definition."""
    lines = [node.lineno]
    lines.extend(decorator.lineno for decorator in node.decorator_list)
    return lines


@icontract.require(
    lambda node: isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)),
    "node must be a function definition",
)
@icontract.require(lambda base_methods: isinstance(base_methods, dict), "base_methods must be a dict")
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a bool")
def _is_registered_or_override(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    class_name: str | None,
    base_methods: dict[str, frozenset[str]],
) -> bool:
    """Decide whether a definition's callers are invisible to a name scan."""
    # Loop invariant: no decorator in [0..i] registers or overrides the def
    for decorator in node.decorator_list:
        segment = _decorator_segment(decorator)
        if segment in _OVERRIDE_DECORATORS:
            return True
        if segment not in _INERT_DECORATORS:
            return True

    if class_name is None:
        return False
    return node.name in base_methods.get(class_name, frozenset())


@icontract.require(lambda decorator: isinstance(decorator, ast.expr), "decorator must be an expression")
@icontract.ensure(lambda result: isinstance(result, str), "result must be a string")
def _decorator_segment(decorator: ast.expr) -> str:
    """Return the final dotted segment of a decorator's name."""
    node = decorator
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


# ---------------------------------------------------------------------------
# Base-class resolution
# ---------------------------------------------------------------------------


@icontract.require(lambda trees: isinstance(trees, list), "trees must be a list")
@icontract.ensure(lambda result: isinstance(result, dict), "result must be a dict")
def _inherited_method_names(
    trees: list[tuple[str, ast.Module]],
) -> dict[str, frozenset[str]]:
    """Map each class name to the method names its base classes declare.

    Bases are resolved by simple name across the whole scanned set, so a
    Protocol or ABC declared in another module of the project resolves.
    Bases defined outside the scanned sources — third-party frameworks —
    cannot be resolved and contribute nothing; `@override` covers those.
    """
    declared: dict[str, frozenset[str]] = {}
    bases: dict[str, tuple[str, ...]] = {}

    # Loop invariant: declared and bases hold entries for classes in trees[0..i]
    for _, tree in trees:
        for node in ast.iter_child_nodes(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            declared[node.name] = frozenset(
                child.name
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            )
            bases[node.name] = tuple(
                segment
                for segment in (_base_segment(base) for base in node.bases)
                if segment
            )

    return {
        class_name: _collect_base_methods(class_name, declared, bases)
        for class_name in declared
    }


@icontract.require(lambda base: isinstance(base, ast.expr), "base must be an expression")
@icontract.ensure(lambda result: isinstance(result, str), "result must be a string")
def _base_segment(base: ast.expr) -> str:
    """Return the final dotted segment of a base-class expression."""
    node = base
    if isinstance(node, ast.Subscript):
        node = node.value
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


@icontract.require(lambda class_name: is_non_empty_string(class_name), "class_name must be non-empty")
@icontract.require(lambda declared: isinstance(declared, dict), "declared must be a dict")
@icontract.require(lambda bases: isinstance(bases, dict), "bases must be a dict")
@icontract.ensure(lambda result: isinstance(result, frozenset), "result must be a frozenset")
def _collect_base_methods(
    class_name: str,
    declared: dict[str, frozenset[str]],
    bases: dict[str, tuple[str, ...]],
) -> frozenset[str]:
    """Collect method names declared by a class's transitive base classes."""
    collected: set[str] = set()
    seen = {class_name}
    pending = list(bases.get(class_name, ()))
    depth = 0

    # Loop invariant: collected holds methods of every base popped so far
    while pending and depth < _MAX_BASE_DEPTH:
        depth += 1
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        collected.update(declared.get(current, frozenset()))
        pending.extend(bases.get(current, ()))

    return frozenset(collected)
