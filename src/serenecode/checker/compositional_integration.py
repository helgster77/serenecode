"""Integration and system-level checks for compositional analysis.

This module implements assume-guarantee reasoning, data flow verification,
system invariant checks, and declared integration validation for the
Level 6 compositional checker.

This is a core module — no I/O operations are permitted. Source code
is received as structured SourceFile objects with pre-read content.
"""

from __future__ import annotations

import ast

import icontract

from serenecode.checker.compositional_parsing import (
    ClassInfo,
    FunctionInfo,
    ModuleInfo,
    ProtocolInfo,
    _get_call_target_name,
    _module_path_has_segment,
    _normalize_module_name,
)
from serenecode.config import SerenecodeConfig, is_core_module, is_exempt_module
from serenecode.contracts.predicates import is_non_empty_string
from serenecode.models import (
    CheckStatus,
    Detail,
    FunctionResult,
    VerificationLevel,
)


# ---------------------------------------------------------------------------
# Assume-guarantee reasoning
# ---------------------------------------------------------------------------


@icontract.require(
    lambda modules: isinstance(modules, list),
    "modules must be a list",
)
@icontract.ensure(
    lambda result: isinstance(result, list),
    "result must be a list",
)
def check_assume_guarantee(
    modules: list[ModuleInfo],
    config: SerenecodeConfig,
) -> list[FunctionResult]:
    """Check assume-guarantee reasoning across module boundaries.

    For each cross-module function call, verifies that:
    1. If the callee has preconditions, the caller has postconditions
       to guarantee them.
    2. If the callee has preconditions and the caller has parameters,
       the caller has preconditions to constrain its own inputs.

    Args:
        modules: List of parsed module information.
        config: Active Serenecode configuration.

    Returns:
        List of FunctionResult for assume-guarantee violations.
    """
    results: list[FunctionResult] = []
    module_functions = _build_module_function_map(modules)
    import_map = _build_import_resolution_map(modules)

    # Loop invariant: results contains assume-guarantee findings for modules[0..i]
    for mod in modules:
        if is_exempt_module(mod.module_path, config):
            continue
        results.extend(_check_assume_guarantee_for_module(
            mod, import_map, module_functions,
        ))

    return results


def _check_assume_guarantee_for_module(
    mod: ModuleInfo,
    import_map: dict[str, dict[str, tuple[str, str | None]]],
    module_functions: dict[str, dict[str, FunctionInfo]],
) -> list[FunctionResult]:
    """Check assume-guarantee reasoning for one module."""
    results: list[FunctionResult] = []
    reported_ensure: set[str] = set()
    reported_require: set[str] = set()

    # Loop invariant: results contains findings for function_infos[0..j]
    for func_info in mod.function_infos:
        if not func_info.is_public:
            continue
        for call_target in func_info.calls:
            resolved = _resolve_call_target(call_target, mod, import_map, module_functions)
            if resolved is None:
                continue
            callee_module, callee_func = resolved
            if callee_module == mod.module_path:
                continue

            ensure_key = f"{func_info.name}->{callee_module}"
            if callee_func.has_require and not func_info.has_ensure and ensure_key not in reported_ensure:
                reported_ensure.add(ensure_key)
                results.append(FunctionResult(
                    function=func_info.name, file=mod.file_path, line=func_info.line,
                    level_requested=6, level_achieved=5, status=CheckStatus.FAILED,
                    details=(Detail(
                        level=VerificationLevel.COMPOSITIONAL, tool="compositional",
                        finding_type="violation",
                        message=(
                            f"Function '{func_info.name}' calls '{callee_func.name}' "
                            f"(in {callee_module}) which has preconditions, but "
                            f"'{func_info.name}' lacks postconditions"
                        ),
                        suggestion=f"Add @icontract.ensure to '{func_info.name}' to document guarantees for '{callee_func.name}'",
                    ),),
                ))

            require_key = f"{func_info.name}->{callee_module}"
            if (
                callee_func.has_require and len(func_info.parameters) > 0
                and not func_info.has_require and require_key not in reported_require
            ):
                reported_require.add(require_key)
                results.append(FunctionResult(
                    function=func_info.name, file=mod.file_path, line=func_info.line,
                    level_requested=6, level_achieved=5, status=CheckStatus.FAILED,
                    details=(Detail(
                        level=VerificationLevel.COMPOSITIONAL, tool="compositional",
                        finding_type="violation",
                        message=(
                            f"Function '{func_info.name}' passes data to '{callee_func.name}' "
                            f"(in {callee_module}) which has preconditions, but "
                            f"'{func_info.name}' has no preconditions to constrain its inputs"
                        ),
                        suggestion=f"Add @icontract.require to '{func_info.name}' to constrain inputs flowing to '{callee_func.name}'",
                    ),),
                ))

    return results


