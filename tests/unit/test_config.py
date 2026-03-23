"""Tests for Serenecode configuration parser and config models."""

from __future__ import annotations

import pytest

from serenecode.config import (
    config_for_template,
    default_config,
    is_core_module,
    is_exempt_module,
    minimal_config,
    parse_serenecode_md,
    strict_config,
)


class TestDefaultConfig:
    """Tests for the default configuration."""

    def test_template_name(self) -> None:
        config = default_config()
        assert config.template_name == "default"

    def test_requires_public_contracts(self) -> None:
        config = default_config()
        assert config.contract_requirements.require_on_public_functions is True

    def test_requires_class_invariants(self) -> None:
        config = default_config()
        assert config.contract_requirements.require_on_classes is True

    def test_requires_description_strings(self) -> None:
        config = default_config()
        assert config.contract_requirements.require_description_strings is True

    def test_does_not_require_private_contracts(self) -> None:
        config = default_config()
        assert config.contract_requirements.require_on_private is False

    def test_forbids_any_in_core(self) -> None:
        config = default_config()
        assert config.type_requirements.forbid_any_in_core is True

    def test_forbidden_imports(self) -> None:
        config = default_config()
        assert "os" in config.architecture_rules.forbidden_imports_in_core
        assert "pathlib" in config.architecture_rules.forbidden_imports_in_core
        assert "subprocess" in config.architecture_rules.forbidden_imports_in_core

    def test_core_patterns(self) -> None:
        config = default_config()
        assert "core/" in config.architecture_rules.core_module_patterns
        assert "checker/" in config.architecture_rules.core_module_patterns
        assert "models.py" in config.architecture_rules.core_module_patterns

    def test_has_exemptions(self) -> None:
        config = default_config()
        assert len(config.exemptions.exempt_paths) > 0
        assert "cli.py" in config.exemptions.exempt_paths
        assert "adapters/" in config.exemptions.exempt_paths


class TestStrictConfig:
    """Tests for the strict configuration."""

    def test_template_name(self) -> None:
        config = strict_config()
        assert config.template_name == "strict"

    def test_requires_private_contracts(self) -> None:
        config = strict_config()
        assert config.contract_requirements.require_on_private is True

    def test_no_exemptions(self) -> None:
        config = strict_config()
        assert len(config.exemptions.exempt_paths) == 0


class TestMinimalConfig:
    """Tests for the minimal configuration."""

    def test_template_name(self) -> None:
        config = minimal_config()
        assert config.template_name == "minimal"

    def test_no_class_invariants_required(self) -> None:
        config = minimal_config()
        assert config.contract_requirements.require_on_classes is False

    def test_no_description_strings_required(self) -> None:
        config = minimal_config()
        assert config.contract_requirements.require_description_strings is False

    def test_any_allowed_everywhere(self) -> None:
        config = minimal_config()
        assert config.type_requirements.forbid_any_in_core is False

    def test_no_forbidden_imports(self) -> None:
        config = minimal_config()
        assert len(config.architecture_rules.forbidden_imports_in_core) == 0


class TestConfigForTemplate:
    """Tests for config_for_template lookup function."""

    @pytest.mark.parametrize("name", ["default", "strict", "minimal"])
    def test_returns_correct_template(self, name: str) -> None:
        config = config_for_template(name)
        assert config.template_name == name

    def test_invalid_template_raises(self) -> None:
        with pytest.raises(Exception):
            config_for_template("nonexistent")


