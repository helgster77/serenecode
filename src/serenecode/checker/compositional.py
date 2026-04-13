"""Compositional verification checker for Serenecode (Level 6).

This module implements Level 6 verification: module-level analysis that
checks component interactions, dependency direction, interface compliance,
and system-level properties across the entire codebase.

This is a core module — no I/O operations are permitted. Source code
is received as structured SourceFile objects with pre-read content.
"""

from __future__ import annotations

import time

import icontract

from serenecode.checker.compositional_integration import (
    _call_integration_is_satisfied,
    _find_file_for_module,
    _implements_integration_issue,
    _module_name_matches_reference,
    _node_satisfies_call_integration,
    _node_satisfies_single_integration_target,
    _parse_integration_target_list,
    _protocol_signature_issue,
    check_assume_guarantee,
    check_data_flow,
    check_declared_integrations,
    check_system_invariants,
)
from serenecode.checker.compositional_parsing import (
    ClassInfo,
    FunctionInfo,
    MethodSignature,
    ModuleInfo,
    ParameterInfo,
    ProtocolInfo,
    _get_name,
    _is_public_function_name,
    _module_package_name,
    _module_path_has_segment,
    _normalize_module_name,
    _parse_method_signature,
    _resolve_from_import_module,
    _should_check_class_invariants,
    _should_check_function_contracts,
    parse_module_info,
)
from serenecode.config import SerenecodeConfig, is_core_module, is_exempt_module
from serenecode.contracts.predicates import is_non_empty_string
from serenecode.models import (
    CheckResult,
    CheckStatus,
    Detail,
    FunctionResult,
    VerificationLevel,
    make_check_result,
)

# Re-export data structures and key functions so that existing imports
# (e.g. ``from serenecode.checker.compositional import ModuleInfo``)
# continue to work without changes.
__all__ = [
    # Data structures
    "ClassInfo",
    "FunctionInfo",
    "MethodSignature",
    "ModuleInfo",
    "ParameterInfo",
    "ProtocolInfo",
    # Public check functions
    "check_assume_guarantee",
    "check_circular_dependencies",
    "check_compositional",
    "check_contract_completeness",
    "check_data_flow",
    "check_declared_integrations",
    "check_dependency_direction",
    "check_interface_compliance",
    "check_system_invariants",
    "parse_module_info",
    # Private helpers re-exported for tests and sibling modules
    "_call_integration_is_satisfied",
    "_check_signature_compatibility",
    "_class_likely_implements",
    "_find_cycles",
    "_find_file_for_module",
    "_get_name",
    "_implements_integration_issue",
    "_is_adapter_import",
    "_is_cli_import",
    "_is_enum_class",
    "_is_exception_class",
    "_is_public_function_name",
    "_module_name_matches_reference",
    "_module_package_name",
    "_module_path_has_segment",
    "_normalize_module_name",
    "_node_satisfies_call_integration",
    "_node_satisfies_single_integration_target",
    "_parse_integration_target_list",
    "_parse_method_signature",
    "_protocol_signature_issue",
    "_resolve_from_import_module",
    "_should_check_class_invariants",
    "_should_check_function_contracts",
]


# ---------------------------------------------------------------------------
# Compositional checks — dependency direction
# ---------------------------------------------------------------------------


