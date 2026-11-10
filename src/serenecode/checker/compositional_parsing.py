"""Data structures and AST parsing for compositional analysis.

This module defines the core data structures (dataclasses) and the
module-parsing logic used by the Level 6 compositional checker.

This is a core module — no I/O operations are permitted. Source code
is received as structured SourceFile objects with pre-read content.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

import icontract

from serenecode.checker.contract_binding import _PRECONDITION_OPT_OUT
from serenecode.checker.structural_helpers import (
    IcontractNames,
    _function_opt_out_lines,
    get_decorator_name,
    has_decorator,
    resolve_icontract_aliases,
)
from serenecode.checker.structural_quality import _has_opt_out_comment
from serenecode.config import SerenecodeConfig
from serenecode.contracts.predicates import is_non_empty_string


# ---------------------------------------------------------------------------
# Data structures for compositional analysis
# ---------------------------------------------------------------------------


@icontract.invariant(
    lambda self: is_non_empty_string(self.name),
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

    @icontract.ensure(
        lambda self: 0 <= self.required_parameters <= len(self.parameters),
        "required_parameters must be within bounds after init",
    )
    def __post_init__(self) -> None:
        """Default required_parameters to len(parameters) when not explicitly set."""
        if self.required_parameters < 0:
            object.__setattr__(self, "required_parameters", len(self.parameters))


@icontract.invariant(
    lambda self: is_non_empty_string(self.name),
    "Parameter name must be non-empty",
)
@dataclass(frozen=True)
class ParameterInfo:
    """A single function parameter with its type annotation."""

    name: str
    annotation: str | None  # type annotation as string, or None if untyped


@icontract.invariant(
    lambda self: is_non_empty_string(self.name) and self.line >= 1,
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
    precondition_waived: bool = False  # carries a "# no-precondition: <reason>" comment


@icontract.invariant(
    lambda self: is_non_empty_string(self.name) and self.line >= 1,
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
    lambda self: is_non_empty_string(self.name) and self.line >= 1,
    "Protocol must have a non-empty name and valid line number",
)
@dataclass(frozen=True)
class ProtocolInfo:
    """Information about a Protocol definition."""

    name: str
    line: int
    methods: tuple[MethodSignature, ...]


@icontract.invariant(
    lambda self: is_non_empty_string(self.file_path) and is_non_empty_string(self.module_path),
    "Module must have non-empty file and module paths",
)
@icontract.invariant(
    lambda self: (
        all(is_non_empty_string(name) for name in self.imports)
        and all(is_non_empty_string(name) for name in self.all_imports)
        and all(is_non_empty_string(m) and is_non_empty_string(n) for m, n in self.from_imports)
    ),
    "Every recorded import must name a module",
)
@dataclass(frozen=True)
class ModuleInfo:
    """Parsed information about a single module for compositional analysis.

    ``imports`` and ``from_imports`` hold the top-level statements, which is
    what cycle detection uses. ``all_imports`` holds every import target in
    the file, wherever it appears, which is what the layer rule uses.
    """

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
    all_imports: tuple[str, ...] = ()


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
    lambda module_path: is_non_empty_string(module_path),
    "module_path must be a non-empty string",
)
@icontract.require(
    lambda file_path, module_path: "\x00" not in file_path and "\x00" not in module_path,
    "paths must not contain a null byte; a parent reference such as '../pkg' is allowed",
)
@icontract.ensure(
    lambda file_path, module_path, result: result.file_path == file_path and result.module_path == module_path,
    "parsed ModuleInfo must record the supplied file_path and module_path verbatim",
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
            func_info = _parse_function_info(node, aliases, source)
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
        all_imports=_collect_all_imports(tree, module_path),
    )


@icontract.require(lambda tree: isinstance(tree, ast.Module), "tree must be an ast.Module")
@icontract.require(lambda module_path: isinstance(module_path, str), "module_path must be a string")
@icontract.ensure(lambda result: all(result), "every collected import names a module")
@icontract.ensure(lambda result: len(set(result)) == len(result), "each import target is listed once")
def _collect_all_imports(tree: ast.Module, module_path: str) -> tuple[str, ...]:
    """List every module a file imports, wherever the statement appears.

    Implements: REQ-069

    An import inside a function body, a ``try`` or an ``if`` creates the same
    dependency as one at the top of the file, so the layer rule has to see
    it. ``from package import name`` contributes ``package.name`` as well as
    ``package``, because ``name`` may itself be a module.

    Args:
        tree: The parsed module.
        module_path: The module's path, used to resolve relative imports.

    Returns:
        The distinct import targets in first-seen order.
    """
    targets: list[str] = []
    # Loop invariant: targets holds the import targets of every node walked so far
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names = [alias.name for alias in node.names if alias.name != "*"]
            resolved = _resolve_from_import_module(node, module_path)
            if resolved:
                targets.append(resolved)
                targets.extend(f"{resolved}.{name}" for name in names)
            else:
                # `from .. import name` above the known package root
                targets.extend(names)
        elif isinstance(node, ast.Call):
            dynamic_target = _dynamic_import_target(node)
            if dynamic_target is not None:
                targets.append(dynamic_target)
    return tuple(dict.fromkeys(targets))


@icontract.require(lambda call: isinstance(call, ast.Call), "call must be a call expression")
@icontract.ensure(lambda result: result is None or len(result) > 0, "a reported target is non-empty")
def _dynamic_import_target(call: ast.Call) -> str | None:
    """Return the string-literal target of ``import_module`` / ``__import__``.

    A computed module name cannot be known from the AST and yields None.
    """
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    if name not in ("import_module", "__import__") or not call.args:
        return None
    target = call.args[0]
    if not isinstance(target, ast.Constant) or not isinstance(target.value, str):
        return None
    return target.value.lstrip(".") or None


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
    """Check whether invariant completeness applies to a class.

    Implements: REQ-065
    """
    if not config.contract_requirements.require_on_classes:
        return False
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
    source: str = "",
) -> FunctionInfo:
    """Parse a function definition into a FunctionInfo.

    Implements: REQ-065

    Args:
        node: An AST FunctionDef or AsyncFunctionDef node.
        aliases: Resolved icontract import names for decorator detection.
        source: The module source, read for the precondition waiver comment.

    Returns:
        A FunctionInfo with function metadata including contracts and calls.
    """
    parameters = _parse_parameters(node)
    return_ann = ast.unparse(node.returns) if node.returns else None
    has_req = has_decorator(node, aliases.require_names)
    has_ens = has_decorator(node, aliases.ensure_names)
    calls = _extract_calls(node)
    # The waiver only matters when the precondition is absent, so the comment
    # scan is skipped for every function that already has one.
    waived = not has_req and bool(source) and _has_opt_out_comment(
        source, _function_opt_out_lines(node), _PRECONDITION_OPT_OUT,
    )

    return FunctionInfo(
        name=node.name,
        line=node.lineno,
        is_public=_is_public_function_name(node.name),
        parameters=parameters,
        return_annotation=return_ann,
        has_require=has_req,
        has_ensure=has_ens,
        calls=calls,
        precondition_waived=waived,
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
