"""SERENECODE.md parser and configuration models.

This module defines the structured configuration that drives the
verification pipeline. It provides hardcoded configs for the default,
strict, and minimal templates, plus a basic markdown parser that
detects the template type and extracts exemption paths.

This is a core module — no I/O imports are permitted. Configuration
is parsed from strings, not files.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import icontract

from serenecode.contracts.predicates import is_non_empty_string

__all__ = [
    "ContractConfig",
    "TypeConfig",
    "ArchitectureConfig",
    "ErrorHandlingConfig",
    "LoopRecursionConfig",
    "NamingConfig",
    "ExemptionConfig",
    "CodeQualityConfig",
    "ModuleHealthConfig",
    "SerenecodeConfig",
    "default_config",
    "strict_config",
    "minimal_config",
    "config_for_template",
    "parse_serenecode_md",
    "is_core_module",
    "is_exempt_module",
]


@icontract.invariant(
    lambda self: not self.require_on_private or self.require_on_public_functions,
    "Private contract requirements imply public contract requirements",
)
@dataclass(frozen=True)
class ContractConfig:
    """Configuration for contract requirements."""

    require_on_public_functions: bool
    require_on_classes: bool
    require_description_strings: bool
    require_on_private: bool


@icontract.invariant(
    lambda self: not self.forbid_any_in_core or self.require_annotations,
    "Forbidding Any in core implies annotations are required",
)
@dataclass(frozen=True)
class TypeConfig:
    """Configuration for type annotation requirements."""

    require_annotations: bool
    forbid_any_in_core: bool
    require_parameterized_generics: bool


@icontract.invariant(
    lambda self: isinstance(self.forbidden_imports_in_core, tuple)
    and isinstance(self.core_module_patterns, tuple),
    "Import and pattern lists must be tuples",
)
@dataclass(frozen=True)
class ArchitectureConfig:
    """Configuration for architectural rules."""

    forbidden_imports_in_core: tuple[str, ...]
    core_module_patterns: tuple[str, ...]


@icontract.invariant(
    lambda self: isinstance(self.forbidden_exception_types, tuple),
    "Forbidden exception types must be a tuple",
)
@dataclass(frozen=True)
class ErrorHandlingConfig:
    """Configuration for error handling rules."""

    require_domain_exceptions: bool
    forbidden_exception_types: tuple[str, ...]
    forbid_silent_exception_handling: bool = False


@icontract.invariant(
    lambda self: not self.require_recursion_variant_comments
    or self.require_loop_invariant_comments,
    "Recursion variant requirements imply loop invariant requirements",
)
@dataclass(frozen=True)
class LoopRecursionConfig:
    """Configuration for loop and recursion documentation rules."""

    require_loop_invariant_comments: bool
    require_recursion_variant_comments: bool


@icontract.invariant(
    lambda self: self.module_style in ("snake_case", "PascalCase", "UPPER_SNAKE_CASE")
    and self.class_style in ("snake_case", "PascalCase", "UPPER_SNAKE_CASE")
    and self.function_style in ("snake_case", "PascalCase", "UPPER_SNAKE_CASE"),
    "Naming styles must be recognized conventions",
)
@dataclass(frozen=True)
class NamingConfig:
    """Configuration for naming conventions."""

    module_style: str
    class_style: str
    function_style: str


@icontract.invariant(
    lambda self: isinstance(self.exempt_paths, tuple),
    "Exempt paths must be a tuple",
)
@dataclass(frozen=True)
class ExemptionConfig:
    """Configuration for exempt paths."""

    exempt_paths: tuple[str, ...]


# no-invariant: all fields are independent boolean toggles for AI-failure-mode L1 checks; there are no inter-flag relationships to constrain
@dataclass(frozen=True)
class CodeQualityConfig:
    """Configuration for AI-failure-mode code quality checks (Level 1).

    Each flag toggles one structural check that targets a pattern AI
    coding agents reliably ship that compiles and looks correct but
    represents a real bug or anti-pattern. All defaults are documented
    in SERENECODE.md under 'Code Quality Standards'.
    """

    forbid_stub_residue: bool
    forbid_mutable_default_arguments: bool
    forbid_bare_asserts_outside_tests: bool
    forbid_print_in_core: bool
    forbid_dangerous_calls: bool
    forbid_todo_comments: bool
    require_test_assertions: bool
    forbid_isinstance_tautology: bool
    forbid_unused_parameters: bool


@icontract.invariant(
    lambda self: self.file_length_warn > 0 and self.file_length_error > 0,
    "File length thresholds must be positive",
)
@icontract.invariant(
    lambda self: self.file_length_warn < self.file_length_error,
    "File length warn threshold must be less than error threshold",
)
@icontract.invariant(
    lambda self: self.function_length_warn > 0 and self.function_length_error > 0,
    "Function length thresholds must be positive",
)
@icontract.invariant(
    lambda self: self.function_length_warn < self.function_length_error,
    "Function length warn threshold must be less than error threshold",
)
@icontract.invariant(
    lambda self: self.parameter_count_warn > 0 and self.parameter_count_error > 0,
    "Parameter count thresholds must be positive",
)
@icontract.invariant(
    lambda self: self.parameter_count_warn < self.parameter_count_error,
    "Parameter count warn threshold must be less than error threshold",
)
@icontract.invariant(
    lambda self: self.class_method_count_warn > 0 and self.class_method_count_error > 0,
    "Class method count thresholds must be positive",
)
@icontract.invariant(
    lambda self: self.class_method_count_warn < self.class_method_count_error,
    "Class method count warn threshold must be less than error threshold",
)
@dataclass(frozen=True)
class ModuleHealthConfig:
    """Configuration for module health checks (Level 1).

    Implements: REQ-001

    Each pair of thresholds defines an advisory warning level and a hard
    error level. Advisory warnings use the EXEMPT advisory pattern
    (visible but non-blocking). Errors use FAILED status and block
    verification.
    """

    enabled: bool
    file_length_warn: int
    file_length_error: int
    function_length_warn: int
    function_length_error: int
    parameter_count_warn: int
    parameter_count_error: int
    class_method_count_warn: int
    class_method_count_error: int


@icontract.invariant(
    lambda self: 1 <= self.recommended_level <= 6,
    "Recommended level must be between 1 and 6",
)
@icontract.invariant(
    lambda self: self.template_name in ("default", "strict", "minimal"),
    "Template name must be a recognized template",
)
@dataclass(frozen=True)
class SerenecodeConfig:
    """Complete Serenecode configuration parsed from SERENECODE.md.

    Implements: REQ-002

    This is the central configuration object that all checkers use
    to determine what rules to enforce.
    """

    contract_requirements: ContractConfig
    type_requirements: TypeConfig
    architecture_rules: ArchitectureConfig
    error_handling_rules: ErrorHandlingConfig
    loop_recursion_rules: LoopRecursionConfig
    naming_conventions: NamingConfig
    exemptions: ExemptionConfig
    code_quality_rules: CodeQualityConfig
    module_health: ModuleHealthConfig
    template_name: str
    recommended_level: int = 3


# Default I/O-related modules forbidden in core
_DEFAULT_FORBIDDEN_IMPORTS = (
    "os",
    "pathlib",
    "subprocess",
    "requests",
    "socket",
    "shutil",
    "tempfile",
    "glob",
)

# Default core module path patterns
_DEFAULT_CORE_PATTERNS = (
    "core/",
    "checker/",
    "models.py",
    "contracts/",
    "config.py",
)

# Default exempt paths
_DEFAULT_EXEMPT_PATHS = (
    "cli.py",
    "__init__.py",
    "adapters/",
    "mcp/",
    "templates/",
    "tests/fixtures/",
    "ports/",
    "exceptions.py",
)

# Module health presets per template
_DEFAULT_MODULE_HEALTH = ModuleHealthConfig(
    enabled=True,
    file_length_warn=500, file_length_error=1000,
    function_length_warn=50, function_length_error=100,
    parameter_count_warn=5, parameter_count_error=8,
    class_method_count_warn=15, class_method_count_error=25,
)

_STRICT_MODULE_HEALTH = ModuleHealthConfig(
    enabled=True,
    file_length_warn=400, file_length_error=700,
    function_length_warn=30, function_length_error=60,
    parameter_count_warn=4, parameter_count_error=6,
    class_method_count_warn=10, class_method_count_error=18,
)

_MINIMAL_MODULE_HEALTH = ModuleHealthConfig(
    enabled=True,
    file_length_warn=750, file_length_error=1500,
    function_length_warn=75, function_length_error=150,
    parameter_count_warn=7, parameter_count_error=10,
    class_method_count_warn=20, class_method_count_error=35,
)

# Default forbidden exception types in core
_DEFAULT_FORBIDDEN_EXCEPTIONS = (
    "Exception",
    "ValueError",
    "TypeError",
    "RuntimeError",
    "KeyError",
    "IndexError",
    "AttributeError",
)


@icontract.ensure(
    lambda result: result.template_name == "default",
    "default config must have template_name 'default'",
)
def default_config() -> SerenecodeConfig:
    """Return the default Serenecode configuration.

    Implements: REQ-003

    Matches the conventions defined in the standard SERENECODE.md template.
    """
    return SerenecodeConfig(
        contract_requirements=ContractConfig(
            require_on_public_functions=True,
            require_on_classes=True,
            require_description_strings=True,
            require_on_private=False,
        ),
        type_requirements=TypeConfig(
            require_annotations=True,
            forbid_any_in_core=True,
            require_parameterized_generics=True,
        ),
        architecture_rules=ArchitectureConfig(
            forbidden_imports_in_core=_DEFAULT_FORBIDDEN_IMPORTS,
            core_module_patterns=_DEFAULT_CORE_PATTERNS,
        ),
        error_handling_rules=ErrorHandlingConfig(
            require_domain_exceptions=False,
            forbidden_exception_types=(),
            forbid_silent_exception_handling=True,
        ),
        loop_recursion_rules=LoopRecursionConfig(
            require_loop_invariant_comments=False,
            require_recursion_variant_comments=False,
        ),
        naming_conventions=NamingConfig(
            module_style="snake_case",
            class_style="PascalCase",
            function_style="snake_case",
        ),
        exemptions=ExemptionConfig(
            exempt_paths=_DEFAULT_EXEMPT_PATHS,
        ),
        code_quality_rules=CodeQualityConfig(
            forbid_stub_residue=True,
            forbid_mutable_default_arguments=True,
            forbid_bare_asserts_outside_tests=True,
            forbid_print_in_core=True,
            forbid_dangerous_calls=True,
            forbid_todo_comments=True,
            require_test_assertions=True,
            forbid_isinstance_tautology=True,
            forbid_unused_parameters=False,
        ),
        module_health=_DEFAULT_MODULE_HEALTH,
        template_name="default",
        recommended_level=4,
    )


@icontract.ensure(
    lambda result: result.template_name == "strict",
    "strict config must have template_name 'strict'",
)
def strict_config() -> SerenecodeConfig:
    """Return the strict Serenecode configuration.

    Implements: REQ-003

    All SHOULD become MUST, no exemptions.
    """
    return SerenecodeConfig(
        contract_requirements=ContractConfig(
            require_on_public_functions=True,
            require_on_classes=True,
            require_description_strings=True,
            require_on_private=True,
        ),
        type_requirements=TypeConfig(
            require_annotations=True,
            forbid_any_in_core=True,
            require_parameterized_generics=True,
        ),
        architecture_rules=ArchitectureConfig(
            forbidden_imports_in_core=_DEFAULT_FORBIDDEN_IMPORTS,
            core_module_patterns=_DEFAULT_CORE_PATTERNS,
        ),
        error_handling_rules=ErrorHandlingConfig(
            require_domain_exceptions=True,
            forbidden_exception_types=_DEFAULT_FORBIDDEN_EXCEPTIONS,
            forbid_silent_exception_handling=True,
        ),
        loop_recursion_rules=LoopRecursionConfig(
            require_loop_invariant_comments=True,
            require_recursion_variant_comments=True,
        ),
        naming_conventions=NamingConfig(
            module_style="snake_case",
            class_style="PascalCase",
            function_style="snake_case",
        ),
        exemptions=ExemptionConfig(
            exempt_paths=(),
        ),
        code_quality_rules=CodeQualityConfig(
            forbid_stub_residue=True,
            forbid_mutable_default_arguments=True,
            forbid_bare_asserts_outside_tests=True,
            forbid_print_in_core=True,
            forbid_dangerous_calls=True,
            forbid_todo_comments=True,
            require_test_assertions=True,
            forbid_isinstance_tautology=True,
            forbid_unused_parameters=True,
        ),
        module_health=_STRICT_MODULE_HEALTH,
        template_name="strict",
        recommended_level=6,
    )


@icontract.ensure(
    lambda result: result.template_name == "minimal",
    "minimal config must have template_name 'minimal'",
)
def minimal_config() -> SerenecodeConfig:
    """Return the minimal Serenecode configuration.

    Implements: REQ-003

    Contracts on public functions only, relaxed architecture rules.
    """
    return SerenecodeConfig(
        contract_requirements=ContractConfig(
            require_on_public_functions=True,
            require_on_classes=False,
            require_description_strings=False,
            require_on_private=False,
        ),
        type_requirements=TypeConfig(
            require_annotations=True,
            forbid_any_in_core=False,
            require_parameterized_generics=False,
        ),
        architecture_rules=ArchitectureConfig(
            forbidden_imports_in_core=(),
            core_module_patterns=(),
        ),
        error_handling_rules=ErrorHandlingConfig(
            require_domain_exceptions=False,
            forbidden_exception_types=(),
        ),
        loop_recursion_rules=LoopRecursionConfig(
            require_loop_invariant_comments=False,
            require_recursion_variant_comments=False,
        ),
        naming_conventions=NamingConfig(
            module_style="snake_case",
            class_style="PascalCase",
            function_style="snake_case",
        ),
        exemptions=ExemptionConfig(
            exempt_paths=_DEFAULT_EXEMPT_PATHS,
        ),
        code_quality_rules=CodeQualityConfig(
            forbid_stub_residue=False,
            forbid_mutable_default_arguments=False,
            forbid_bare_asserts_outside_tests=False,
            forbid_print_in_core=False,
            forbid_dangerous_calls=False,
            forbid_todo_comments=False,
            require_test_assertions=False,
            forbid_isinstance_tautology=False,
            forbid_unused_parameters=False,
        ),
        module_health=_MINIMAL_MODULE_HEALTH,
        template_name="minimal",
        recommended_level=2,
    )


@icontract.require(
    lambda template_name: template_name in ("default", "strict", "minimal"),
    "template_name must be 'default', 'strict', or 'minimal'",
)
@icontract.ensure(
    lambda template_name, result: result.template_name == template_name,
    "returned config must match the requested template",
)
def config_for_template(template_name: str) -> SerenecodeConfig:
    """Return the configuration for a named template.

    Args:
        template_name: One of 'default', 'strict', or 'minimal'.

    Returns:
        The corresponding SerenecodeConfig.
    """
    configs = {
        "default": default_config,
        "strict": strict_config,
        "minimal": minimal_config,
    }
    return configs[template_name]()


@icontract.require(
    lambda content: isinstance(content, str),
    "content must be a string",
)
@icontract.ensure(
    lambda result: result.template_name in ("default", "strict", "minimal"),
    "parsed config must have a valid template name",
)
def parse_serenecode_md(content: str) -> SerenecodeConfig:
    """Parse a SERENECODE.md file content into a SerenecodeConfig.

    Uses template detection as a starting point, then applies supported
    rule overrides extracted from the file content so local edits still
    influence the active verification policy.

    Args:
        content: The full text content of a SERENECODE.md file.

    Returns:
        A SerenecodeConfig matching the detected template with
        any extracted exemption overrides.
    """
    template = _detect_template(content)
    config = config_for_template(template)
    exempt_paths = _extract_exemptions(content)
    return _apply_content_overrides(content, config, exempt_paths)


@icontract.require(
    lambda content: content is not None,
    "content must be provided",
)
@icontract.ensure(
    lambda config, result: result.naming_conventions == config.naming_conventions,
    "naming conventions are preserved through overrides",
)
def _apply_content_overrides(
    content: str,
    config: SerenecodeConfig,
    exempt_paths: tuple[str, ...] | None,
) -> SerenecodeConfig:
    """Apply supported rule overrides derived from SERENECODE.md content."""
    contract_requirements = _override_contract_config(content, config)
    type_requirements = _override_type_config(content, config)
    error_handling_rules = _override_error_handling_config(content, config)
    loop_recursion_rules = _override_loop_recursion_config(content, config)
    architecture_rules = _override_architecture_config(
        config, type_requirements, error_handling_rules,
    )
    exemptions = ExemptionConfig(
        exempt_paths=(
            config.exemptions.exempt_paths
            if exempt_paths is None
            else exempt_paths
        ),
    )
    code_quality_rules = _override_code_quality_config(content, config)

    return SerenecodeConfig(
        contract_requirements=contract_requirements,
        type_requirements=type_requirements,
        architecture_rules=architecture_rules,
        error_handling_rules=error_handling_rules,
        loop_recursion_rules=loop_recursion_rules,
        naming_conventions=config.naming_conventions,
        exemptions=exemptions,
        code_quality_rules=code_quality_rules,
        module_health=config.module_health,
        template_name=config.template_name,
        recommended_level=config.recommended_level,
    )


def _override_contract_config(content: str, config: SerenecodeConfig) -> ContractConfig:
    """Derive ContractConfig overrides from SERENECODE.md content."""
    return ContractConfig(
        require_on_public_functions=config.contract_requirements.require_on_public_functions,
        require_on_classes=_matches_rule(
            content,
            r"Every class[^\n]*MUST[^\n]*@icontract\.invariant",
            config.contract_requirements.require_on_classes,
        ),
        require_description_strings=_matches_rule(
            content,
            r"Every `@icontract\.require` and `@icontract\.ensure` MUST include a human-readable description string",
            config.contract_requirements.require_description_strings,
        ),
        require_on_private=_matches_rule(
            content,
            r"Private(?:/Helper)?\s+Functions?[^\n]*MUST\s+have\s+contracts|Private(?: functions|/Helper Functions)[^\n]*MUST\s+have\s+contracts",
            config.contract_requirements.require_on_private,
        ),
    )


def _override_type_config(content: str, config: SerenecodeConfig) -> TypeConfig:
    """Derive TypeConfig overrides from SERENECODE.md content."""
    return TypeConfig(
        require_annotations=config.type_requirements.require_annotations,
        forbid_any_in_core=_matches_rule(
            content,
            r"No use of `Any` in core modules|No use of `Any` anywhere",
            config.type_requirements.forbid_any_in_core,
        ),
        require_parameterized_generics=_matches_rule(
            content,
            r"Generic types must be fully parameterized",
            config.type_requirements.require_parameterized_generics,
        ),
    )


def _override_error_handling_config(content: str, config: SerenecodeConfig) -> ErrorHandlingConfig:
    """Derive ErrorHandlingConfig overrides from SERENECODE.md content."""
    require_domain_exceptions = _matches_rule(
        content,
        r"Core domain functions raise domain-specific exceptions",
        config.error_handling_rules.require_domain_exceptions,
    )
    forbidden_exception_types = _extract_forbidden_exception_types(content)
    forbid_silent_exception_handling = _matches_rule(
        content,
        r"(?:silent|silently)[^\n]*except|except[^\n]*(?:silent|silently)|"
        r"silent\s+exception\s+handling",
        config.error_handling_rules.forbid_silent_exception_handling,
    )
    return ErrorHandlingConfig(
        require_domain_exceptions=require_domain_exceptions,
        forbidden_exception_types=(
            forbidden_exception_types
            if forbidden_exception_types is not None
            else config.error_handling_rules.forbidden_exception_types
            if require_domain_exceptions
            else ()
        ),
        forbid_silent_exception_handling=forbid_silent_exception_handling,
    )


def _override_loop_recursion_config(content: str, config: SerenecodeConfig) -> LoopRecursionConfig:
    """Derive LoopRecursionConfig overrides from SERENECODE.md content."""
    return LoopRecursionConfig(
        require_loop_invariant_comments=_matches_rule(
            content,
            r"[Ll]oops? MUST include (?:a comment describing the loop invariant|invariant comments)",
            config.loop_recursion_rules.require_loop_invariant_comments,
        ),
        require_recursion_variant_comments=_matches_rule(
            content,
            r"Recursive functions MUST include a comment documenting the variant|Recursive functions MUST document the variant",
            config.loop_recursion_rules.require_recursion_variant_comments,
        ),
    )


def _override_architecture_config(
    config: SerenecodeConfig,
    type_requirements: TypeConfig,
    error_handling_rules: ErrorHandlingConfig,
) -> ArchitectureConfig:
    """Derive ArchitectureConfig, filling core patterns when needed."""
    needs_core_patterns = (
        (
            type_requirements.forbid_any_in_core
            or error_handling_rules.require_domain_exceptions
        )
        and len(config.architecture_rules.core_module_patterns) == 0
    )
    return ArchitectureConfig(
        forbidden_imports_in_core=config.architecture_rules.forbidden_imports_in_core,
        core_module_patterns=(
            _DEFAULT_CORE_PATTERNS
            if needs_core_patterns
            else config.architecture_rules.core_module_patterns
        ),
    )


def _override_code_quality_config(content: str, config: SerenecodeConfig) -> CodeQualityConfig:
    """Derive CodeQualityConfig overrides from SERENECODE.md content.

    Each rule activates only on an imperative statement (MUST/forbid/required/
    MUST NOT) so the descriptive prose in the Code Quality Standards section
    doesn't accidentally turn rules on or off.
    """
    return CodeQualityConfig(
        forbid_stub_residue=_matches_rule(
            content,
            r"(?:MUST(?: NOT)?|forbid)[^\n]*stub (?:residue|bod(?:y|ies))",
            config.code_quality_rules.forbid_stub_residue,
        ),
        forbid_mutable_default_arguments=_matches_rule(
            content,
            r"(?:MUST(?: NOT)?|forbid)[^\n]*mutable default",
            config.code_quality_rules.forbid_mutable_default_arguments,
        ),
        forbid_bare_asserts_outside_tests=_matches_rule(
            content,
            r"(?:MUST(?: NOT)?|forbid)[^\n]*bare assert|bare asserts?[^\n]*MUST NOT",
            config.code_quality_rules.forbid_bare_asserts_outside_tests,
        ),
        forbid_print_in_core=_matches_rule(
            content,
            r"(?:MUST(?: NOT)?|forbid)[^\n]*print\s*\(",
            config.code_quality_rules.forbid_print_in_core,
        ),
        forbid_dangerous_calls=_matches_rule(
            content,
            r"(?:MUST(?: NOT)?|forbid)[^\n]*(?:eval|exec|pickle\.loads|os\.system|shell\s*=\s*True)",
            config.code_quality_rules.forbid_dangerous_calls,
        ),
        forbid_todo_comments=_matches_rule(
            content,
            r"(?:MUST(?: NOT)?|forbid)[^\n]*(?:TODO|FIXME|XXX|HACK)",
            config.code_quality_rules.forbid_todo_comments,
        ),
        require_test_assertions=_matches_rule(
            content,
            r"tests? (?:must|MUST) (?:contain|have) (?:at least one\s+)?assert(?:ion)?",
            config.code_quality_rules.require_test_assertions,
        ),
        forbid_isinstance_tautology=_matches_rule(
            content,
            r"(?:MUST(?: NOT)?|forbid)[^\n]*tautological\s+isinstance",
            config.code_quality_rules.forbid_isinstance_tautology,
        ),
        forbid_unused_parameters=_matches_rule(
            content,
            r"(?:MUST(?: NOT)?|forbid)[^\n]*unused (?:function )?parameters?",
            config.code_quality_rules.forbid_unused_parameters,
        ),
    )


@icontract.require(
    lambda content: isinstance(content, str),
    "content must be a string",
)
@icontract.require(
    lambda pattern: isinstance(pattern, str),
    "pattern must be a string",
)
@icontract.ensure(
    lambda default, result: not default or result,
    "when default is True, result must also be True (rules can only be activated, not deactivated)",
)
def _matches_rule(content: str, pattern: str, default: bool) -> bool:
    """Return a parsed rule value when the file mentions the rule, else keep default.

    Matches the pattern to determine whether a rule is mentioned. When the
    rule text is mentioned, returns True. This function only activates rules;
    it cannot deactivate them. The template system (default/strict/minimal)
    controls baseline activation.
    """
    if re.search(pattern, content, re.IGNORECASE):
        return True
    return default


@icontract.require(lambda content: isinstance(content, str), "content must be a string")
@icontract.ensure(lambda result: result is None or isinstance(result, tuple), "result must be a tuple or None")
def _extract_forbidden_exception_types(content: str) -> tuple[str, ...] | None:
    """Extract explicitly forbidden exception type names from SERENECODE.md content."""
    line_match = re.search(r"(?:Never raise bare|never bare)([^\n]+)", content)
    if line_match is None:
        return None

    exception_types = tuple(re.findall(r"`([^`]+)`", line_match.group(0)))
    return exception_types if exception_types else None


@icontract.require(lambda content: isinstance(content, str), "content must be a string")
@icontract.ensure(lambda result: result in ("default", "strict", "minimal"), "result must be a known template")
def _detect_template(content: str) -> str:
    """Detect which template a SERENECODE.md most closely matches.

    First checks for an explicit ``Template: <name>`` declaration.
    Falls back to heuristic detection using section headings and
    rule keywords.

    Args:
        content: SERENECODE.md file content.

    Returns:
        Template name: 'default', 'strict', or 'minimal'.
    """
    # Explicit declaration takes precedence over heuristic detection
    template_match = re.search(
        r"Template:\s*(default|strict|minimal)", content, re.IGNORECASE,
    )
    if template_match:
        return template_match.group(1).lower()

    has_contract_section = "## Contract Standards" in content
    has_architecture_section = "## Architecture Standards" in content
    has_loop_section = "## Loop and Recursion Standards" in content

    if not has_contract_section:
        return "minimal"

    if not has_architecture_section and not has_loop_section:
        return "minimal"

    # Check for strict indicators
    has_private_must = bool(re.search(r"Private.*MUST\s+have\s+contracts", content))
    if has_private_must:
        return "strict"

    return "default"


@icontract.require(lambda content: isinstance(content, str), "content must be a string")
@icontract.ensure(lambda result: result is None or isinstance(result, tuple), "result must be a tuple or None")
def _extract_exemptions(content: str) -> tuple[str, ...] | None:
    """Extract exempt paths from the Exemptions section.

    Args:
        content: SERENECODE.md file content.

    Returns:
        Tuple of exempt path strings, or None if no Exemptions section found.
    """
    exemptions_match = re.search(
        r"## Exemptions\s*\n(.*?)(?:\n## |\Z)",
        content,
        re.DOTALL,
    )
    if not exemptions_match:
        return None

    section = exemptions_match.group(1)
    paths: list[str] = []

    # Loop invariant: paths contains all exempt paths found in lines[0..i]
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("-"):
            continue
        # Match backtick-quoted path-like values at the start of bullet text.
        # The path must be the first backtick content and look like a file/dir
        # reference (contains a dot or ends with /).
        path_match = re.match(r"-\s+`([^`]+)`", stripped)
        if path_match:
            candidate = path_match.group(1)
            if "." in candidate or candidate.endswith("/"):
                paths.append(candidate)

    if not paths:
        return None

    return tuple(paths)


@icontract.require(
    lambda path: isinstance(path, str),
    "path must be a string",
)
@icontract.ensure(
    lambda result: isinstance(result, tuple),
    "result must be a tuple",
)
def _path_segments(path: str) -> tuple[str, ...]:
    """Normalize a path-like string into slash-separated segments."""
    normalized = path.replace("\\", "/").strip("/")
    if not normalized:
        return ()
    return tuple(segment for segment in normalized.split("/") if segment and segment != ".")


@icontract.require(
    lambda module_path: isinstance(module_path, str),
    "module_path must be a string",
)
@icontract.require(
    lambda pattern: isinstance(pattern, str),
    "pattern must be a string",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _path_pattern_matches(module_path: str, pattern: str) -> bool:
    """Check whether a configured path pattern matches a module path by segments.

    Matching is segment-based (not substring): ``"cli.py"`` matches any file
    whose last segment is ``cli.py``, and ``"adapters/"`` matches any
    directory segment named ``adapters``. This is by design for single-project
    repos. In monorepo layouts with duplicate directory names, use longer
    path patterns (e.g. ``"billing/adapters/"``) for disambiguation.
    """
    module_segments = _path_segments(module_path)
    pattern_segments = _path_segments(pattern)
    if not module_segments or not pattern_segments:
        return False

    if pattern.endswith(("/", "\\")):
        window_size = len(pattern_segments)
        if window_size > len(module_segments):
            return False

        # Loop invariant: no prior window of module_segments matched pattern_segments.
        for index in range(len(module_segments) - window_size + 1):
            if module_segments[index:index + window_size] == pattern_segments:
                return True
        return False

    if len(pattern_segments) == 1:
        return module_segments[-1] == pattern_segments[0]

    window_size = len(pattern_segments)
    if window_size > len(module_segments):
        return False

    # Loop invariant: no prior window of module_segments matched pattern_segments.
    for index in range(len(module_segments) - window_size + 1):
        if module_segments[index:index + window_size] == pattern_segments:
            return True
    return False


@icontract.require(
    lambda module_path: isinstance(module_path, str),
    "module_path must be a string",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a boolean",
)
@icontract.ensure(
    lambda module_path, config, result: not result or len(config.architecture_rules.core_module_patterns) > 0,
    "a module can only be core if core patterns are configured",
)
def is_core_module(module_path: str, config: SerenecodeConfig) -> bool:
    """Check whether a module path matches any core module pattern.

    Args:
        module_path: The file path or module path to check.
        config: The active configuration.

    Returns:
        True if the module is considered a core module.
    """
    # Loop invariant: result is True if any pattern in patterns[0..i] matches
    for pattern in config.architecture_rules.core_module_patterns:
        if _path_pattern_matches(module_path, pattern):
            return True
    return False


@icontract.require(
    lambda module_path: isinstance(module_path, str),
    "module_path must be a string",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a boolean",
)
@icontract.ensure(
    lambda module_path, config, result: not result or len(config.exemptions.exempt_paths) > 0,
    "a module can only be exempt if exempt paths are configured",
)
def is_exempt_module(module_path: str, config: SerenecodeConfig) -> bool:
    """Check whether a module path is exempt from full verification.

    Args:
        module_path: The file path or module path to check.
        config: The active configuration.

    Returns:
        True if the module is exempt.
    """
    # Loop invariant: result is True if any path in exempt_paths[0..i] matches
    for exempt_path in config.exemptions.exempt_paths:
        if _path_pattern_matches(module_path, exempt_path):
            return True
    return False