@icontract.require(
    lambda modules: isinstance(modules, list),
    "modules must be a list",
)
@icontract.ensure(
    lambda result: isinstance(result, list),
    "result must be a list",
)
def check_dependency_direction(
    modules: list[ModuleInfo],
    config: SerenecodeConfig,
) -> list[FunctionResult]:
    """Check that import dependencies follow the hexagonal architecture.

    Rules:
    - core/ must not import from adapters/
    - ports/ must not import from adapters/
    - core/ must not import from cli.py
    - No circular imports between core modules

    Args:
        modules: List of parsed module information.
        config: Active Serenecode configuration.

    Returns:
        List of FunctionResult for dependency violations.
    """
    results: list[FunctionResult] = []

    # Dependency direction checks apply to core and port modules.
    # Unlike contract exemptions, architectural rules are always enforced.
    # Loop invariant: results contains dependency violations for modules[0..i]
    for mod in modules:
        is_core = is_core_module(mod.module_path, config)
        is_port = _module_path_has_segment(mod.module_path, "ports")

        if not is_core and not is_port:
            continue

        # Check imports
        # Loop invariant: results contains violations for imports[0..j]
        for imp in mod.imports:
            violation = _check_import_direction(imp, is_core, is_port)
            if violation:
                results.append(FunctionResult(
                    function="<module>",
                    file=mod.file_path,
                    line=1,
                    level_requested=6,
                    level_achieved=5,
                    status=CheckStatus.FAILED,
                    details=(Detail(
                        level=VerificationLevel.COMPOSITIONAL,
                        tool="compositional",
                        finding_type="violation",
                        message=violation,
                        suggestion="Move this import to an adapter module or use dependency injection",
                    ),),
                ))

        # Check from-imports
        # Loop invariant: results contains violations for from_imports[0..j]
        for from_mod, _ in mod.from_imports:
            violation = _check_import_direction(from_mod, is_core, is_port)
            if violation:
                results.append(FunctionResult(
                    function="<module>",
                    file=mod.file_path,
                    line=1,
                    level_requested=6,
                    level_achieved=5,
                    status=CheckStatus.FAILED,
                    details=(Detail(
                        level=VerificationLevel.COMPOSITIONAL,
                        tool="compositional",
                        finding_type="violation",
                        message=violation,
                        suggestion="Move this import to an adapter module or use dependency injection",
                    ),),
                ))

    return results