@icontract.require(
    lambda modules: isinstance(modules, list),
    "modules must be a list",
)
@icontract.ensure(
    lambda result: isinstance(result, dict),
    "result must be a dictionary",
)
def _build_module_function_map(
    modules: list[ModuleInfo],
) -> dict[str, dict[str, FunctionInfo]]:
    """Build a lookup map of module_path -> {function_name -> FunctionInfo}.

    Args:
        modules: List of parsed module information.

    Returns:
        Nested dict mapping module paths to their function info by name.
    """
    result: dict[str, dict[str, FunctionInfo]] = {}
    # Loop invariant: result contains entries for modules[0..i]
    for mod in modules:
        func_map: dict[str, FunctionInfo] = {}
        # Loop invariant: func_map contains entries for function_infos[0..j]
        for fi in mod.function_infos:
            func_map[fi.name] = fi
        result[mod.module_path] = func_map
    return result


@icontract.require(
    lambda modules: isinstance(modules, list),
    "modules must be a list",
)
@icontract.ensure(
    lambda result: isinstance(result, dict),
    "result must be a dictionary",
)
def _build_import_resolution_map(
    modules: list[ModuleInfo],
) -> dict[str, dict[str, tuple[str, str | None]]]:
    """Build a map of what names are imported into each module.

    Args:
        modules: List of parsed module information.

    Returns:
        Dict of module_path -> {bound_name -> (source_module, original_name)}.
    """
    result: dict[str, dict[str, tuple[str, str | None]]] = {}
    # Loop invariant: result contains entries for modules[0..i]
    for mod in modules:
        names: dict[str, tuple[str, str | None]] = {}
        if mod.import_bindings:
            # Loop invariant: names contains entries for import_bindings[0..j]
            for bound_name, source_mod, original_name in mod.import_bindings:
                names[bound_name] = (source_mod, original_name)
        else:
            # Backward-compatible fallback for tests that build ModuleInfo manually.
            # Loop invariant: names contains entries for from_imports[0..j]
            for from_mod, name in mod.from_imports:
                names[name] = (from_mod, name)
        result[mod.module_path] = names
    return result


@icontract.require(lambda module_name: isinstance(module_name, str), "module_name must be a string")
@icontract.require(lambda reference: isinstance(reference, str), "reference must be a string")
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a bool")
def _module_name_matches_reference(module_name: str, reference: str) -> bool:
    """Check whether a module name matches a reference on full segment boundaries."""
    normalized_module = _normalize_module_name(module_name)
    normalized_reference = _normalize_module_name(reference)
    if not normalized_module or not normalized_reference:
        return False

    module_parts = tuple(part for part in normalized_module.split(".") if part)
    reference_parts = tuple(part for part in normalized_reference.split(".") if part)
    if not module_parts or not reference_parts or len(reference_parts) > len(module_parts):
        return False

    return module_parts[-len(reference_parts):] == reference_parts


