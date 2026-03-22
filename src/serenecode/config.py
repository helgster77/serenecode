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


@icontract.invariant(
    lambda self: 1 <= self.recommended_level <= 5,
    "Recommended level must be between 1 and 5",
)
@icontract.invariant(
    lambda self: self.template_name in ("default", "strict", "minimal"),
    "Template name must be a recognized template",
)
@dataclass(frozen=True)
class SerenecodeConfig:
    """Complete Serenecode configuration parsed from SERENECODE.md.

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
    "templates/",
    "tests/fixtures/",
    "ports/",
    "exceptions.py",
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
    lambda result: isinstance(result, SerenecodeConfig),
    "result must be a SerenecodeConfig",
)
def default_config() -> SerenecodeConfig:
    """Return the default Serenecode configuration.

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
            require_domain_exceptions=True,
            forbidden_exception_types=_DEFAULT_FORBIDDEN_EXCEPTIONS,
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
            exempt_paths=_DEFAULT_EXEMPT_PATHS,
        ),
        template_name="default",
        recommended_level=3,
    )


@icontract.ensure(
    lambda result: isinstance(result, SerenecodeConfig),
    "result must be a SerenecodeConfig",
)
def strict_config() -> SerenecodeConfig:
    """Return the strict Serenecode configuration.

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
        template_name="strict",
        recommended_level=5,
    )


@icontract.ensure(
    lambda result: isinstance(result, SerenecodeConfig),
    "result must be a SerenecodeConfig",
)
def minimal_config() -> SerenecodeConfig:
    """Return the minimal Serenecode configuration.

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
        template_name="minimal",
        recommended_level=2,
    )


@icontract.require(
    lambda template_name: template_name in ("default", "strict", "minimal"),
    "template_name must be 'default', 'strict', or 'minimal'",
)
@icontract.ensure(
    lambda result: isinstance(result, SerenecodeConfig),
    "result must be a SerenecodeConfig",
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
    lambda result: isinstance(result, SerenecodeConfig),
    "result must be a SerenecodeConfig",
)
def parse_serenecode_md(content: str) -> SerenecodeConfig:
    """Parse a SERENECODE.md file content into a SerenecodeConfig.

    Phase 1 implementation: detects which template the content most
    closely matches by looking for key section headings, and extracts
    exemption paths from the Exemptions section.

    Args:
        content: The full text content of a SERENECODE.md file.

    Returns:
        A SerenecodeConfig matching the detected template with
        any extracted exemption overrides.
    """
    template = _detect_template(content)
    config = config_for_template(template)
    exempt_paths = _extract_exemptions(content)

    if exempt_paths is not None:
        config = SerenecodeConfig(
            contract_requirements=config.contract_requirements,
            type_requirements=config.type_requirements,
            architecture_rules=config.architecture_rules,
            error_handling_rules=config.error_handling_rules,
            loop_recursion_rules=config.loop_recursion_rules,
            naming_conventions=config.naming_conventions,
            exemptions=ExemptionConfig(exempt_paths=exempt_paths),
            template_name=config.template_name,
            recommended_level=config.recommended_level,
        )

    return config


@icontract.require(lambda content: isinstance(content, str), "content must be a string")
@icontract.ensure(lambda result: result in ("default", "strict", "minimal"), "result must be a known template")
def _detect_template(content: str) -> str:
    """Detect which template a SERENECODE.md most closely matches.

    Uses the presence of key section headings and rule keywords.

    Args:
        content: SERENECODE.md file content.

    Returns:
        Template name: 'default', 'strict', or 'minimal'.
    """
    has_contract_section = "## Contract Standards" in content
    has_architecture_section = "## Architecture Standards" in content
    has_loop_section = "## Loop and Recursion Standards" in content

    if not has_contract_section:
        return "minimal"

    if not has_architecture_section and not has_loop_section:
        return "minimal"

    # Check for strict indicators
    has_private_must = bool(re.search(r"Private.*MUST\s+have\s+contracts", content))
    has_no_exemptions = "## Exemptions" not in content

    if has_private_must and has_no_exemptions:
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
        # Look for backtick-quoted paths in bullet points
        path_match = re.search(r"`([^`]+)`", line)
        if path_match and line.strip().startswith("-"):
            paths.append(path_match.group(1))

    if not paths:
        return None

    return tuple(paths)


@icontract.require(
    lambda module_path: isinstance(module_path, str),
    "module_path must be a string",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a boolean",
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
        if pattern in module_path:
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
        if exempt_path in module_path:
            return True
    return False