@icontract.require(
    lambda imported: is_non_empty_string(imported),
    "imported must be a non-empty string",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _is_adapter_import(imported: str) -> bool:
    """Check if an import refers to an adapter module by segment matching.

    Matches 'adapters' as a complete module/path segment, not as a substring.

    Args:
        imported: The imported module or path string.

    Returns:
        True if the import refers to an adapter module.
    """
    segments = imported.replace("/", ".").split(".")
    return "adapters" in segments


@icontract.require(
    lambda imported: is_non_empty_string(imported),
    "imported must be a non-empty string",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _is_cli_import(imported: str) -> bool:
    """Check if an import refers to a CLI module by segment matching.

    Matches 'cli' as a complete module segment, not as a substring.
    For example, 'cli' and 'myproject.cli' match, but 'click' does not.

    Args:
        imported: The imported module or path string.

    Returns:
        True if the import refers to a CLI module.
    """
    segments = imported.replace("/", ".").split(".")
    return "cli" in segments


@icontract.require(
    lambda imported: is_non_empty_string(imported),
    "imported must be a non-empty string",
)
@icontract.require(lambda is_core: isinstance(is_core, bool), "is_core must be a bool")
@icontract.require(lambda is_port: isinstance(is_port, bool), "is_port must be a bool")
@icontract.ensure(
    lambda result: result is None or isinstance(result, str),
    "result must be a string or None",
)
def _check_import_direction(
    imported: str,
    is_core: bool,
    is_port: bool,
) -> str | None:
    """Check if a single import violates dependency direction.

    Uses segment-based matching to avoid false positives on library
    names that contain 'cli' or 'adapters' as substrings.

    Args:
        imported: The imported module name.
        is_core: Whether the importing module is a core module.
        is_port: Whether the importing module is a port module.

    Returns:
        A violation message string, or None if the import is valid.
    """
    if _is_adapter_import(imported):
        location = "core" if is_core else "ports"
        return f"Module in {location}/ imports from adapters: '{imported}'"

    if is_core and _is_cli_import(imported):
        return f"Core module imports from CLI: '{imported}'"

    return None


# ---------------------------------------------------------------------------
# Interface compliance
# ---------------------------------------------------------------------------


@icontract.require(
    lambda modules: isinstance(modules, list),
    "modules must be a list",
)
@icontract.ensure(
    lambda result: isinstance(result, list),
    "result must be a list",
)
def check_interface_compliance(
    modules: list[ModuleInfo],
    config: SerenecodeConfig,
) -> list[FunctionResult]:
    """Check that adapter classes implement all Protocol methods.

    For each Protocol defined in ports/, finds classes in adapters/
    that claim to implement it (by type or convention) and verifies
    all Protocol methods are present.

    Args:
        modules: List of parsed module information.
        config: Active Serenecode configuration.

    Returns:
        List of FunctionResult for compliance violations.
    """
    protocols = _collect_port_protocols(modules)
    if not protocols:
        return []
    adapter_classes = _collect_adapter_classes_with_files(modules)

    results: list[FunctionResult] = []
    # Loop invariant: results contains compliance findings for protocols[0..i]
    for _port_file, proto in protocols:
        # Loop invariant: checked adapter_classes[0..j] against this protocol
        for adapter_file, adapter_cls in adapter_classes:
            if not _class_likely_implements(adapter_cls, proto):
                continue
            results.extend(_check_adapter_vs_protocol(
                adapter_file, adapter_cls, proto,
            ))

    return results


def _collect_port_protocols(
    modules: list[ModuleInfo],
) -> list[tuple[str, ProtocolInfo]]:
    """Collect all protocols from port modules."""
    protocols: list[tuple[str, ProtocolInfo]] = []
    # Loop invariant: protocols updated for modules[0..i]
    for mod in modules:
        if _module_path_has_segment(mod.module_path, "ports"):
            for proto in mod.protocols:
                protocols.append((mod.file_path, proto))
    return protocols


def _collect_adapter_classes_with_files(
    modules: list[ModuleInfo],
) -> list[tuple[str, ClassInfo]]:
    """Collect all classes from adapter modules with their file paths."""
    adapter_classes: list[tuple[str, ClassInfo]] = []
    # Loop invariant: adapter_classes updated for modules[0..i]
    for mod in modules:
        if _module_path_has_segment(mod.module_path, "adapters"):
            for cls in mod.classes:
                adapter_classes.append((mod.file_path, cls))
    return adapter_classes


def _check_adapter_vs_protocol(
    adapter_file: str,
    adapter_cls: ClassInfo,
    proto: ProtocolInfo,
) -> list[FunctionResult]:
    """Check one adapter class against one Protocol for missing methods and signature mismatches."""
    results: list[FunctionResult] = []
    proto_method_names = {m.name for m in proto.methods}
    adapter_method_names = set(adapter_cls.methods)
    missing = proto_method_names - adapter_method_names

    # Loop invariant: results contains findings for missing[0..k]
    for method_name in sorted(missing):
        results.append(FunctionResult(
            function=adapter_cls.name, file=adapter_file, line=adapter_cls.line,
            level_requested=6, level_achieved=5, status=CheckStatus.FAILED,
            details=(Detail(
                level=VerificationLevel.COMPOSITIONAL, tool="compositional",
                finding_type="violation",
                message=f"Class '{adapter_cls.name}' appears to implement '{proto.name}' but is missing method '{method_name}'",
                suggestion=f"Add method '{method_name}' to '{adapter_cls.name}'",
            ),),
        ))

    adapter_sig_map = {s.name: s for s in adapter_cls.method_signatures}
    # Loop invariant: results contains signature findings for proto.methods[0..k]
    for proto_method in proto.methods:
        adapter_sig = adapter_sig_map.get(proto_method.name)
        if adapter_sig is None:
            continue
        for issue in _check_signature_compatibility(adapter_sig, proto_method):
            results.append(FunctionResult(
                function=adapter_cls.name, file=adapter_file, line=adapter_cls.line,
                level_requested=6, level_achieved=5, status=CheckStatus.FAILED,
                details=(Detail(
                    level=VerificationLevel.COMPOSITIONAL, tool="compositional",
                    finding_type="violation",
                    message=f"Class '{adapter_cls.name}' vs Protocol '{proto.name}': {issue}",
                    suggestion=f"Update method signature to match Protocol '{proto.name}'",
                ),),
            ))
    return results


@icontract.require(
    lambda adapter_sig: isinstance(adapter_sig, MethodSignature),
    "adapter_sig must be a MethodSignature",
)
@icontract.require(
    lambda proto_sig: isinstance(proto_sig, MethodSignature),
    "proto_sig must be a MethodSignature",
)
@icontract.ensure(
    lambda result: isinstance(result, list),
    "result must be a list",
)
def _check_signature_compatibility(
    adapter_sig: MethodSignature,
    proto_sig: MethodSignature,
) -> list[str]:
    """Check if an adapter method signature is compatible with a Protocol method.

    Args:
        adapter_sig: The adapter class method signature.
        proto_sig: The Protocol method signature to check against.

    Returns:
        List of incompatibility descriptions (empty if compatible).
    """
    issues: list[str] = []
    if len(adapter_sig.parameters) < len(proto_sig.parameters):
        issues.append(
            f"Method '{proto_sig.name}' implementation has "
            f"{len(adapter_sig.parameters)} parameters but Protocol "
            f"requires {len(proto_sig.parameters)}"
        )
    if adapter_sig.required_parameters > proto_sig.required_parameters:
        issues.append(
            f"Method '{proto_sig.name}' implementation requires "
            f"{adapter_sig.required_parameters} parameters but Protocol "
            f"requires only {proto_sig.required_parameters}"
        )
    if proto_sig.has_return_annotation and not adapter_sig.has_return_annotation:
        issues.append(
            f"Method '{proto_sig.name}' missing return annotation "
            f"(Protocol specifies one)"
        )
    if (
        proto_sig.return_annotation is not None
        and adapter_sig.return_annotation is not None
        and "".join(proto_sig.return_annotation.split()) != "".join(adapter_sig.return_annotation.split())
    ):
        issues.append(
            f"Method '{proto_sig.name}' return annotation "
            f"'{adapter_sig.return_annotation}' does not match Protocol "
            f"annotation '{proto_sig.return_annotation}'"
        )
    return issues


@icontract.require(lambda cls: isinstance(cls, ClassInfo), "cls must be a ClassInfo")
@icontract.require(
    lambda proto: isinstance(proto, ProtocolInfo),
    "proto must be a ProtocolInfo",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _class_likely_implements(cls: ClassInfo, proto: ProtocolInfo) -> bool:
    """Heuristic to check if a class likely implements a protocol.

    Checks if the class name contains the protocol name (without
    'Protocol' suffix), or if they share most method names.

    Args:
        cls: The candidate implementing class.
        proto: The Protocol to check against.

    Returns:
        True if the class likely implements the protocol.
    """
    # Loop invariant: no base seen so far explicitly names the protocol.
    for base in cls.bases:
        if base == proto.name or base.endswith(f".{proto.name}"):
            return True

    # Name-based heuristic
    proto_base = proto.name.replace("Protocol", "")
    if proto_base and proto_base.lower() in cls.name.lower():
        return True

    # Method overlap heuristic — if >50% of protocol methods are present
    if not proto.methods:
        return False
    proto_method_names = {m.name for m in proto.methods}
    cls_method_names = set(cls.methods)
    overlap = proto_method_names & cls_method_names
    return len(overlap) > len(proto_method_names) * 0.5


_ENUM_BASE_NAMES = frozenset({
    "Enum", "IntEnum", "StrEnum", "Flag", "IntFlag",
    "enum.Enum", "enum.IntEnum", "enum.StrEnum", "enum.Flag", "enum.IntFlag",
})


@icontract.require(lambda cls: isinstance(cls, ClassInfo), "cls must be a ClassInfo")
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _is_enum_class(cls: ClassInfo) -> bool:
    """Check if a class is an Enum subclass based on its bases.

    Args:
        cls: The class info to check.

    Returns:
        True if the class inherits from an Enum base.
    """
    # Loop invariant: checked bases[0..i] against _ENUM_BASE_NAMES
    for base in cls.bases:
        if base in _ENUM_BASE_NAMES:
            return True
    return False


@icontract.require(lambda cls: isinstance(cls, ClassInfo), "cls must be a ClassInfo")
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _is_exception_class(cls: ClassInfo) -> bool:
    """Check if a class participates in an exception hierarchy."""
    # Loop invariant: checked bases[0..i] for exception-like base names
    for base in cls.bases:
        if base in {"Exception", "BaseException"}:
            return True
        if base.endswith(("Error", "Exception")):
            return True
    return False


# ---------------------------------------------------------------------------
# Contract completeness
# ---------------------------------------------------------------------------


@icontract.require(
    lambda modules: isinstance(modules, list),
    "modules must be a list",
)
@icontract.ensure(
    lambda result: isinstance(result, list),
    "result must be a list",
)
def check_contract_completeness(
    modules: list[ModuleInfo],
    config: SerenecodeConfig,
) -> list[FunctionResult]:
    """Check that all public functions across the codebase have contracts.

    Uses FunctionInfo metadata to verify that every public function in
    non-exempt modules has icontract.require (if it has parameters) and
    icontract.ensure decorators, and every public class has an invariant.

    Args:
        modules: List of parsed module information.
        config: Active Serenecode configuration.

    Returns:
        List of FunctionResult for contract coverage violations.
    """
    results: list[FunctionResult] = []
    # Loop invariant: results contains completeness findings for modules[0..i]
    for mod in modules:
        if is_exempt_module(mod.module_path, config):
            continue
        results.extend(_check_function_contracts_completeness(mod, config))
        results.extend(_check_class_invariants_completeness(mod, config))
        _check_module_size_info(results, mod)
    return results


def _check_function_contracts_completeness(
    mod: ModuleInfo,
    config: SerenecodeConfig,
) -> list[FunctionResult]:
    """Check functions in a module for contract presence."""
    results: list[FunctionResult] = []
    # Loop invariant: results contains findings for function_infos[0..j]
    for func_info in mod.function_infos:
        if not _should_check_function_contracts(func_info, config):
            continue
        details: list[Detail] = []
        if len(func_info.parameters) > 0 and not func_info.has_require:
            details.append(Detail(
                level=VerificationLevel.COMPOSITIONAL, tool="compositional",
                finding_type="violation",
                message=f"Function '{func_info.name}' in {mod.module_path} missing @icontract.require (precondition)",
                suggestion="Add precondition contract",
            ))
        if not func_info.has_ensure:
            details.append(Detail(
                level=VerificationLevel.COMPOSITIONAL, tool="compositional",
                finding_type="violation",
                message=f"Function '{func_info.name}' in {mod.module_path} missing @icontract.ensure (postcondition)",
                suggestion="Add postcondition contract",
            ))
        if details:
            results.append(FunctionResult(
                function=func_info.name, file=mod.file_path, line=func_info.line,
                level_requested=6, level_achieved=5, status=CheckStatus.FAILED,
                details=tuple(details),
            ))
    return results


def _check_class_invariants_completeness(
    mod: ModuleInfo,
    config: SerenecodeConfig,
) -> list[FunctionResult]:
    """Check classes in a module for invariant presence."""
    results: list[FunctionResult] = []
    # Loop invariant: results contains findings for classes[0..j]
    for cls in mod.classes:
        if not _should_check_class_invariants(cls, config):
            continue
        if _is_enum_class(cls) or _is_exception_class(cls) or cls.is_protocol or cls.has_no_invariant_comment:
            continue
        if not cls.has_invariant:
            results.append(FunctionResult(
                function=cls.name, file=mod.file_path, line=cls.line,
                level_requested=6, level_achieved=5, status=CheckStatus.FAILED,
                details=(Detail(
                    level=VerificationLevel.COMPOSITIONAL, tool="compositional",
                    finding_type="violation",
                    message=f"Class '{cls.name}' in {mod.module_path} missing @icontract.invariant",
                    suggestion="Add class invariant",
                ),),
            ))
    return results


def _check_module_size_info(
    results: list[FunctionResult],
    mod: ModuleInfo,
) -> None:
    """Flag large modules as informational."""
    total_public = len([f for f in mod.function_infos if f.is_public])
    if total_public > 10:
        results.append(FunctionResult(
            function="<module>", file=mod.file_path, line=1,
            level_requested=6, level_achieved=6, status=CheckStatus.PASSED,
            details=(Detail(
                level=VerificationLevel.COMPOSITIONAL, tool="compositional",
                finding_type="info",
                message=f"Module has {total_public} public functions — consider splitting into smaller modules",
            ),),
        ))


# ---------------------------------------------------------------------------
# Circular dependency detection
# ---------------------------------------------------------------------------


@icontract.require(
    lambda modules: isinstance(modules, list),
    "modules must be a list",
)
@icontract.ensure(
    lambda result: isinstance(result, list),
    "result must be a list",
)
def check_circular_dependencies(
    modules: list[ModuleInfo],
    config: SerenecodeConfig,
) -> list[FunctionResult]:
    """Detect circular import dependencies between internal modules.

    Builds a directed graph from module imports, filtering to only
    internal project modules, then uses DFS to detect cycles.

    Args:
        modules: List of parsed module information.
        config: Active Serenecode configuration.

    Returns:
        List of FunctionResult for circular dependency violations.
    """
    # Build set of known module paths for internal resolution
    known_modules: dict[str, ModuleInfo] = {}
    # Loop invariant: known_modules contains entries for modules[0..i]
    for mod in modules:
        known_modules[mod.module_path] = mod
        base = mod.module_path.removesuffix(".py")
        known_modules[base] = mod

    # Build adjacency list
    graph: dict[str, set[str]] = {mod.module_path: set() for mod in modules}

    # Loop invariant: graph contains edges for modules[0..i]
    for mod in modules:
        # Loop invariant: graph[mod] updated for imports[0..j]
        for imp in mod.imports:
            resolved = _resolve_to_known_module(imp, known_modules)
            if resolved and resolved != mod.module_path:
                graph[mod.module_path].add(resolved)
        # Loop invariant: graph[mod] updated for from_imports[0..j]
        for from_mod, imported_name in mod.from_imports:
            resolved = _resolve_from_import_target(
                from_mod,
                imported_name,
                known_modules,
            )
            if resolved and resolved != mod.module_path:
                graph[mod.module_path].add(resolved)

    cycles = _find_cycles(graph)

    results: list[FunctionResult] = []
    reported_cycles: set[frozenset[str]] = set()

    # Loop invariant: results contains findings for deduplicated cycles[0..i]
    for cycle in cycles:
        cycle_key = frozenset(cycle)
        if cycle_key in reported_cycles:
            continue
        reported_cycles.add(cycle_key)

        cycle_str = " -> ".join(cycle) + " -> " + cycle[0]
        first_file = known_modules[cycle[0]].file_path if cycle[0] in known_modules else "<unknown>"
        results.append(FunctionResult(
            function="<module>",
            file=first_file,
            line=1,
            level_requested=6,
            level_achieved=5,
            status=CheckStatus.FAILED,
            details=(Detail(
                level=VerificationLevel.COMPOSITIONAL,
                tool="compositional",
                finding_type="violation",
                message=f"Circular dependency detected: {cycle_str}",
                suggestion="Break the cycle by introducing a Protocol interface or restructuring imports",
            ),),
        ))

    return results


@icontract.require(
    lambda import_name: is_non_empty_string(import_name),
    "import_name must be a non-empty string",
)
@icontract.require(
    lambda known_modules: isinstance(known_modules, dict),
    "known_modules must be a dictionary",
)
@icontract.ensure(
    lambda result: result is None or isinstance(result, str),
    "result must be a string or None",
)
def _resolve_to_known_module(
    import_name: str,
    known_modules: dict[str, ModuleInfo],
) -> str | None:
    """Try to map an import name to a known internal module path.

    Attempts various transformations: dotted to path, with/without
    package prefix, etc.

    Args:
        import_name: The import module name to resolve.
        known_modules: Map of known module paths to ModuleInfo.

    Returns:
        The resolved module_path string, or None if not internal.
    """
    if import_name in known_modules:
        return known_modules[import_name].module_path

    path_form = import_name.replace(".", "/")
    if path_form in known_modules:
        return known_modules[path_form].module_path

    if f"{path_form}.py" in known_modules:
        return known_modules[f"{path_form}.py"].module_path

    # Try stripping common prefix segments
    parts = import_name.split(".")
    # Loop invariant: checked parts[i:] for matches
    for i in range(len(parts)):
        suffix = "/".join(parts[i:])
        if suffix in known_modules:
            return known_modules[suffix].module_path
        if f"{suffix}.py" in known_modules:
            return known_modules[f"{suffix}.py"].module_path

    return None


@icontract.require(
    lambda from_module: isinstance(from_module, str),
    "from_module must be a string",
)
@icontract.require(
    lambda imported_name: is_non_empty_string(imported_name),
    "imported_name must be a non-empty string",
)
@icontract.require(
    lambda known_modules: isinstance(known_modules, dict),
    "known_modules must be a dictionary",
)
@icontract.ensure(
    lambda result: result is None or isinstance(result, str),
    "result must be a string or None",
)
def _resolve_from_import_target(
    from_module: str,
    imported_name: str,
    known_modules: dict[str, ModuleInfo],
) -> str | None:
    """Resolve a from-import to the most specific known internal module."""
    if from_module:
        combined = _resolve_to_known_module(f"{from_module}.{imported_name}", known_modules)
        if combined is not None:
            return combined

        resolved = _resolve_to_known_module(from_module, known_modules)
        if resolved is not None:
            return resolved

    return _resolve_to_known_module(imported_name, known_modules)


@icontract.require(
    lambda graph: isinstance(graph, dict),
    "graph must be a dictionary",
)
@icontract.ensure(
    lambda result: isinstance(result, list),
    "result must be a list",
)
def _find_cycles(graph: dict[str, set[str]]) -> list[tuple[str, ...]]:
    """Find all cycles in a directed graph using DFS coloring.

    Args:
        graph: Adjacency list representation of the directed graph.

    Returns:
        List of tuples, each representing a cycle path.
    """
    white, gray, black = 0, 1, 2
    color: dict[str, int] = {v: white for v in graph}
    path: list[str] = []
    cycles: list[tuple[str, ...]] = []

    def _dfs(node: str) -> None:
        """DFS visit for cycle detection.

        Args:
            node: Current node to visit.
        """
        # Variant: number of WHITE nodes decreases with each call
        color[node] = gray
        path.append(node)
        # Loop invariant: checked neighbors[0..i] for back-edges
        for neighbor in graph.get(node, set()):
            if neighbor not in color:
                continue
            if color[neighbor] == gray:
                idx = path.index(neighbor)
                cycles.append(tuple(path[idx:]))
            elif color[neighbor] == white:
                _dfs(neighbor)
        path.pop()
        color[node] = black

    # Loop invariant: DFS completed for all WHITE nodes in graph[0..i]
    for node in graph:
        if color[node] == white:
            _dfs(node)

    return cycles


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


@icontract.require(
    lambda sources: isinstance(sources, (list, tuple)),
    "sources must be a list or tuple",
)
@icontract.require(
    lambda spec_content: spec_content is None or isinstance(spec_content, str),
    "spec_content must be None or a string",
)
@icontract.ensure(
    lambda result: result.level_requested == 6,
    "compositional check reports findings at the compositional level",
)
def check_compositional(
    sources: list[tuple[str, str, str]] | tuple[tuple[str, str, str], ...],
    config: SerenecodeConfig,
    spec_content: str | None = None,
) -> CheckResult:
    """Run the full Level 6 compositional check on a set of source files.

    This is the main entry point for compositional verification. It
    parses all modules, then runs all compositional checks: dependency
    direction, circular dependencies, interface compliance, contract
    completeness, assume-guarantee reasoning, data flow verification,
    and system invariants.

    Args:
        sources: Sequence of (source_code, file_path, module_path) tuples.
        config: Active Serenecode configuration.
        spec_content: Optional SPEC.md content used for declared-integration
            semantic checks.

    Returns:
        A CheckResult containing all compositional findings.
    """
    start_time = time.monotonic()

    # Parse all modules
    modules: list[ModuleInfo] = []
    # Loop invariant: modules contains parsed info for sources[0..i]
    for source, file_path, module_path in sources:
        mod_info = parse_module_info(source, file_path, module_path)
        modules.append(mod_info)

    # Run all compositional checks
    all_results: list[FunctionResult] = []

    # Report parse errors so they are visible instead of silently ignored
    # Loop invariant: all_results contains parse error findings for modules[0..i]
    for mod in modules:
        if mod.parse_error is not None:
            all_results.append(FunctionResult(
                function="<module>",
                file=mod.file_path,
                line=1,
                level_requested=6,
                level_achieved=5,
                status=CheckStatus.SKIPPED,
                details=(Detail(
                    level=VerificationLevel.COMPOSITIONAL,
                    tool="compositional",
                    finding_type="parse_error",
                    message=f"Could not parse '{mod.file_path}': {mod.parse_error}",
                    suggestion="Fix the syntax error before running compositional verification",
                ),),
            ))
    all_results.extend(check_dependency_direction(modules, config))
    all_results.extend(check_circular_dependencies(modules, config))
    all_results.extend(check_interface_compliance(modules, config))
    all_results.extend(check_contract_completeness(modules, config))
    all_results.extend(check_assume_guarantee(modules, config))
    all_results.extend(check_data_flow(modules, config))
    all_results.extend(check_system_invariants(modules, config))
    all_results.extend(check_declared_integrations(modules, sources, spec_content))

    elapsed = time.monotonic() - start_time
    return make_check_result(
        tuple(all_results),
        level_requested=6,
        duration_seconds=elapsed,
    )
