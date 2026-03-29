"""Compositional verification checker for Serenecode (Level 5).

This module implements Level 5 verification: module-level analysis that
checks component interactions, dependency direction, interface compliance,
and system-level properties across the entire codebase.

This is a core module — no I/O operations are permitted. Source code
is received as structured SourceFile objects with pre-read content.
"""

from __future__ import annotations

import ast
import time
from dataclasses import dataclass

import icontract

from serenecode.checker.structural import (
    IcontractNames,
    get_decorator_name,
    has_decorator,
    resolve_icontract_aliases,
)
from serenecode.config import SerenecodeConfig, is_core_module, is_exempt_module
from serenecode.contracts.predicates import is_non_empty_string, is_valid_file_path_string
from serenecode.models import (
    CheckResult,
    CheckStatus,
    Detail,
    FunctionResult,
    VerificationLevel,
    make_check_result,
)


# ---------------------------------------------------------------------------
# Data structures for compositional analysis
# ---------------------------------------------------------------------------


@icontract.invariant(
    lambda self: len(self.name) > 0,
    "Method name must be non-empty",
)
@icontract.invariant(
    lambda self: 0 <= self.required_parameters <= len(self.parameters),
    "required parameter count must fit within the signature",
)
@dataclass(frozen=True)
class MethodSignature:
    """A method signature from a Protocol or class."""

    name: str
    parameters: tuple[str, ...]  # parameter names (excluding self)
    has_return_annotation: bool
    required_parameters: int = -1
    return_annotation: str | None = None

    @icontract.ensure(lambda result: result is None, "post-init returns None")
    def __post_init__(self) -> None:
        """Fill in backwards-compatible defaults for optional metadata."""
        if self.required_parameters < 0:
            object.__setattr__(self, "required_parameters", len(self.parameters))


@icontract.invariant(
    lambda self: len(self.name) > 0,
    "Parameter name must be non-empty",
)
@dataclass(frozen=True)
class ParameterInfo:
    """A single function parameter with its type annotation."""

    name: str
    annotation: str | None  # type annotation as string, or None if untyped


@icontract.invariant(
    lambda self: len(self.name) > 0 and self.line >= 1,
    "Function must have a non-empty name and valid line number",
)
@dataclass(frozen=True)
class FunctionInfo:
    """Full information about a function definition."""

    name: str
    line: int
    is_public: bool
    parameters: tuple[ParameterInfo, ...]
    return_annotation: str | None
    has_require: bool
    has_ensure: bool
    calls: tuple[str, ...]  # call target names extracted from body


@icontract.invariant(
    lambda self: len(self.name) > 0 and self.line >= 1,
    "Class must have a non-empty name and valid line number",
)
@dataclass(frozen=True)
class ClassInfo:
    """Information about a class definition."""

    name: str
    line: int
    bases: tuple[str, ...]
    methods: tuple[str, ...]
    is_protocol: bool
    method_signatures: tuple[MethodSignature, ...] = ()
    has_invariant: bool = False
    has_no_invariant_comment: bool = False


@icontract.invariant(
    lambda self: len(self.name) > 0 and self.line >= 1,
    "Protocol must have a non-empty name and valid line number",
)
@dataclass(frozen=True)
class ProtocolInfo:
    """Information about a Protocol definition."""

    name: str
    line: int
    methods: tuple[MethodSignature, ...]


@icontract.invariant(
    lambda self: len(self.file_path) > 0 and len(self.module_path) > 0,
    "Module must have non-empty file and module paths",
)
@dataclass(frozen=True)
class ModuleInfo:
    """Parsed information about a single module for compositional analysis."""

    file_path: str
    module_path: str
    imports: tuple[str, ...]
    from_imports: tuple[tuple[str, str], ...]  # (resolved_module, imported_name) pairs
    classes: tuple[ClassInfo, ...]
    functions: tuple[str, ...]
    protocols: tuple[ProtocolInfo, ...]
    function_infos: tuple[FunctionInfo, ...] = ()
    import_bindings: tuple[tuple[str, str, str | None], ...] = ()
    parse_error: str | None = None


# ---------------------------------------------------------------------------
# Module parsing
# ---------------------------------------------------------------------------