@icontract.require(
    lambda call_target: isinstance(call_target, str),
    "call_target must be a string",
)
@icontract.require(
    lambda caller_module: isinstance(caller_module, ModuleInfo),
    "caller_module must be a ModuleInfo",
)
@icontract.require(
    lambda import_map: isinstance(import_map, dict),
    "import_map must be a dictionary",
)
@icontract.require(
    lambda module_functions: isinstance(module_functions, dict),
    "module_functions must be a dictionary",
)
@icontract.ensure(
    lambda result: result is None or isinstance(result, tuple),
    "result must be a tuple or None",
)
def _resolve_call_target(
    call_target: str,
    caller_module: ModuleInfo,
    import_map: dict[str, dict[str, tuple[str, str | None]]],
    module_functions: dict[str, dict[str, FunctionInfo]],
) -> tuple[str, FunctionInfo] | None:
    """Try to resolve a call target to a specific module and FunctionInfo.

    Args:
        call_target: The call target name string.
        caller_module: The module containing the call.
        import_map: Map of imported names per module.
        module_functions: Map of functions per module.

    Returns:
        Tuple of (module_path, FunctionInfo) or None if unresolvable.
    """
    # Case 1: simple name — check if it was imported from another module
    if "." not in call_target:
        imports = import_map.get(caller_module.module_path, {})
        if call_target in imports:
            source_mod, orig_name = imports[call_target]
            target_name = orig_name if orig_name is not None else call_target
            # Loop invariant: checked module_functions entries[0..i]
            for mod_path, funcs in module_functions.items():
                if _module_name_matches_reference(mod_path, source_mod):
                    if target_name in funcs:
                        return (mod_path, funcs[target_name])
        return None

    # Case 2: dotted name — e.g., "module.function"
    parts = call_target.rsplit(".", 1)
    if len(parts) == 2:
        module_part, func_name = parts
        imports = import_map.get(caller_module.module_path, {})
        if module_part in imports:
            source_mod, orig_name = imports[module_part]
            if orig_name is not None:
                normalized_part = _normalize_module_name(f"{source_mod}.{orig_name}")
            else:
                normalized_part = _normalize_module_name(source_mod)
        else:
            normalized_part = _normalize_module_name(module_part)
        # Loop invariant: checked module_functions entries[0..i]
        for mod_path, funcs in module_functions.items():
            if _module_name_matches_reference(mod_path, normalized_part) and func_name in funcs:
                return (mod_path, funcs[func_name])

    return None


# ---------------------------------------------------------------------------
# Data flow verification
# ---------------------------------------------------------------------------


@icontract.require(
    lambda modules: isinstance(modules, list),
    "modules must be a list",
)
@icontract.ensure(
    lambda result: isinstance(result, list),
    "result must be a list",
)
def check_data_flow(
    modules: list[ModuleInfo],
    config: SerenecodeConfig,
) -> list[FunctionResult]:
    """Verify that data flowing across module boundaries maintains contracts.

    For each cross-module function call between public functions, checks:
    1. The callee's parameters have type annotations.
    2. If the callee has preconditions, the caller has a return type annotation.

    Args:
        modules: List of parsed module information.
        config: Active Serenecode configuration.

    Returns:
        List of FunctionResult for data flow violations.
    """
    results: list[FunctionResult] = []
    module_functions = _build_module_function_map(modules)
    import_map = _build_import_resolution_map(modules)

    # Loop invariant: results contains data flow findings for modules[0..i]
    for mod in modules:
        if is_exempt_module(mod.module_path, config):
            continue
        results.extend(_check_data_flow_for_module(
            mod, modules, import_map, module_functions,
        ))

    return results