class TestParseSerenecodeMd:
    """Tests for SERENECODE.md parsing."""

    def test_detects_default_template(self) -> None:
        content = """# SERENECODE.md

## Contract Standards
Every public function MUST have contracts.

## Architecture Standards
Hexagonal architecture.

## Loop and Recursion Standards
Loops MUST have invariants.

## Exemptions
- `cli.py` — Thin CLI layer.
- `adapters/` — I/O boundary code.
"""
        config = parse_serenecode_md(content)
        assert config.template_name == "default"

    def test_detects_minimal_template(self) -> None:
        content = """# SERENECODE.md

Just some basic rules, nothing formal.
"""
        config = parse_serenecode_md(content)
        assert config.template_name == "minimal"

    def test_extracts_exemptions(self) -> None:
        content = """# SERENECODE.md

## Contract Standards
Rules here.

## Architecture Standards
More rules.

## Loop and Recursion Standards
Loop rules.

## Exemptions
- `cli.py` — Thin CLI layer.
- `adapters/` — I/O boundary code.
- `scripts/` — One-off scripts.
"""
        config = parse_serenecode_md(content)
        assert "cli.py" in config.exemptions.exempt_paths
        assert "adapters/" in config.exemptions.exempt_paths
        assert "scripts/" in config.exemptions.exempt_paths

    def test_strict_with_exemptions_keeps_private_contract_requirement(self) -> None:
        content = """# SERENECODE.md

## Contract Standards
Private functions MUST have contracts.

## Architecture Standards
Hexagonal architecture.

## Exemptions
- `cli.py` — Thin CLI layer.
"""
        config = parse_serenecode_md(content)
        assert config.template_name == "strict"
        assert config.contract_requirements.require_on_private is True
        assert config.exemptions.exempt_paths == ("cli.py",)

    def test_minimal_override_extracts_forbidden_exception_types(self) -> None:
        content = """# SERENECODE.md

## Contract Standards
Public functions SHOULD have contracts.

## Error Handling Standards
Core domain functions raise domain-specific exceptions and never bare `Exception`, `ValueError`, or `TypeError`.
"""
        config = parse_serenecode_md(content)
        assert config.template_name == "minimal"
        assert config.error_handling_rules.require_domain_exceptions is True
        assert config.error_handling_rules.forbidden_exception_types == (
            "Exception",
            "ValueError",
            "TypeError",
        )

    def test_minimal_core_overrides_restore_core_patterns(self) -> None:
        content = """# SERENECODE.md

## Contract Standards
Public functions SHOULD have contracts.

## Type Standards
No use of `Any` in core modules.
"""
        config = parse_serenecode_md(content)
        assert config.template_name == "minimal"
        assert config.type_requirements.forbid_any_in_core is True
        assert "core/" in config.architecture_rules.core_module_patterns

    def test_minimal_domain_exception_override_restores_core_patterns(self) -> None:
        content = """# SERENECODE.md

## Contract Standards
Public functions SHOULD have contracts.

## Error Handling Standards
Core domain functions raise domain-specific exceptions.
"""
        config = parse_serenecode_md(content)
        assert config.template_name == "minimal"
        assert config.error_handling_rules.require_domain_exceptions is True
        assert "core/" in config.architecture_rules.core_module_patterns

    def test_empty_content_returns_minimal(self) -> None:
        config = parse_serenecode_md("")
        assert config.template_name == "minimal"


class TestIsCoreModule:
    """Tests for is_core_module helper."""

    def test_core_directory(self) -> None:
        config = default_config()
        assert is_core_module("src/serenecode/core/engine.py", config) is True

    def test_checker_directory(self) -> None:
        config = default_config()
        assert is_core_module("src/serenecode/checker/structural.py", config) is True

    def test_models_file(self) -> None:
        config = default_config()
        assert is_core_module("src/serenecode/models.py", config) is True

    def test_adapter_not_core(self) -> None:
        config = default_config()
        assert is_core_module("src/serenecode/adapters/local_fs.py", config) is False

    def test_cli_not_core(self) -> None:
        config = default_config()
        assert is_core_module("src/serenecode/cli.py", config) is False

    def test_minimal_config_nothing_is_core(self) -> None:
        config = minimal_config()
        assert is_core_module("src/serenecode/core/engine.py", config) is False

    def test_core_matching_is_segment_aware(self) -> None:
        config = default_config()
        assert is_core_module("src/domain/viewmodels.py", config) is False
        assert is_core_module("src/domain/models.py", config) is True


class TestIsExemptModule:
    """Tests for is_exempt_module helper."""

    def test_cli_is_exempt(self) -> None:
        config = default_config()
        assert is_exempt_module("src/serenecode/cli.py", config) is True

    def test_adapters_are_exempt(self) -> None:
        config = default_config()
        assert is_exempt_module("src/serenecode/adapters/local_fs.py", config) is True

    def test_core_not_exempt(self) -> None:
        config = default_config()
        assert is_exempt_module("src/serenecode/core/engine.py", config) is False

    def test_strict_nothing_exempt(self) -> None:
        config = strict_config()
        assert is_exempt_module("src/serenecode/cli.py", config) is False

    def test_exempt_matching_is_segment_aware(self) -> None:
        config = default_config()
        assert is_exempt_module("src/domain/notcli.py", config) is False
        assert is_exempt_module("src/domain/real_cli.py", config) is False