@icontract.require(
    lambda source: isinstance(source, str),
    "source must be a string",
)
@icontract.require(
    lambda file_path: is_non_empty_string(file_path),
    "file_path must be a non-empty string",
)
@icontract.require(
    lambda file_path: is_valid_file_path_string(file_path),
    "file_path must be a valid path string",
)
@icontract.require(
    lambda module_path: is_non_empty_string(module_path),
    "module_path must be a non-empty string",
)
@icontract.require(
    lambda module_path: is_valid_file_path_string(module_path),
    "module_path must be a valid module path string",
)
@icontract.ensure(
    lambda result: isinstance(result, ModuleInfo),
    "result must be a ModuleInfo",
)
def parse_module_info(
    source: str,
    file_path: str,
    module_path: str,
) -> ModuleInfo:
    """Parse a Python source file into a ModuleInfo for compositional analysis.

    Args:
        source: Python source code.
        file_path: Path to the file.
        module_path: Derived module path for architecture checks.

    Returns:
        A ModuleInfo containing structural information about the module.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, TypeError, ValueError) as parse_exc:
        return ModuleInfo(
            file_path=file_path,
            module_path=module_path,
            imports=(),
            from_imports=(),
            classes=(),
            functions=(),
            protocols=(),
            parse_error=str(parse_exc),
        )

    aliases = resolve_icontract_aliases(tree)

    imports: list[str] = []
    from_imports: list[tuple[str, str]] = []
    import_bindings: list[tuple[str, str, str | None]] = []
    classes: list[ClassInfo] = []
    functions: list[str] = []
    function_infos: list[FunctionInfo] = []
    protocols: list[ProtocolInfo] = []

    # Loop invariant: collected info for all top-level nodes processed so far
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            # Loop invariant: imports list updated for aliases[0..i]
            for alias in node.names:
                imports.append(alias.name)
                bound_name = alias.asname if alias.asname else alias.name.split(".")[0]
                import_bindings.append((bound_name, alias.name, None))
        elif isinstance(node, ast.ImportFrom):
            resolved_module = _resolve_from_import_module(node, module_path)
            if resolved_module:
                # Loop invariant: from_imports updated for names[0..i]
                for alias in node.names:
                    from_imports.append((resolved_module, alias.name))
                    if alias.name != "*":
                        bound_name = alias.asname if alias.asname else alias.name
                        import_bindings.append((bound_name, resolved_module, alias.name))
        elif isinstance(node, ast.ClassDef):
            class_info = _parse_class(node, aliases, source)
            classes.append(class_info)
            if class_info.is_protocol:
                protocol_info = _parse_protocol(node)
                protocols.append(protocol_info)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            is_public = _is_public_function_name(node.name)
            if is_public:
                functions.append(node.name)
            func_info = _parse_function_info(node, aliases)
            function_infos.append(func_info)

    return ModuleInfo(
        file_path=file_path,
        module_path=module_path,
        imports=tuple(imports),
        from_imports=tuple(from_imports),
        classes=tuple(classes),
        functions=tuple(functions),
        protocols=tuple(protocols),
        function_infos=tuple(function_infos),
        import_bindings=tuple(import_bindings),
    )


@icontract.require(
    lambda node: isinstance(node, ast.ImportFrom),
    "node must be an ImportFrom node",
)
@icontract.require(
    lambda module_path: isinstance(module_path, str),
    "module_path must be a string",
)
@icontract.ensure(
    lambda result: result is None or isinstance(result, str),
    "result must be a string or None",
)
def _resolve_from_import_module(
    node: ast.ImportFrom,
    module_path: str,
) -> str | None:
    """Resolve an ImportFrom node to a module name relative to module_path."""
    if node.level == 0:
        return node.module

    current_package = _module_package_name(module_path)
    package_parts = [part for part in current_package.split(".") if part]
    ascend = node.level - 1

    if ascend > len(package_parts):
        base_parts: list[str] = []
    else:
        base_parts = package_parts[:len(package_parts) - ascend]

    resolved_parts = list(base_parts)
    if node.module:
        resolved_parts.extend(part for part in node.module.split(".") if part)

    if not resolved_parts:
        return None

    return ".".join(resolved_parts)


@icontract.require(
    lambda module_path: isinstance(module_path, str),
    "module_path must be a string",
)
@icontract.ensure(
    lambda result: isinstance(result, str),
    "result must be a string",
)
def _module_package_name(module_path: str) -> str:
    """Get the dotted package path for a module path."""
    normalized = _normalize_module_name(module_path)

    if normalized.endswith(".__init__"):
        return normalized[:-9]

    if "." in normalized:
        return normalized.rsplit(".", 1)[0]

    return ""


@icontract.require(
    lambda name: is_non_empty_string(name),
    "name must be a non-empty string",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _is_public_function_name(name: str) -> bool:
    """Check whether a function should count as public API."""
    if name.startswith("_") and not name.startswith("__"):
        return False
    if name.startswith("__") and name.endswith("__") and name != "__init__":
        return False
    return True


@icontract.require(
    lambda func_info: isinstance(func_info, FunctionInfo),
    "func_info must be a FunctionInfo",
)
@icontract.require(
    lambda config: isinstance(config, SerenecodeConfig),
    "config must be a SerenecodeConfig",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _should_check_function_contracts(
    func_info: FunctionInfo,
    config: SerenecodeConfig,
) -> bool:
    """Check whether contract completeness applies to a function."""
    if config.contract_requirements.require_on_private:
        return not (
            func_info.name.startswith("__")
            and func_info.name.endswith("__")
            and func_info.name != "__init__"
        )
    return _is_public_function_name(func_info.name)


@icontract.require(
    lambda cls: isinstance(cls, ClassInfo),
    "cls must be a ClassInfo",
)
@icontract.require(
    lambda config: isinstance(config, SerenecodeConfig),
    "config must be a SerenecodeConfig",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _should_check_class_invariants(
    cls: ClassInfo,
    config: SerenecodeConfig,
) -> bool:
    """Check whether invariant completeness applies to a class."""
    if config.contract_requirements.require_on_private:
        return True
    return not cls.name.startswith("_")


@icontract.require(
    lambda node: isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)),
    "node must be a function definition",
)
@icontract.require(
    lambda aliases: isinstance(aliases, IcontractNames),
    "aliases must be IcontractNames",
)
@icontract.ensure(
    lambda result: isinstance(result, FunctionInfo),
    "result must be a FunctionInfo",
)
def _parse_function_info(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    aliases: IcontractNames,
) -> FunctionInfo:
    """Parse a function definition into a FunctionInfo.

    Args:
        node: An AST FunctionDef or AsyncFunctionDef node.
        aliases: Resolved icontract import names for decorator detection.

    Returns:
        A FunctionInfo with function metadata including contracts and calls.
    """
    parameters = _parse_parameters(node)
    return_ann = ast.unparse(node.returns) if node.returns else None
    has_req = has_decorator(node, aliases.require_names)
    has_ens = has_decorator(node, aliases.ensure_names)
    calls = _extract_calls(node)

    return FunctionInfo(
        name=node.name,
        line=node.lineno,
        is_public=_is_public_function_name(node.name),
        parameters=parameters,
        return_annotation=return_ann,
        has_require=has_req,
        has_ensure=has_ens,
        calls=calls,
    )


@icontract.require(
    lambda node: isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)),
    "node must be a function definition",
)
@icontract.ensure(
    lambda result: isinstance(result, tuple),
    "result must be a tuple",
)
def _parse_parameters(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[ParameterInfo, ...]:
    """Extract parameter names and type annotations from a function.

    Args:
        node: An AST FunctionDef or AsyncFunctionDef node.

    Returns:
        Tuple of ParameterInfo for non-self/cls parameters.
    """
    params: list[ParameterInfo] = []
    signature_params = list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs)
    if node.args.vararg is not None:
        signature_params.append(node.args.vararg)
    if node.args.kwarg is not None:
        signature_params.append(node.args.kwarg)

    # Loop invariant: params contains ParameterInfo for signature_params[0..i] excluding self/cls
    for arg in signature_params:
        if arg.arg in ("self", "cls"):
            continue
        annotation = ast.unparse(arg.annotation) if arg.annotation else None
        params.append(ParameterInfo(name=arg.arg, annotation=annotation))
    return tuple(params)


@icontract.require(
    lambda node: isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)),
    "node must be a function definition",
)
@icontract.ensure(
    lambda result: isinstance(result, tuple),
    "result must be a tuple",
)
def _extract_calls(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[str, ...]:
    """Extract all function call target names from a function body.

    Args:
        node: An AST FunctionDef or AsyncFunctionDef node.

    Returns:
        Tuple of call target name strings.
    """
    calls: list[str] = []
    # Loop invariant: calls contains target names for all ast.Call nodes in body[0..i]
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            name = _get_call_target_name(child.func)
            if name:
                calls.append(name)
    return tuple(calls)


@icontract.require(
    lambda node: isinstance(node, ast.AST),
    "node must be an AST node",
)
@icontract.ensure(
    lambda result: isinstance(result, str),
    "result must be a string",
)
def _get_call_target_name(node: ast.expr) -> str:
    """Resolve an AST call target to a dotted name string.

    Args:
        node: The func attribute of an ast.Call node.

    Returns:
        The resolved name string, or empty string if unresolvable.
    """
    # Variant: depth of nesting decreases
    if isinstance(node, ast.Name):
        return node.id
    elif isinstance(node, ast.Attribute):
        value_name = _get_call_target_name(node.value)
        if value_name:
            return f"{value_name}.{node.attr}"
        return node.attr
    return ""


@icontract.require(lambda node: isinstance(node, ast.ClassDef), "node must be a class definition")
@icontract.require(
    lambda aliases: isinstance(aliases, IcontractNames),
    "aliases must be IcontractNames",
)
@icontract.ensure(
    lambda result: isinstance(result, ClassInfo),
    "result must be a ClassInfo",
)
def _parse_class(node: ast.ClassDef, aliases: IcontractNames, source: str = "") -> ClassInfo:
    """Parse a class definition into a ClassInfo.

    Args:
        node: An AST ClassDef node.
        aliases: Resolved icontract import names for invariant detection.
        source: Original source code for comment checking.

    Returns:
        A ClassInfo with class structural information.
    """
    bases: list[str] = []
    # Loop invariant: bases contains names for node.bases[0..i]
    for base in node.bases:
        if isinstance(base, ast.Name):
            bases.append(base.id)
        elif isinstance(base, ast.Attribute):
            bases.append(f"{_get_name(base.value)}.{base.attr}")

    methods: list[str] = []
    method_sigs: list[MethodSignature] = []
    # Loop invariant: methods and method_sigs contain data for all method nodes processed
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods.append(item.name)
            method_sigs.append(_parse_method_signature(item))

    is_protocol = "Protocol" in bases or any(
        b.endswith(".Protocol") for b in bases
    )
    has_inv = has_decorator(node, aliases.invariant_names)
    has_no_inv_comment = _check_no_invariant_comment(node, source)

    return ClassInfo(
        name=node.name,
        line=node.lineno,
        bases=tuple(bases),
        methods=tuple(methods),
        is_protocol=is_protocol,
        method_signatures=tuple(method_sigs),
        has_invariant=has_inv,
        has_no_invariant_comment=has_no_inv_comment,
    )


@icontract.require(
    lambda node: isinstance(node, ast.ClassDef),
    "node must be a class definition",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _check_no_invariant_comment(node: ast.ClassDef, source: str) -> bool:
    """Check if the class is preceded by a '# no-invariant:' comment."""
    if not source:
        return False
    lines = source.splitlines()
    class_line_index = node.lineno - 1
    # Loop invariant: checking lines above the class for no-invariant comment
    for offset in range(1, min(6, class_line_index + 1)):
        prev_line = lines[class_line_index - offset].strip()
        if prev_line.startswith("# no-invariant:"):
            return True
        if prev_line.startswith("#"):
            continue
        if not prev_line.startswith("@"):
            break
    return False


@icontract.require(lambda node: isinstance(node, ast.ClassDef), "node must be a class definition")
@icontract.ensure(
    lambda result: isinstance(result, ProtocolInfo),
    "result must be a ProtocolInfo",
)
def _parse_protocol(node: ast.ClassDef) -> ProtocolInfo:
    """Parse a Protocol class into a ProtocolInfo with method signatures.

    Args:
        node: An AST ClassDef node representing a Protocol.

    Returns:
        A ProtocolInfo with method signatures.
    """
    methods: list[MethodSignature] = []

    # Loop invariant: methods contains signatures for all method nodes processed
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods.append(_parse_method_signature(item))

    return ProtocolInfo(
        name=node.name,
        line=node.lineno,
        methods=tuple(methods),
    )


@icontract.require(
    lambda node: isinstance(node, ast.AST),
    "node must be an AST node",
)
@icontract.ensure(
    lambda result: isinstance(result, str),
    "result must be a string",
)
def _get_name(node: ast.expr) -> str:
    """Get a simple name from an AST expression.

    Args:
        node: An AST expression node.

    Returns:
        The name string.
    """
    # Variant: depth of nesting decreases
    if isinstance(node, ast.Name):
        return node.id
    elif isinstance(node, ast.Attribute):
        return f"{_get_name(node.value)}.{node.attr}"
    return "<unknown>"


@icontract.require(
    lambda node: isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)),
    "node must be a function definition",
)
@icontract.ensure(
    lambda result: isinstance(result, MethodSignature),
    "result must be a MethodSignature",
)
def _parse_method_signature(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> MethodSignature:
    """Extract parameter and return-shape metadata for interface checks."""
    params: list[str] = []
    required_parameters = 0

    positional_params = list(node.args.posonlyargs) + list(node.args.args)
    first_optional_index = len(positional_params) - len(node.args.defaults)
    # Loop invariant: params and required_parameters reflect positional_params[0..i].
    for index, arg in enumerate(positional_params):
        if arg.arg in ("self", "cls"):
            continue
        params.append(arg.arg)
        if index < first_optional_index:
            required_parameters += 1

    # Loop invariant: params and required_parameters reflect kwonlyargs[0..i].
    for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
        if arg.arg in ("self", "cls"):
            continue
        params.append(arg.arg)
        if default is None:
            required_parameters += 1

    if node.args.vararg is not None and node.args.vararg.arg not in ("self", "cls"):
        params.append(node.args.vararg.arg)
    if node.args.kwarg is not None and node.args.kwarg.arg not in ("self", "cls"):
        params.append(node.args.kwarg.arg)

    return MethodSignature(
        name=node.name,
        parameters=tuple(params),
        has_return_annotation=node.returns is not None,
        required_parameters=required_parameters,
        return_annotation=ast.unparse(node.returns) if node.returns else None,
    )


@icontract.require(
    lambda module_path: isinstance(module_path, str),
    "module_path must be a string",
)
@icontract.require(
    lambda segment: isinstance(segment, str) and len(segment) > 0,
    "segment must be a non-empty string",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _module_path_has_segment(module_path: str, segment: str) -> bool:
    """Check whether a slash-separated path contains an exact segment."""
    normalized = module_path.replace("\\", "/").strip("/")
    if not normalized:
        return False
    segments = tuple(part for part in normalized.split("/") if part and part != ".")
    return segment in segments


# ---------------------------------------------------------------------------
# Compositional checks
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
    # Collect all protocols from port modules
    protocols: list[tuple[str, ProtocolInfo]] = []
    # Loop invariant: protocols contains all Protocol defs from ports modules[0..i]
    for mod in modules:
        if _module_path_has_segment(mod.module_path, "ports"):
            # Loop invariant: protocols updated for mod.protocols[0..j]
            for proto in mod.protocols:
                protocols.append((mod.file_path, proto))

    if not protocols:
        return []

    # Collect all classes from adapter modules
    adapter_classes: list[tuple[str, ClassInfo]] = []
    # Loop invariant: adapter_classes contains classes from adapter modules[0..i]
    for mod in modules:
        if _module_path_has_segment(mod.module_path, "adapters"):
            # Loop invariant: adapter_classes updated for mod.classes[0..j]
            for cls in mod.classes:
                adapter_classes.append((mod.file_path, cls))

    results: list[FunctionResult] = []

    # For each protocol, check if adapter classes implement all methods
    # Loop invariant: results contains compliance findings for protocols[0..i]
    for port_file, proto in protocols:
        proto_method_names = {m.name for m in proto.methods}

        # Loop invariant: checked adapter_classes[0..j] against this protocol
        for adapter_file, adapter_cls in adapter_classes:
            if not _class_likely_implements(adapter_cls, proto):
                continue

            adapter_method_names = set(adapter_cls.methods)
            missing = proto_method_names - adapter_method_names

            # Report missing methods
            # Loop invariant: results contains findings for missing[0..k]
            for method_name in sorted(missing):
                results.append(FunctionResult(
                    function=adapter_cls.name,
                    file=adapter_file,
                    line=adapter_cls.line,
                    level_requested=6,
                    level_achieved=5,
                    status=CheckStatus.FAILED,
                    details=(Detail(
                        level=VerificationLevel.COMPOSITIONAL,
                        tool="compositional",
                        finding_type="violation",
                        message=(
                            f"Class '{adapter_cls.name}' appears to implement "
                            f"'{proto.name}' but is missing method '{method_name}'"
                        ),
                        suggestion=f"Add method '{method_name}' to '{adapter_cls.name}'",
                    ),),
                ))

            # Check signature compatibility for methods that exist in both
            adapter_sig_map = {s.name: s for s in adapter_cls.method_signatures}
            # Loop invariant: results contains signature findings for proto.methods[0..k]
            for proto_method in proto.methods:
                adapter_sig = adapter_sig_map.get(proto_method.name)
                if adapter_sig is None:
                    continue  # already reported as missing above
                # Loop invariant: results updated for all issues from this comparison
                for issue in _check_signature_compatibility(adapter_sig, proto_method):
                    results.append(FunctionResult(
                        function=adapter_cls.name,
                        file=adapter_file,
                        line=adapter_cls.line,
                        level_requested=6,
                        level_achieved=5,
                        status=CheckStatus.FAILED,
                        details=(Detail(
                            level=VerificationLevel.COMPOSITIONAL,
                            tool="compositional",
                            finding_type="violation",
                            message=(
                                f"Class '{adapter_cls.name}' vs Protocol "
                                f"'{proto.name}': {issue}"
                            ),
                            suggestion=(
                                f"Update method signature to match "
                                f"Protocol '{proto.name}'"
                            ),
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

        # Check functions for contract presence
        # Loop invariant: results contains findings for function_infos[0..j]
        for func_info in mod.function_infos:
            if not _should_check_function_contracts(func_info, config):
                continue

            details: list[Detail] = []
            has_params = len(func_info.parameters) > 0

            if has_params and not func_info.has_require:
                details.append(Detail(
                    level=VerificationLevel.COMPOSITIONAL,
                    tool="compositional",
                    finding_type="violation",
                    message=(
                        f"Function '{func_info.name}' in {mod.module_path} "
                        "missing @icontract.require (precondition)"
                    ),
                    suggestion="Add precondition contract",
                ))

            if not func_info.has_ensure:
                details.append(Detail(
                    level=VerificationLevel.COMPOSITIONAL,
                    tool="compositional",
                    finding_type="violation",
                    message=(
                        f"Function '{func_info.name}' in {mod.module_path} "
                        "missing @icontract.ensure (postcondition)"
                    ),
                    suggestion="Add postcondition contract",
                ))

            if details:
                results.append(FunctionResult(
                    function=func_info.name,
                    file=mod.file_path,
                    line=func_info.line,
                    level_requested=6,
                    level_achieved=5,
                    status=CheckStatus.FAILED,
                    details=tuple(details),
                ))

        # Check classes for invariants (skip Enum, exception, Protocol classes)
        # Loop invariant: results contains findings for classes[0..j]
        for cls in mod.classes:
            if not _should_check_class_invariants(cls, config):
                continue
            if _is_enum_class(cls) or _is_exception_class(cls) or cls.is_protocol or cls.has_no_invariant_comment:
                continue
            if not cls.has_invariant:
                results.append(FunctionResult(
                    function=cls.name,
                    file=mod.file_path,
                    line=cls.line,
                    level_requested=6,
                    level_achieved=5,
                    status=CheckStatus.FAILED,
                    details=(Detail(
                        level=VerificationLevel.COMPOSITIONAL,
                        tool="compositional",
                        finding_type="violation",
                        message=(
                            f"Class '{cls.name}' in {mod.module_path} "
                            "missing @icontract.invariant"
                        ),
                        suggestion="Add class invariant",
                    ),),
                ))

        # Informational: flag large modules
        total_public = len([f for f in mod.function_infos if f.is_public])
        if total_public > 10:
            results.append(FunctionResult(
                function="<module>",
                file=mod.file_path,
                line=1,
                level_requested=6,
                level_achieved=6,
                status=CheckStatus.PASSED,
                details=(Detail(
                    level=VerificationLevel.COMPOSITIONAL,
                    tool="compositional",
                    finding_type="info",
                    message=(
                        f"Module has {total_public} public functions — "
                        "consider splitting into smaller modules"
                    ),
                ),),
            ))

    return results


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

        # Track reported pairs to avoid duplicates from multiple calls
        reported_ensure: set[str] = set()
        reported_require: set[str] = set()

        # Loop invariant: results contains findings for function_infos[0..j]
        for func_info in mod.function_infos:
            if not func_info.is_public:
                continue

            # Loop invariant: results contains findings for calls[0..k]
            for call_target in func_info.calls:
                resolved = _resolve_call_target(
                    call_target, mod, import_map, module_functions,
                )
                if resolved is None:
                    continue

                callee_module, callee_func = resolved

                if callee_module == mod.module_path:
                    continue

                ensure_key = f"{func_info.name}->{callee_module}"
                if (
                    callee_func.has_require
                    and not func_info.has_ensure
                    and ensure_key not in reported_ensure
                ):
                    reported_ensure.add(ensure_key)
                    results.append(FunctionResult(
                        function=func_info.name,
                        file=mod.file_path,
                        line=func_info.line,
                        level_requested=6,
                        level_achieved=5,
                        status=CheckStatus.FAILED,
                        details=(Detail(
                            level=VerificationLevel.COMPOSITIONAL,
                            tool="compositional",
                            finding_type="violation",
                            message=(
                                f"Function '{func_info.name}' calls "
                                f"'{callee_func.name}' (in {callee_module}) "
                                f"which has preconditions, but "
                                f"'{func_info.name}' lacks postconditions"
                            ),
                            suggestion=(
                                f"Add @icontract.ensure to '{func_info.name}' "
                                f"to document guarantees for "
                                f"'{callee_func.name}'"
                            ),
                        ),),
                    ))

                require_key = f"{func_info.name}->{callee_module}"
                if (
                    callee_func.has_require
                    and len(func_info.parameters) > 0
                    and not func_info.has_require
                    and require_key not in reported_require
                ):
                    reported_require.add(require_key)
                    results.append(FunctionResult(
                        function=func_info.name,
                        file=mod.file_path,
                        line=func_info.line,
                        level_requested=6,
                        level_achieved=5,
                        status=CheckStatus.FAILED,
                        details=(Detail(
                            level=VerificationLevel.COMPOSITIONAL,
                            tool="compositional",
                            finding_type="violation",
                            message=(
                                f"Function '{func_info.name}' passes data to "
                                f"'{callee_func.name}' (in {callee_module}) "
                                f"which has preconditions, but "
                                f"'{func_info.name}' has no preconditions "
                                f"to constrain its inputs"
                            ),
                            suggestion=(
                                f"Add @icontract.require to "
                                f"'{func_info.name}' to constrain inputs "
                                f"flowing to '{callee_func.name}'"
                            ),
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


@icontract.require(lambda name: isinstance(name, str), "name must be a string")
@icontract.ensure(
    lambda result: isinstance(result, str),
    "result must be a string",
)
def _normalize_module_name(name: str) -> str:
    """Normalize a module name for comparison by converting to dot-separated form.

    Strips '.py' suffix and replaces '/' with '.'.

    Args:
        name: A module name or path string.

    Returns:
        Normalized dot-separated module name.
    """
    return name.removesuffix(".py").replace("/", ".")


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

        # Track reported pairs to avoid duplicates
        reported_untyped: set[str] = set()
        reported_return: set[str] = set()

        # Loop invariant: results contains findings for function_infos[0..j]
        for func_info in mod.function_infos:
            if not func_info.is_public:
                continue

            # Loop invariant: results contains findings for calls[0..k]
            for call_target in func_info.calls:
                resolved = _resolve_call_target(
                    call_target, mod, import_map, module_functions,
                )
                if resolved is None:
                    continue

                callee_module, callee_func = resolved
                if callee_module == mod.module_path:
                    continue

                if not callee_func.is_public:
                    continue

                # Check: callee parameters should be typed at boundaries
                untyped = [
                    p for p in callee_func.parameters
                    if p.annotation is None
                ]
                untyped_key = f"{callee_module}.{callee_func.name}"
                if untyped and untyped_key not in reported_untyped:
                    reported_untyped.add(untyped_key)
                    param_names = ", ".join(p.name for p in untyped)
                    callee_file = _find_file_for_module(callee_module, modules)
                    results.append(FunctionResult(
                        function=callee_func.name,
                        file=callee_file,
                        line=callee_func.line,
                        level_requested=6,
                        level_achieved=5,
                        status=CheckStatus.FAILED,
                        details=(Detail(
                            level=VerificationLevel.COMPOSITIONAL,
                            tool="compositional",
                            finding_type="violation",
                            message=(
                                f"Function '{callee_func.name}' receives "
                                f"cross-module data but parameters "
                                f"[{param_names}] lack type annotations"
                            ),
                            suggestion=(
                                "Add type annotations to all parameters "
                                "that receive cross-module data"
                            ),
                        ),),
                    ))

                # Check: caller return type should be annotated
                return_key = f"{func_info.name}->{callee_module}"
                if (
                    callee_func.has_require
                    and func_info.return_annotation is None
                    and return_key not in reported_return
                ):
                    reported_return.add(return_key)
                    results.append(FunctionResult(
                        function=func_info.name,
                        file=mod.file_path,
                        line=func_info.line,
                        level_requested=6,
                        level_achieved=5,
                        status=CheckStatus.FAILED,
                        details=(Detail(
                            level=VerificationLevel.COMPOSITIONAL,
                            tool="compositional",
                            finding_type="violation",
                            message=(
                                f"Function '{func_info.name}' provides data "
                                f"to '{callee_func.name}' (which has "
                                f"preconditions) but lacks a return type "
                                f"annotation"
                            ),
                            suggestion=(
                                "Add return type annotation to document "
                                "the data contract"
                            ),
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

    # Collect all protocols from ports
    all_protocols: dict[str, tuple[str, ProtocolInfo]] = {}
    # Loop invariant: all_protocols and non-protocol findings updated for modules[0..i]
    for mod in modules:
        if not _module_path_has_segment(mod.module_path, "ports"):
            continue
        # Loop invariant: all_protocols updated for mod.protocols[0..j]
        for proto in mod.protocols:
            all_protocols[proto.name] = (mod.file_path, proto)
        # Check non-protocol classes in ports (allow DTO dataclasses)
        # Loop invariant: results updated for mod.classes[0..j]
        for cls in mod.classes:
            if cls.is_protocol or cls.name.startswith("_"):
                continue
            # Allow dataclass DTOs: classes with no public methods are data carriers
            public_methods = [m for m in cls.methods if not m.startswith("_")]
            if public_methods:
                results.append(FunctionResult(
                    function=cls.name,
                    file=mod.file_path,
                    line=cls.line,
                    level_requested=6,
                    level_achieved=5,
                    status=CheckStatus.FAILED,
                    details=(Detail(
                        level=VerificationLevel.COMPOSITIONAL,
                        tool="compositional",
                        finding_type="violation",
                        message=(
                            f"Class '{cls.name}' in ports/ is not a "
                            f"Protocol. All classes in ports/ must be "
                            f"Protocol definitions or data carriers."
                        ),
                        suggestion=(
                            "Add Protocol as a base class, or move to "
                            "adapters/"
                        ),
                    ),),
                ))

    # Check every Protocol has at least one likely implementation
    adapter_classes: list[ClassInfo] = []
    # Loop invariant: adapter_classes contains classes from adapter modules[0..i]
    for mod in modules:
        if _module_path_has_segment(mod.module_path, "adapters"):
            adapter_classes.extend(mod.classes)

    # Loop invariant: results updated for all_protocols entries[0..i]
    for proto_name, (proto_file, proto_info) in all_protocols.items():
        has_impl = False
        # Loop invariant: has_impl is True if any adapter_classes[0..j] implements proto
        for cls in adapter_classes:
            if _class_likely_implements(cls, proto_info):
                has_impl = True
                break
        if not has_impl:
            results.append(FunctionResult(
                function=proto_name,
                file=proto_file,
                line=proto_info.line,
                level_requested=6,
                level_achieved=6,
                status=CheckStatus.PASSED,
                details=(Detail(
                    level=VerificationLevel.COMPOSITIONAL,
                    tool="compositional",
                    finding_type="info",
                    message=(
                        f"Protocol '{proto_name}' has no detected adapter "
                        f"implementation. This may indicate a missing "
                        f"adapter or a detection limitation."
                    ),
                ),),
            ))

    # Check forbidden imports in core
    forbidden = set(config.architecture_rules.forbidden_imports_in_core)
    # Loop invariant: results updated for modules[0..i] regarding forbidden imports
    for mod in modules:
        if not is_core_module(mod.module_path, config):
            continue
        # Loop invariant: results updated for imports[0..j]
        for imp in mod.imports:
            top_module = imp.split(".")[0]
            if top_module in forbidden:
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
                        message=(
                            f"Core module '{mod.module_path}' imports "
                            f"forbidden I/O library '{imp}'"
                        ),
                        suggestion=(
                            "Use dependency injection via a Protocol "
                            "in ports/"
                        ),
                    ),),
                ))
        # Loop invariant: results updated for from_imports[0..j]
        for from_mod, _ in mod.from_imports:
            top_module = from_mod.split(".")[0]
            if top_module in forbidden:
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
                        message=(
                            f"Core module '{mod.module_path}' imports from "
                            f"forbidden I/O library '{from_mod}'"
                        ),
                        suggestion=(
                            "Use dependency injection via a Protocol "
                            "in ports/"
                        ),
                    ),),
                ))

    return results


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


@icontract.require(
    lambda sources: isinstance(sources, (list, tuple)),
    "sources must be a list or tuple",
)
@icontract.ensure(
    lambda result: isinstance(result, CheckResult),
    "result must be a CheckResult",
)
def check_compositional(
    sources: list[tuple[str, str, str]] | tuple[tuple[str, str, str], ...],
    config: SerenecodeConfig,
) -> CheckResult:
    """Run the full Level 5 compositional check on a set of source files.

    This is the main entry point for compositional verification. It
    parses all modules, then runs all compositional checks: dependency
    direction, circular dependencies, interface compliance, contract
    completeness, assume-guarantee reasoning, data flow verification,
    and system invariants.

    Args:
        sources: Sequence of (source_code, file_path, module_path) tuples.
        config: Active Serenecode configuration.

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

    elapsed = time.monotonic() - start_time
    return make_check_result(
        tuple(all_results),
        level_requested=6,
        duration_seconds=elapsed,
    )
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