def _check_data_flow_for_module(
    mod: ModuleInfo,
    all_modules: list[ModuleInfo],
    import_map: dict[str, dict[str, tuple[str, str | None]]],
    module_functions: dict[str, dict[str, FunctionInfo]],
) -> list[FunctionResult]:
    """Check data flow for one module's cross-module calls."""
    results: list[FunctionResult] = []
    reported_untyped: set[str] = set()
    reported_return: set[str] = set()

    # Loop invariant: results contains findings for function_infos[0..j]
    for func_info in mod.function_infos:
        if not func_info.is_public:
            continue
        for call_target in func_info.calls:
            resolved = _resolve_call_target(call_target, mod, import_map, module_functions)
            if resolved is None:
                continue
            callee_module, callee_func = resolved
            if callee_module == mod.module_path or not callee_func.is_public:
                continue

            untyped = [p for p in callee_func.parameters if p.annotation is None]
            untyped_key = f"{callee_module}.{callee_func.name}"
            if untyped and untyped_key not in reported_untyped:
                reported_untyped.add(untyped_key)
                param_names = ", ".join(p.name for p in untyped)
                callee_file = _find_file_for_module(callee_module, all_modules)
                results.append(FunctionResult(
                    function=callee_func.name, file=callee_file, line=callee_func.line,
                    level_requested=6, level_achieved=5, status=CheckStatus.FAILED,
                    details=(Detail(
                        level=VerificationLevel.COMPOSITIONAL, tool="compositional",
                        finding_type="violation",
                        message=f"Function '{callee_func.name}' receives cross-module data but parameters [{param_names}] lack type annotations",
                        suggestion="Add type annotations to all parameters that receive cross-module data",
                    ),),
                ))

            return_key = f"{func_info.name}->{callee_module}"
            if callee_func.has_require and func_info.return_annotation is None and return_key not in reported_return:
                reported_return.add(return_key)
                results.append(FunctionResult(
                    function=func_info.name, file=mod.file_path, line=func_info.line,
                    level_requested=6, level_achieved=5, status=CheckStatus.FAILED,
                    details=(Detail(
                        level=VerificationLevel.COMPOSITIONAL, tool="compositional",
                        finding_type="violation",
                        message=f"Function '{func_info.name}' provides data to '{callee_func.name}' (which has preconditions) but lacks a return type annotation",
                        suggestion="Add return type annotation to document the data contract",
                    ),),
                ))

    return results


@icontract.require(
    lambda module_path: is_non_empty_string(module_path),
    "module_path must be a non-empty string",
)
@icontract.require(
    lambda modules: isinstance(modules, list),
    "modules must be a list",
)
@icontract.ensure(
    lambda result: isinstance(result, str),
    "result must be a string",
)
def _find_file_for_module(
    module_path: str,
    modules: list[ModuleInfo],
) -> str:
    """Find the file path for a given module path.

    Args:
        module_path: The module path to look up.
        modules: List of all parsed modules.

    Returns:
        The file path string, or '<unknown>' if not found.
    """
    # Loop invariant: checked modules[0..i] for matching module_path
    for mod in modules:
        if mod.module_path == module_path:
            return mod.file_path
    return "<unknown>"


# ---------------------------------------------------------------------------
# System invariants
# ---------------------------------------------------------------------------


@icontract.require(
    lambda modules: isinstance(modules, list),
    "modules must be a list",
)
@icontract.ensure(
    lambda result: isinstance(result, list),
    "result must be a list",
)
def check_system_invariants(
    modules: list[ModuleInfo],
    config: SerenecodeConfig,
) -> list[FunctionResult]:
    """Verify system-wide architectural invariants.

    Checks:
    1. All public classes in ports/ are Protocols.
    2. Every Protocol has at least one adapter implementation.
    3. Core modules do not import forbidden I/O libraries.

    Args:
        modules: List of parsed module information.
        config: Active Serenecode configuration.

    Returns:
        List of FunctionResult for system invariant violations.
    """
    results: list[FunctionResult] = []
    from serenecode.checker.compositional import _class_likely_implements

    all_protocols, port_class_findings = _collect_port_protocols(modules)
    results.extend(port_class_findings)

    adapter_classes = _collect_adapter_classes(modules)
    results.extend(_check_protocol_implementations(
        all_protocols, adapter_classes, _class_likely_implements,
    ))
    results.extend(_check_forbidden_core_imports(modules, config))

    return results


def _collect_port_protocols(
    modules: list[ModuleInfo],
) -> tuple[dict[str, tuple[str, ProtocolInfo]], list[FunctionResult]]:
    """Collect protocols from ports and flag non-protocol classes."""
    all_protocols: dict[str, tuple[str, ProtocolInfo]] = {}
    findings: list[FunctionResult] = []
    # Loop invariant: all_protocols and findings updated for modules[0..i]
    for mod in modules:
        if not _module_path_has_segment(mod.module_path, "ports"):
            continue
        for proto in mod.protocols:
            all_protocols[proto.name] = (mod.file_path, proto)
        for cls in mod.classes:
            if cls.is_protocol or cls.name.startswith("_"):
                continue
            public_methods = [m for m in cls.methods if not m.startswith("_")]
            if public_methods:
                findings.append(FunctionResult(
                    function=cls.name, file=mod.file_path, line=cls.line,
                    level_requested=6, level_achieved=5, status=CheckStatus.FAILED,
                    details=(Detail(
                        level=VerificationLevel.COMPOSITIONAL, tool="compositional",
                        finding_type="violation",
                        message=(
                            f"Class '{cls.name}' in ports/ is not a Protocol. "
                            f"All classes in ports/ must be Protocol definitions or data carriers."
                        ),
                        suggestion="Add Protocol as a base class, or move to adapters/",
                    ),),
                ))
    return all_protocols, findings


def _collect_adapter_classes(modules: list[ModuleInfo]) -> list[ClassInfo]:
    """Collect all classes from adapter modules."""
    adapter_classes: list[ClassInfo] = []
    # Loop invariant: adapter_classes contains classes from adapter modules[0..i]
    for mod in modules:
        if _module_path_has_segment(mod.module_path, "adapters"):
            adapter_classes.extend(mod.classes)
    return adapter_classes


def _check_protocol_implementations(
    all_protocols: dict[str, tuple[str, ProtocolInfo]],
    adapter_classes: list[ClassInfo],
    class_likely_implements: object,
) -> list[FunctionResult]:
    """Check every Protocol has at least one likely implementation."""
    results: list[FunctionResult] = []
    # Loop invariant: results updated for all_protocols entries[0..i]
    for proto_name, (proto_file, proto_info) in all_protocols.items():
        has_impl = any(class_likely_implements(cls, proto_info) for cls in adapter_classes)
        if not has_impl:
            results.append(FunctionResult(
                function=proto_name, file=proto_file, line=proto_info.line,
                level_requested=6, level_achieved=6, status=CheckStatus.PASSED,
                details=(Detail(
                    level=VerificationLevel.COMPOSITIONAL, tool="compositional",
                    finding_type="info",
                    message=(
                        f"Protocol '{proto_name}' has no detected adapter implementation. "
                        f"This may indicate a missing adapter or a detection limitation."
                    ),
                ),),
            ))
    return results


def _check_forbidden_core_imports(
    modules: list[ModuleInfo],
    config: SerenecodeConfig,
) -> list[FunctionResult]:
    """Check forbidden imports in core modules."""
    forbidden = set(config.architecture_rules.forbidden_imports_in_core)
    results: list[FunctionResult] = []
    # Loop invariant: results updated for modules[0..i] regarding forbidden imports
    for mod in modules:
        if not is_core_module(mod.module_path, config):
            continue
        for imp in mod.imports:
            if imp.split(".")[0] in forbidden:
                results.append(FunctionResult(
                    function="<module>", file=mod.file_path, line=1,
                    level_requested=6, level_achieved=5, status=CheckStatus.FAILED,
                    details=(Detail(
                        level=VerificationLevel.COMPOSITIONAL, tool="compositional",
                        finding_type="violation",
                        message=f"Core module '{mod.module_path}' imports forbidden I/O library '{imp}'",
                        suggestion="Use dependency injection via a Protocol in ports/",
                    ),),
                ))
        for from_mod, _ in mod.from_imports:
            if from_mod.split(".")[0] in forbidden:
                results.append(FunctionResult(
                    function="<module>", file=mod.file_path, line=1,
                    level_requested=6, level_achieved=5, status=CheckStatus.FAILED,
                    details=(Detail(
                        level=VerificationLevel.COMPOSITIONAL, tool="compositional",
                        finding_type="violation",
                        message=f"Core module '{mod.module_path}' imports from forbidden I/O library '{from_mod}'",
                        suggestion="Use dependency injection via a Protocol in ports/",
                    ),),
                ))
    return results


@icontract.require(
    lambda modules: isinstance(modules, list),
    "modules must be a list",
)
@icontract.require(
    lambda sources: isinstance(sources, (list, tuple)),
    "sources must be a list or tuple",
)
@icontract.require(
    lambda spec_content: spec_content is None or isinstance(spec_content, str),
    "spec_content must be None or a string",
)
@icontract.ensure(
    lambda result: isinstance(result, list),
    "result must be a list",
)
def check_declared_integrations(
    modules: list[ModuleInfo],
    sources: list[tuple[str, str, str]] | tuple[tuple[str, str, str], ...],
    spec_content: str | None,
) -> list[FunctionResult]:
    """Verify that declared INT items are semantically satisfied.

    This supplements Level 1 traceability with a deeper Level 6 check:
    tagged integration points must correspond to actual interactions or
    interface relationships in the implementation.
    """
    if spec_content is None or not spec_content.strip():
        return []

    from serenecode.checker.spec_traceability import (
        extract_implementations,
        extract_integration_points,
    )
    from serenecode.checker.compositional import _check_signature_compatibility

    integration_points = extract_integration_points(spec_content)
    if not integration_points:
        return []

    source_map = {file_path: source for source, file_path, _module_path in sources}
    implementation_refs = _collect_int_refs(sources, extract_implementations)
    class_map = {
        (mod.file_path, cls.name): cls for mod in modules for cls in mod.classes
    }
    protocol_map = {
        proto.name: proto for mod in modules for proto in mod.protocols
    }

    results: list[FunctionResult] = []
    # Loop invariant: results contains semantic findings for integration_points[0..i]
    for point in integration_points:
        finding = _check_single_integration_point(
            point, implementation_refs, source_map,
            class_map, protocol_map, _check_signature_compatibility,
        )
        if finding is not None:
            results.append(finding)

    return results


def _collect_int_refs(
    sources: list[tuple[str, str, str]] | tuple[tuple[str, str, str], ...],
    extract_implementations: object,
) -> dict[str, list[tuple[str, str, int]]]:
    """Collect INT implementation references from all sources."""
    implementation_refs: dict[str, list[tuple[str, str, int]]] = {}
    # Loop invariant: implementation_refs contains INT references from sources[0..i]
    for source, file_path, _module_path in sources:
        for symbol_name, identifier, line_no in extract_implementations(source):
            if identifier.startswith("INT-"):
                implementation_refs.setdefault(identifier, []).append((
                    file_path, symbol_name, line_no,
                ))
    return implementation_refs


def _check_single_integration_point(
    point: object,
    implementation_refs: dict[str, list[tuple[str, str, int]]],
    source_map: dict[str, str],
    class_map: dict[tuple[str, str], ClassInfo],
    protocol_map: dict[str, ProtocolInfo],
    check_sig_compat: object,
) -> FunctionResult | None:
    """Check one integration point. Returns None if satisfied."""
    refs = implementation_refs.get(point.identifier, [])
    if not refs:
        return None

    first_ref = refs[0]
    if point.kind == "call":
        if _call_integration_is_satisfied(point, refs, source_map):
            return None
        return FunctionResult(
            function=point.identifier, file=first_ref[0], line=first_ref[2],
            level_requested=6, level_achieved=5, status=CheckStatus.FAILED,
            details=(Detail(
                level=VerificationLevel.COMPOSITIONAL, tool="compositional",
                finding_type="integration_semantics",
                message=(
                    f"{point.identifier} declares a call integration from "
                    f"'{point.source}' to '{point.target}', but no matching "
                    "call, constructor call, or isinstance check for those "
                    "targets was detected in the tagged implementation body."
                ),
                suggestion=(
                    f"Ensure the implementation for {point.source} references "
                    f"{point.target} as required. Comma-separated targets must all be present (AND). "
                    f"Keep 'Implements: {point.identifier}' on the responsible symbol."
                ),
            ),),
        )

    issue = _implements_integration_issue(
        point, refs, class_map, protocol_map, check_sig_compat,
    )
    if issue is None:
        return None
    return FunctionResult(
        function=point.identifier, file=first_ref[0], line=first_ref[2],
        level_requested=6, level_achieved=5, status=CheckStatus.FAILED,
        details=(Detail(
            level=VerificationLevel.COMPOSITIONAL, tool="compositional",
            finding_type="integration_semantics",
            message=(
                f"{point.identifier} declares that '{point.source}' implements "
                f"'{point.target}', but the tagged implementation does not "
                f"satisfy that relationship: {issue}"
            ),
            suggestion=(
                f"Make the implementing class inherit from or remain substitutable "
                f"for {point.target}, and tag the class docstring with "
                f"'Implements: {point.identifier}'."
            ),
        ),),
    )


@icontract.require(lambda point: point is not None, "point must be provided")
@icontract.require(lambda refs: isinstance(refs, list), "refs must be a list")
@icontract.require(lambda source_map: isinstance(source_map, dict), "source_map must be a dict")
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a boolean")
def _call_integration_is_satisfied(
    point: object,
    refs: list[tuple[str, str, int]],
    source_map: dict[str, str],
) -> bool:
    """Return True if any tagged implementation satisfies the declared integration."""
    source_names = {segment for segment in str(getattr(point, "source")).split(".") if segment}
    target_name = str(getattr(point, "target"))
    targets = _parse_integration_target_list(target_name)
    if not targets:
        targets = (target_name.strip(),) if target_name.strip() else tuple()
    if not targets:
        return False
    candidate_refs = [ref for ref in refs if ref[1] in source_names]
    refs_to_check = candidate_refs if candidate_refs else refs

    # Loop invariant: no checked ref in refs_to_check[0..i] satisfied the call integration
    for file_path, symbol_name, line_no in refs_to_check:
        source = source_map.get(file_path)
        if source is None:
            continue
        node = _find_symbol_node(source, symbol_name, line_no)
        if node is None:
            continue
        if _node_satisfies_call_integration(node, targets):
            return True
    return False


@icontract.require(lambda point: point is not None, "point must be provided")
@icontract.require(lambda refs: isinstance(refs, list), "refs must be a list")
@icontract.require(lambda class_map: isinstance(class_map, dict), "class_map must be a dict")
@icontract.require(lambda protocol_map: isinstance(protocol_map, dict), "protocol_map must be a dict")
@icontract.ensure(lambda result: result is None or isinstance(result, str), "result must be str or None")
def _implements_integration_issue(
    point: object,
    refs: list[tuple[str, str, int]],
    class_map: dict[tuple[str, str], ClassInfo],
    protocol_map: dict[str, ProtocolInfo],
    check_signature_compatibility: object | None = None,
) -> str | None:
    """Return None if an implements integration is satisfied, else a short issue."""
    source_names = {segment for segment in str(getattr(point, "source")).split(".") if segment}
    target_name = str(getattr(point, "target")).split(".")[-1]
    candidate_refs = [ref for ref in refs if ref[1] in source_names]
    refs_to_check = candidate_refs if candidate_refs else refs
    protocol = protocol_map.get(target_name)
    first_issue: str | None = None

    # Loop invariant: first_issue describes the first mismatch seen in refs_to_check[0..i]
    for file_path, symbol_name, _line_no in refs_to_check:
        class_info = class_map.get((file_path, symbol_name))
        if class_info is None:
            if first_issue is None:
                first_issue = "the tag is not attached to an implementing class"
            continue

        explicit_inheritance = any(
            base == target_name or base.endswith(f".{target_name}")
            for base in class_info.bases
        )
        if protocol is None:
            if explicit_inheritance:
                return None
            if first_issue is None:
                first_issue = f"class '{class_info.name}' does not inherit from '{target_name}'"
            continue

        signature_issue = _protocol_signature_issue(
            class_info, protocol, check_signature_compatibility,
        )
        if signature_issue is None:
            return None
        if first_issue is None:
            first_issue = signature_issue

    return first_issue or f"no class matching source '{getattr(point, 'source')}' was found"


@icontract.require(lambda class_info: class_info is not None, "class_info must be provided")
@icontract.require(lambda protocol: protocol is not None, "protocol must be provided")
@icontract.ensure(lambda result: result is None or isinstance(result, str), "result must be str or None")
def _protocol_signature_issue(
    class_info: ClassInfo,
    protocol: ProtocolInfo,
    check_signature_compatibility: object | None = None,
) -> str | None:
    """Return None if a class structurally matches a protocol, else a short issue."""
    if check_signature_compatibility is None:
        from serenecode.checker.compositional import _check_signature_compatibility
        check_signature_compatibility = _check_signature_compatibility

    signature_map = {signature.name: signature for signature in class_info.method_signatures}

    # Loop invariant: no mismatch was found in protocol.methods[0..i]
    for method in protocol.methods:
        class_signature = signature_map.get(method.name)
        if class_signature is None:
            return f"class '{class_info.name}' is missing method '{method.name}'"
        issues = check_signature_compatibility(class_signature, method)
        if issues:
            return issues[0]

    return None


@icontract.require(lambda source: isinstance(source, str), "source must be a string")
@icontract.require(lambda symbol_name: isinstance(symbol_name, str) and len(symbol_name) > 0, "symbol_name must be non-empty")
@icontract.require(lambda line_no: isinstance(line_no, int) and line_no >= 1, "line_no must be >= 1")
@icontract.ensure(lambda result: result is None or isinstance(result, ast.AST), "result must be None or an AST node")
def _find_symbol_node(
    source: str,
    symbol_name: str,
    line_no: int,
) -> ast.AST | None:
    """Find a class/function/method AST node by name and line number."""
    # silent-except: semantic integration is best-effort over user source; parse failures are reported elsewhere
    try:
        tree = ast.parse(source)
    except (SyntaxError, TypeError, ValueError):
        return None

    # Loop invariant: no matching node has been found in the visited subtree so far
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if node.name == symbol_name and node.lineno == line_no:
            return node
    return None


@icontract.require(lambda raw: isinstance(raw, str), "raw must be a string")
@icontract.ensure(lambda result: isinstance(result, tuple), "result must be a tuple")
def _parse_integration_target_list(raw: str) -> tuple[str, ...]:
    """Parse comma-separated INT Target values; every entry must be satisfied (AND)."""
    parts = [part.strip() for part in raw.split(",")]
    return tuple(part for part in parts if part)


@icontract.require(lambda type_expr: isinstance(type_expr, ast.AST), "type_expr must be an AST node")
@icontract.require(lambda target_name: isinstance(target_name, str) and len(target_name) > 0, "target_name must be non-empty")
@icontract.require(lambda simple_target: isinstance(simple_target, str) and len(simple_target) > 0, "simple_target must be non-empty")
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a boolean")
def _type_expr_matches_integration_target(
    type_expr: ast.expr,
    target_name: str,
    simple_target: str,
) -> bool:
    """Return True if a runtime type expression refers to the integration target."""
    # Variant: tuple nesting depth of type_expr decreases
    if isinstance(type_expr, ast.Tuple):
        return any(
            _type_expr_matches_integration_target(elt, target_name, simple_target)
            for elt in type_expr.elts
        )
    dotted = _get_call_target_name(type_expr)
    if not dotted:
        return False
    return (
        dotted == target_name
        or dotted == simple_target
        or dotted.endswith(f".{simple_target}")
    )


@icontract.require(lambda node: isinstance(node, ast.AST), "node must be an AST node")
@icontract.require(lambda targets: isinstance(targets, tuple), "targets must be a tuple")
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a boolean")
def _node_satisfies_call_integration(node: ast.AST, targets: tuple[str, ...]) -> bool:
    """Check whether a body satisfies all declared targets (calls and/or isinstance)."""
    if not targets:
        return False
    return all(_node_satisfies_single_integration_target(node, t) for t in targets)


@icontract.require(lambda node: isinstance(node, ast.AST), "node must be an AST node")
@icontract.require(lambda target_name: isinstance(target_name, str) and len(target_name) > 0, "target_name must be non-empty")
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a boolean")
def _node_satisfies_single_integration_target(node: ast.AST, target_name: str) -> bool:
    """Check whether a subtree shows a call to the target or isinstance(..., target)."""
    simple_target = target_name.split(".")[-1]

    # Loop invariant: no evidence for target_name found in node's subtree yet
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        if isinstance(child.func, ast.Name) and child.func.id == "isinstance":
            if len(child.args) >= 2 and _type_expr_matches_integration_target(
                child.args[1],
                target_name,
                simple_target,
            ):
                return True
            continue
        call_target = _get_call_target_name(child.func)
        if (
            call_target == target_name
            or call_target == simple_target
            or call_target.endswith(f".{simple_target}")
        ):
            return True
    return False
