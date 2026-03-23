"""Tests for the Level 5 compositional verification checker."""

from __future__ import annotations

import textwrap

import icontract
import pytest

from serenecode.checker.compositional import (
    ClassInfo,
    FunctionInfo,
    MethodSignature,
    ModuleInfo,
    ParameterInfo,
    ProtocolInfo,
    check_assume_guarantee,
    check_circular_dependencies,
    check_compositional,
    check_contract_completeness,
    check_data_flow,
    check_dependency_direction,
    check_interface_compliance,
    check_system_invariants,
    parse_module_info,
)
from serenecode.config import default_config, minimal_config, strict_config
from serenecode.models import CheckStatus
from tests.conftest import icontract_enabled


class TestParseModuleInfo:
    """Tests for module info parsing."""

    def test_parses_imports(self) -> None:
        source = textwrap.dedent("""\
            import os
            import ast
            from pathlib import Path
        """)
        info = parse_module_info(source, "test.py", "test.py")
        assert "os" in info.imports
        assert "ast" in info.imports
        assert ("pathlib", "Path") in info.from_imports

    def test_parses_import_alias_bindings(self) -> None:
        source = textwrap.dedent("""\
            import core.helpers as helpers
            from core.validators import validate as guarded
        """)
        info = parse_module_info(source, "test.py", "test.py")
        assert ("helpers", "core.helpers", None) in info.import_bindings
        assert ("guarded", "core.validators", "validate") in info.import_bindings

    def test_parses_classes(self) -> None:
        source = textwrap.dedent("""\
            class MyClass:
                def method_a(self):
                    pass
                def method_b(self):
                    pass
        """)
        info = parse_module_info(source, "test.py", "test.py")
        assert len(info.classes) == 1
        assert info.classes[0].name == "MyClass"
        assert "method_a" in info.classes[0].methods
        assert "method_b" in info.classes[0].methods

    def test_parses_protocols(self) -> None:
        source = textwrap.dedent("""\
            from typing import Protocol

            class FileReader(Protocol):
                def read_file(self, path: str) -> str:
                    ...
                def file_exists(self, path: str) -> bool:
                    ...
        """)
        info = parse_module_info(source, "ports/file_system.py", "ports/file_system.py")
        assert len(info.protocols) == 1
        assert info.protocols[0].name == "FileReader"
        assert len(info.protocols[0].methods) == 2

    def test_parses_functions(self) -> None:
        source = textwrap.dedent("""\
            def public_func():
                pass
            def _private_func():
                pass
        """)
        info = parse_module_info(source, "test.py", "test.py")
        assert "public_func" in info.functions
        assert "_private_func" not in info.functions

    def test_resolves_relative_from_imports(self) -> None:
        source = textwrap.dedent("""\
            from .helper import util
            from ..shared import common
        """)
        info = parse_module_info(source, "pkg/sub/module.py", "pkg/sub/module.py")
        assert ("pkg.sub.helper", "util") in info.from_imports
        assert ("pkg.shared", "common") in info.from_imports

    def test_handles_syntax_error(self) -> None:
        info = parse_module_info("def broken(:", "test.py", "test.py")
        assert len(info.imports) == 0
        assert len(info.classes) == 0

    def test_handles_null_byte_source(self) -> None:
        info = parse_module_info("\x00", "test.py", "test.py")
        assert len(info.imports) == 0
        assert len(info.classes) == 0

    def test_rejects_invalid_module_path(self) -> None:
        if icontract_enabled():
            with pytest.raises(icontract.ViolationError):
                parse_module_info("pass\n", "test.py", "\x00")
        else:
            pytest.skip("icontract preconditions are disabled")


class TestParseModuleInfoEnhanced:
    """Tests for enhanced module info parsing (FunctionInfo, contracts, calls)."""

    def test_parses_function_info_with_contracts(self) -> None:
        source = textwrap.dedent("""\
            import icontract

            @icontract.require(lambda x: x > 0, "x must be positive")
            @icontract.ensure(lambda result: result >= 0, "result non-negative")
            def compute(x: int) -> int:
                return x * 2
        """)
        info = parse_module_info(source, "test.py", "test.py")
        assert len(info.function_infos) == 1
        fi = info.function_infos[0]
        assert fi.name == "compute"
        assert fi.is_public is True
        assert fi.has_require is True
        assert fi.has_ensure is True
        assert len(fi.parameters) == 1
        assert fi.parameters[0].name == "x"
        assert fi.parameters[0].annotation == "int"
        assert fi.return_annotation == "int"

    def test_parses_function_without_contracts(self) -> None:
        source = textwrap.dedent("""\
            def bare_func(a: str, b: int) -> bool:
                return len(a) > b
        """)
        info = parse_module_info(source, "test.py", "test.py")
        assert len(info.function_infos) == 1
        fi = info.function_infos[0]
        assert fi.has_require is False
        assert fi.has_ensure is False

    def test_extracts_call_targets(self) -> None:
        source = textwrap.dedent("""\
            from other_module import helper

            def caller():
                helper()
                result = some_module.do_thing()
        """)
        info = parse_module_info(source, "test.py", "test.py")
        fi = info.function_infos[0]
        assert "helper" in fi.calls
        assert "some_module.do_thing" in fi.calls

    def test_parses_class_method_signatures(self) -> None:
        source = textwrap.dedent("""\
            class MyClass:
                def method_a(self, x: int) -> str:
                    pass
                def method_b(self, y: str) -> bool:
                    pass
        """)
        info = parse_module_info(source, "test.py", "test.py")
        cls = info.classes[0]
        assert len(cls.method_signatures) == 2
        sig_a = next(s for s in cls.method_signatures if s.name == "method_a")
        assert sig_a.parameters == ("x",)
        assert sig_a.has_return_annotation is True
        assert sig_a.required_parameters == 1
        assert sig_a.return_annotation == "str"

    def test_detects_class_invariant(self) -> None:
        source = textwrap.dedent("""\
            import icontract
            from dataclasses import dataclass

            @icontract.invariant(lambda self: True, "always valid")
            @dataclass(frozen=True)
            class Config:
                name: str
        """)
        info = parse_module_info(source, "test.py", "test.py")
        assert len(info.classes) == 1
        assert info.classes[0].has_invariant is True

    def test_class_without_invariant(self) -> None:
        source = textwrap.dedent("""\
            class PlainClass:
                pass
        """)
        info = parse_module_info(source, "test.py", "test.py")
        assert info.classes[0].has_invariant is False


class TestCheckDependencyDirection:
    """Tests for dependency direction checking."""

    def test_core_importing_adapter_fails(self) -> None:
        modules = [
            ModuleInfo(
                file_path="src/serenecode/core/engine.py",
                module_path="core/engine.py",
                imports=("serenecode.adapters.local_fs",),
                from_imports=(),
                classes=(),
                functions=(),
                protocols=(),
            ),
        ]
        results = check_dependency_direction(modules, default_config())
        assert len(results) >= 1
        assert results[0].status == CheckStatus.FAILED
        assert "adapters" in results[0].details[0].message.lower()

    def test_core_importing_stdlib_passes(self) -> None:
        modules = [
            ModuleInfo(
                file_path="src/serenecode/core/engine.py",
                module_path="core/engine.py",
                imports=("ast", "re", "dataclasses"),
                from_imports=(),
                classes=(),
                functions=(),
                protocols=(),
            ),
        ]
        results = check_dependency_direction(modules, default_config())
        assert len(results) == 0

    def test_port_importing_adapter_fails(self) -> None:
        modules = [
            ModuleInfo(
                file_path="src/serenecode/ports/fs.py",
                module_path="ports/fs.py",
                imports=("serenecode.adapters.local_fs",),
                from_imports=(),
                classes=(),
                functions=(),
                protocols=(),
            ),
        ]
        results = check_dependency_direction(modules, default_config())
        assert len(results) >= 1

    def test_adapter_importing_anything_passes(self) -> None:
        modules = [
            ModuleInfo(
                file_path="src/serenecode/adapters/local_fs.py",
                module_path="adapters/local_fs.py",
                imports=("os", "pathlib"),
                from_imports=(),
                classes=(),
                functions=(),
                protocols=(),
            ),
        ]
        results = check_dependency_direction(modules, default_config())
        assert len(results) == 0

    def test_exempt_module_skipped(self) -> None:
        modules = [
            ModuleInfo(
                file_path="src/serenecode/cli.py",
                module_path="cli.py",
                imports=("serenecode.adapters.local_fs",),
                from_imports=(),
                classes=(),
                functions=(),
                protocols=(),
            ),
        ]
        results = check_dependency_direction(modules, default_config())
        assert len(results) == 0


class TestDependencyDirectionFixes:
    """Tests for the cli substring false-positive fix."""

    def test_click_import_not_flagged(self) -> None:
        modules = [
            ModuleInfo(
                file_path="src/serenecode/core/engine.py",
                module_path="core/engine.py",
                imports=("click",),
                from_imports=(),
                classes=(),
                functions=(),
                protocols=(),
            ),
        ]
        results = check_dependency_direction(modules, default_config())
        assert len(results) == 0

    def test_actual_cli_import_flagged(self) -> None:
        modules = [
            ModuleInfo(
                file_path="src/serenecode/core/engine.py",
                module_path="core/engine.py",
                imports=("serenecode.cli",),
                from_imports=(),
                classes=(),
                functions=(),
                protocols=(),
            ),
        ]
        results = check_dependency_direction(modules, default_config())
        assert len(results) >= 1

    def test_transports_directory_not_treated_as_ports(self) -> None:
        modules = [
            ModuleInfo(
                file_path="src/transports/client.py",
                module_path="transports/client.py",
                imports=("serenecode.adapters.local_fs",),
                from_imports=(),
                classes=(),
                functions=(),
                protocols=(),
            ),
        ]
        results = check_dependency_direction(modules, default_config())
        assert len(results) == 0

    def test_pycli_import_not_flagged(self) -> None:
        modules = [
            ModuleInfo(
                file_path="src/serenecode/core/engine.py",
                module_path="core/engine.py",
                imports=("pycli",),
                from_imports=(),
                classes=(),
                functions=(),
                protocols=(),
            ),
        ]
        results = check_dependency_direction(modules, default_config())
        assert len(results) == 0


class TestCheckInterfaceCompliance:
    """Tests for interface compliance checking."""

    def test_complete_implementation_passes(self) -> None:
        modules = [
            ModuleInfo(
                file_path="ports/fs.py",
                module_path="ports/fs.py",
                imports=(),
                from_imports=(),
                classes=(),
                functions=(),
                protocols=(
                    ProtocolInfo(
                        name="FileReader",
                        line=5,
                        methods=(
                            MethodSignature(name="read_file", parameters=("path",), has_return_annotation=True),
                            MethodSignature(name="file_exists", parameters=("path",), has_return_annotation=True),
                        ),
                    ),
                ),
            ),
            ModuleInfo(
                file_path="adapters/local_fs.py",
                module_path="adapters/local_fs.py",
                imports=(),
                from_imports=(),
                classes=(
                    ClassInfo(
                        name="LocalFileReader",
                        line=10,
                        bases=(),
                        methods=("read_file", "file_exists", "list_python_files"),
                        is_protocol=False,
                        method_signatures=(
                            MethodSignature(name="read_file", parameters=("path",), has_return_annotation=True),
                            MethodSignature(name="file_exists", parameters=("path",), has_return_annotation=True),
                            MethodSignature(name="list_python_files", parameters=("directory",), has_return_annotation=True),
                        ),
                    ),
                ),
                functions=(),
                protocols=(),
            ),
        ]
        results = check_interface_compliance(modules, default_config())
        assert len(results) == 0

    def test_missing_method_detected(self) -> None:
        modules = [
            ModuleInfo(
                file_path="ports/fs.py",
                module_path="ports/fs.py",
                imports=(),
                from_imports=(),
                classes=(),
                functions=(),
                protocols=(
                    ProtocolInfo(
                        name="FileReader",
                        line=5,
                        methods=(
                            MethodSignature(name="read_file", parameters=("path",), has_return_annotation=True),
                            MethodSignature(name="file_exists", parameters=("path",), has_return_annotation=True),
                            MethodSignature(name="list_python_files", parameters=("directory",), has_return_annotation=True),
                        ),
                    ),
                ),
            ),
            ModuleInfo(
                file_path="adapters/local_fs.py",
                module_path="adapters/local_fs.py",
                imports=(),
                from_imports=(),
                classes=(
                    ClassInfo(
                        name="LocalFileReader",
                        line=10,
                        bases=(),
                        methods=("read_file",),
                        is_protocol=False,
                        method_signatures=(
                            MethodSignature(name="read_file", parameters=("path",), has_return_annotation=True),
                        ),
                    ),
                ),
                functions=(),
                protocols=(),
            ),
        ]
        results = check_interface_compliance(modules, default_config())
        assert len(results) >= 1
        missing_methods = {r.details[0].message for r in results}
        assert any("file_exists" in m for m in missing_methods)

    def test_explicit_protocol_inheritance_triggers_missing_method_check(self) -> None:
        modules = [
            ModuleInfo(
                file_path="ports/reader.py",
                module_path="ports/reader.py",
                imports=(),
                from_imports=(),
                classes=(),
                functions=(),
                protocols=(
                    ProtocolInfo(
                        name="ReadableProtocol",
                        line=5,
                        methods=(
                            MethodSignature(
                                name="read",
                                parameters=(),
                                has_return_annotation=True,
                                return_annotation="str",
                            ),
                        ),
                    ),
                ),
            ),
            ModuleInfo(
                file_path="adapters/file_io.py",
                module_path="adapters/file_io.py",
                imports=(),
                from_imports=(),
                classes=(
                    ClassInfo(
                        name="FileIO",
                        line=10,
                        bases=("ReadableProtocol",),
                        methods=(),
                        is_protocol=False,
                    ),
                ),
                functions=(),
                protocols=(),
            ),
        ]
        results = check_interface_compliance(modules, default_config())
        assert any("missing method 'read'" in r.details[0].message for r in results)


class TestInterfaceComplianceSignatures:
    """Tests for method signature matching in interface compliance."""

    def test_parameter_count_mismatch_detected(self) -> None:
        modules = [
            ModuleInfo(
                file_path="ports/fs.py",
                module_path="ports/fs.py",
                imports=(),
                from_imports=(),
                classes=(),
                functions=(),
                protocols=(
                    ProtocolInfo(
                        name="FileReader",
                        line=5,
                        methods=(
                            MethodSignature(name="read_file", parameters=("path", "encoding"), has_return_annotation=True),
                        ),
                    ),
                ),
            ),
            ModuleInfo(
                file_path="adapters/local_fs.py",
                module_path="adapters/local_fs.py",
                imports=(),
                from_imports=(),
                classes=(
                    ClassInfo(
                        name="LocalFileReader",
                        line=10,
                        bases=(),
                        methods=("read_file",),
                        is_protocol=False,
                        method_signatures=(
                            MethodSignature(name="read_file", parameters=("path",), has_return_annotation=True),
                        ),
                    ),
                ),
                functions=(),
                protocols=(),
            ),
        ]
        results = check_interface_compliance(modules, default_config())
        assert any("parameters" in r.details[0].message for r in results)

    def test_missing_return_annotation_detected(self) -> None:
        modules = [
            ModuleInfo(
                file_path="ports/fs.py",
                module_path="ports/fs.py",
                imports=(),
                from_imports=(),
                classes=(),
                functions=(),
                protocols=(
                    ProtocolInfo(
                        name="FileReader",
                        line=5,
                        methods=(
                            MethodSignature(name="read_file", parameters=("path",), has_return_annotation=True),
                        ),
                    ),
                ),
            ),
            ModuleInfo(
                file_path="adapters/local_fs.py",
                module_path="adapters/local_fs.py",
                imports=(),
                from_imports=(),
                classes=(
                    ClassInfo(
                        name="LocalFileReader",
                        line=10,
                        bases=(),
                        methods=("read_file",),
                        is_protocol=False,
                        method_signatures=(
                            MethodSignature(name="read_file", parameters=("path",), has_return_annotation=False),
                        ),
                    ),
                ),
                functions=(),
                protocols=(),
            ),
        ]
        results = check_interface_compliance(modules, default_config())
        assert any("return annotation" in r.details[0].message for r in results)

    def test_extra_optional_parameters_allowed(self) -> None:
        modules = [
            ModuleInfo(
                file_path="ports/fs.py",
                module_path="ports/fs.py",
                imports=(),
                from_imports=(),
                classes=(),
                functions=(),
                protocols=(
                    ProtocolInfo(
                        name="FileReader",
                        line=5,
                        methods=(
                            MethodSignature(name="read_file", parameters=("path",), has_return_annotation=True),
                        ),
                    ),
                ),
            ),
            ModuleInfo(
                file_path="adapters/local_fs.py",
                module_path="adapters/local_fs.py",
                imports=(),
                from_imports=(),
                classes=(
                    ClassInfo(
                        name="LocalFileReader",
                        line=10,
                        bases=(),
                        methods=("read_file",),
                        is_protocol=False,
                        method_signatures=(
                            MethodSignature(
                                name="read_file",
                                parameters=("path", "encoding"),
                                has_return_annotation=True,
                                required_parameters=1,
                            ),
                        ),
                    ),
                ),
                functions=(),
                protocols=(),
            ),
        ]
        results = check_interface_compliance(modules, default_config())
        assert len(results) == 0

    def test_extra_required_parameters_detected(self) -> None:
        modules = [
            ModuleInfo(
                file_path="ports/fs.py",
                module_path="ports/fs.py",
                imports=(),
                from_imports=(),
                classes=(),
                functions=(),
                protocols=(
                    ProtocolInfo(
                        name="FileReader",
                        line=5,
                        methods=(
                            MethodSignature(name="read_file", parameters=("path",), has_return_annotation=True),
                        ),
                    ),
                ),
            ),
            ModuleInfo(
                file_path="adapters/local_fs.py",
                module_path="adapters/local_fs.py",
                imports=(),
                from_imports=(),
                classes=(
                    ClassInfo(
                        name="LocalFileReader",
                        line=10,
                        bases=(),
                        methods=("read_file",),
                        is_protocol=False,
                        method_signatures=(
                            MethodSignature(
                                name="read_file",
                                parameters=("path", "encoding"),
                                has_return_annotation=True,
                                required_parameters=2,
                            ),
                        ),
                    ),
                ),
                functions=(),
                protocols=(),
            ),
        ]
        results = check_interface_compliance(modules, default_config())
        assert any("requires only" in r.details[0].message for r in results)

    def test_return_annotation_mismatch_detected(self) -> None:
        modules = [
            ModuleInfo(
                file_path="ports/fs.py",
                module_path="ports/fs.py",
                imports=(),
                from_imports=(),
                classes=(),
                functions=(),
                protocols=(
                    ProtocolInfo(
                        name="FileReader",
                        line=5,
                        methods=(
                            MethodSignature(
                                name="read_file",
                                parameters=("path",),
                                has_return_annotation=True,
                                return_annotation="str",
                            ),
                        ),
                    ),
                ),
            ),
            ModuleInfo(
                file_path="adapters/local_fs.py",
                module_path="adapters/local_fs.py",
                imports=(),
                from_imports=(),
                classes=(
                    ClassInfo(
                        name="LocalFileReader",
                        line=10,
                        bases=(),
                        methods=("read_file",),
                        is_protocol=False,
                        method_signatures=(
                            MethodSignature(
                                name="read_file",
                                parameters=("path",),
                                has_return_annotation=True,
                                return_annotation="bytes",
                            ),
                        ),
                    ),
                ),
                functions=(),
                protocols=(),
            ),
        ]
        results = check_interface_compliance(modules, default_config())
        assert any("return annotation" in r.details[0].message for r in results)


class TestContractCompleteness:
    """Tests for contract completeness checking."""

    def test_function_missing_require_detected(self) -> None:
        source = textwrap.dedent("""\
            import icontract

            @icontract.ensure(lambda result: result >= 0, "non-negative")
            def compute(x: int) -> int:
                return x * 2
        """)
        info = parse_module_info(source, "core/engine.py", "core/engine.py")
        results = check_contract_completeness([info], default_config())
        failed = [r for r in results if r.status == CheckStatus.FAILED]
        assert any("require" in r.details[0].message.lower() for r in failed)

    def test_function_missing_ensure_detected(self) -> None:
        source = textwrap.dedent("""\
            import icontract

            @icontract.require(lambda x: x > 0, "positive")
            def compute(x: int) -> int:
                return x * 2
        """)
        info = parse_module_info(source, "core/engine.py", "core/engine.py")
        results = check_contract_completeness([info], default_config())
        failed = [r for r in results if r.status == CheckStatus.FAILED]
        assert any("ensure" in r.details[0].message.lower() for r in failed)

    def test_class_missing_invariant_detected(self) -> None:
        source = textwrap.dedent("""\
            class PublicClass:
                pass
        """)
        info = parse_module_info(source, "core/engine.py", "core/engine.py")
        results = check_contract_completeness([info], default_config())
        failed = [r for r in results if r.status == CheckStatus.FAILED]
        assert any("invariant" in r.details[0].message.lower() for r in failed)

    def test_fully_contracted_module_passes(self) -> None:
        source = textwrap.dedent("""\
            import icontract
            from dataclasses import dataclass

            @icontract.invariant(lambda self: True, "always valid")
            @dataclass(frozen=True)
            class Config:
                name: str

            @icontract.require(lambda x: x > 0, "positive")
            @icontract.ensure(lambda result: result >= 0, "non-negative")
            def compute(x: int) -> int:
                return x * 2
        """)
        info = parse_module_info(source, "core/engine.py", "core/engine.py")
        results = check_contract_completeness([info], default_config())
        failed = [r for r in results if r.status == CheckStatus.FAILED]
        assert len(failed) == 0

    def test_exempt_module_skipped(self) -> None:
        source = textwrap.dedent("""\
            def no_contracts(x: int) -> int:
                return x
        """)
        info = parse_module_info(source, "adapters/local_fs.py", "adapters/local_fs.py")
        results = check_contract_completeness([info], default_config())
        assert len([r for r in results if r.status == CheckStatus.FAILED]) == 0

    def test_private_functions_not_checked(self) -> None:
        source = textwrap.dedent("""\
            def _private_helper(x: int) -> int:
                return x
        """)
        info = parse_module_info(source, "core/engine.py", "core/engine.py")
        results = check_contract_completeness([info], default_config())
        failed = [r for r in results if r.status == CheckStatus.FAILED]
        assert len(failed) == 0

    def test_private_functions_checked_in_strict_mode(self) -> None:
        source = textwrap.dedent("""\
            def _private_helper(x: int) -> int:
                return x
        """)
        info = parse_module_info(source, "core/engine.py", "core/engine.py")
        results = check_contract_completeness([info], strict_config())
        failed = [r for r in results if r.status == CheckStatus.FAILED]
        assert len(failed) >= 1


class TestCircularDependencies:
    """Tests for circular dependency detection."""

    def test_no_cycles_passes(self) -> None:
        modules = [
            ModuleInfo(
                file_path="a.py", module_path="a.py",
                imports=(), from_imports=(("b", "func"),),
                classes=(), functions=(), protocols=(),
            ),
            ModuleInfo(
                file_path="b.py", module_path="b.py",
                imports=(), from_imports=(("c", "func"),),
                classes=(), functions=(), protocols=(),
            ),
            ModuleInfo(
                file_path="c.py", module_path="c.py",
                imports=(), from_imports=(),
                classes=(), functions=(), protocols=(),
            ),
        ]
        results = check_circular_dependencies(modules, default_config())
        assert len(results) == 0

    def test_simple_cycle_detected(self) -> None:
        modules = [
            ModuleInfo(
                file_path="a.py", module_path="a.py",
                imports=(), from_imports=(("b", "func"),),
                classes=(), functions=(), protocols=(),
            ),
            ModuleInfo(
                file_path="b.py", module_path="b.py",
                imports=(), from_imports=(("a", "func"),),
                classes=(), functions=(), protocols=(),
            ),
        ]
        results = check_circular_dependencies(modules, default_config())
        assert len(results) >= 1
        assert "circular" in results[0].details[0].message.lower()

    def test_transitive_cycle_detected(self) -> None:
        modules = [
            ModuleInfo(
                file_path="a.py", module_path="a.py",
                imports=(), from_imports=(("b", "func"),),
                classes=(), functions=(), protocols=(),
            ),
            ModuleInfo(
                file_path="b.py", module_path="b.py",
                imports=(), from_imports=(("c", "func"),),
                classes=(), functions=(), protocols=(),
            ),
            ModuleInfo(
                file_path="c.py", module_path="c.py",
                imports=(), from_imports=(("a", "func"),),
                classes=(), functions=(), protocols=(),
            ),
        ]
        results = check_circular_dependencies(modules, default_config())
        assert len(results) >= 1

    def test_external_imports_ignored(self) -> None:
        modules = [
            ModuleInfo(
                file_path="a.py", module_path="a.py",
                imports=("os", "ast", "icontract"),
                from_imports=(),
                classes=(), functions=(), protocols=(),
            ),
        ]
        results = check_circular_dependencies(modules, default_config())
        assert len(results) == 0

    def test_self_import_ignored(self) -> None:
        modules = [
            ModuleInfo(
                file_path="a.py", module_path="a.py",
                imports=(), from_imports=(("a", "func"),),
                classes=(), functions=(), protocols=(),
            ),
        ]
        results = check_circular_dependencies(modules, default_config())
        assert len(results) == 0

    def test_relative_import_cycle_detected(self) -> None:
        a_info = parse_module_info(
            textwrap.dedent("""\
                from .b import g
            """),
            "pkg/a.py",
            "pkg/a.py",
        )
        b_info = parse_module_info(
            textwrap.dedent("""\
                from .a import f
            """),
            "pkg/b.py",
            "pkg/b.py",
        )

        results = check_circular_dependencies([a_info, b_info], default_config())

        assert len(results) >= 1
        assert "circular" in results[0].details[0].message.lower()


class TestAssumeGuarantee:
    """Tests for assume-guarantee reasoning."""

    def _make_func(
        self,
        name: str,
        has_require: bool = False,
        has_ensure: bool = False,
        calls: tuple[str, ...] = (),
        params: tuple[ParameterInfo, ...] = (),
    ) -> FunctionInfo:
        return FunctionInfo(
            name=name,
            line=1,
            is_public=True,
            parameters=params,
            return_annotation="int",
            has_require=has_require,
            has_ensure=has_ensure,
            calls=calls,
        )

    def test_cross_module_call_without_postconditions_flagged(self) -> None:
        modules = [
            ModuleInfo(
                file_path="core/engine.py",
                module_path="core/engine.py",
                imports=(),
                from_imports=(("core.helpers", "validate"),),
                classes=(), functions=("process",), protocols=(),
                function_infos=(
                    self._make_func(
                        "process",
                        has_require=True,
                        has_ensure=False,
                        calls=("validate",),
                    ),
                ),
            ),
            ModuleInfo(
                file_path="core/helpers.py",
                module_path="core/helpers.py",
                imports=(), from_imports=(),
                classes=(), functions=("validate",), protocols=(),
                function_infos=(
                    self._make_func(
                        "validate",
                        has_require=True,
                        has_ensure=True,
                        params=(ParameterInfo(name="x", annotation="int"),),
                    ),
                ),
            ),
        ]
        results = check_assume_guarantee(modules, default_config())
        assert any(
            "postconditions" in r.details[0].message
            for r in results if r.status == CheckStatus.FAILED
        )

    def test_cross_module_call_with_contracts_passes(self) -> None:
        modules = [
            ModuleInfo(
                file_path="core/engine.py",
                module_path="core/engine.py",
                imports=(),
                from_imports=(("core.helpers", "validate"),),
                classes=(), functions=("process",), protocols=(),
                function_infos=(
                    self._make_func(
                        "process",
                        has_require=True,
                        has_ensure=True,
                        calls=("validate",),
                    ),
                ),
            ),
            ModuleInfo(
                file_path="core/helpers.py",
                module_path="core/helpers.py",
                imports=(), from_imports=(),
                classes=(), functions=("validate",), protocols=(),
                function_infos=(
                    self._make_func("validate", has_require=True, has_ensure=True),
                ),
            ),
        ]
        results = check_assume_guarantee(modules, default_config())
        failed = [r for r in results if r.status == CheckStatus.FAILED]
        assert len(failed) == 0

    def test_same_module_calls_not_flagged(self) -> None:
        modules = [
            ModuleInfo(
                file_path="core/engine.py",
                module_path="core/engine.py",
                imports=(), from_imports=(),
                classes=(), functions=("process", "validate"), protocols=(),
                function_infos=(
                    self._make_func("process", calls=("validate",)),
                    self._make_func("validate", has_require=True),
                ),
            ),
        ]
        results = check_assume_guarantee(modules, default_config())
        assert len(results) == 0

    def test_external_calls_not_flagged(self) -> None:
        modules = [
            ModuleInfo(
                file_path="core/engine.py",
                module_path="core/engine.py",
                imports=(), from_imports=(),
                classes=(), functions=("process",), protocols=(),
                function_infos=(
                    self._make_func("process", calls=("unknown_external",)),
                ),
            ),
        ]
        results = check_assume_guarantee(modules, default_config())
        assert len(results) == 0

    def test_missing_preconditions_on_caller_flagged(self) -> None:
        modules = [
            ModuleInfo(
                file_path="core/engine.py",
                module_path="core/engine.py",
                imports=(),
                from_imports=(("core.helpers", "validate"),),
                classes=(), functions=("process",), protocols=(),
                function_infos=(
                    self._make_func(
                        "process",
                        has_require=False,
                        has_ensure=True,
                        calls=("validate",),
                        params=(ParameterInfo(name="x", annotation="int"),),
                    ),
                ),
            ),
            ModuleInfo(
                file_path="core/helpers.py",
                module_path="core/helpers.py",
                imports=(), from_imports=(),
                classes=(), functions=("validate",), protocols=(),
                function_infos=(
                    self._make_func(
                        "validate",
                        has_require=True,
                        has_ensure=True,
                        params=(ParameterInfo(name="x", annotation="int"),),
                    ),
                ),
            ),
        ]
        results = check_assume_guarantee(modules, default_config())
        assert any(
            "preconditions" in r.details[0].message and "constrain" in r.details[0].message
            for r in results if r.status == CheckStatus.FAILED
        )

    def test_from_import_alias_is_resolved(self) -> None:
        callee = parse_module_info(
            textwrap.dedent("""\
                import icontract

                @icontract.require(lambda x: x > 0, "x must be positive")
                @icontract.ensure(lambda result: result > 0, "result must be positive")
                def validate(x: int) -> int:
                    return x
            """),
            "core/helpers.py",
            "core/helpers.py",
        )
        caller = parse_module_info(
            textwrap.dedent("""\
                from core.helpers import validate as guarded

                def process(x: int) -> int:
                    return guarded(x)
            """),
            "core/engine.py",
            "core/engine.py",
        )

        results = check_assume_guarantee([caller, callee], default_config())

        assert any(
            "lacks postconditions" in r.details[0].message
            for r in results if r.status == CheckStatus.FAILED
        )


class TestDataFlow:
    """Tests for data flow verification."""

    def _make_func(
        self,
        name: str,
        params: tuple[ParameterInfo, ...] = (),
        return_annotation: str | None = "int",
        has_require: bool = False,
        has_ensure: bool = False,
        calls: tuple[str, ...] = (),
    ) -> FunctionInfo:
        return FunctionInfo(
            name=name,
            line=1,
            is_public=True,
            parameters=params,
            return_annotation=return_annotation,
            has_require=has_require,
            has_ensure=has_ensure,
            calls=calls,
        )

    def test_untyped_params_at_boundary_flagged(self) -> None:
        modules = [
            ModuleInfo(
                file_path="core/engine.py",
                module_path="core/engine.py",
                imports=(),
                from_imports=(("core.helpers", "validate"),),
                classes=(), functions=("process",), protocols=(),
                function_infos=(
                    self._make_func("process", calls=("validate",)),
                ),
            ),
            ModuleInfo(
                file_path="core/helpers.py",
                module_path="core/helpers.py",
                imports=(), from_imports=(),
                classes=(), functions=("validate",), protocols=(),
                function_infos=(
                    self._make_func(
                        "validate",
                        params=(ParameterInfo(name="x", annotation=None),),
                    ),
                ),
            ),
        ]
        results = check_data_flow(modules, default_config())
        assert any(
            "type annotations" in r.details[0].message
            for r in results if r.status == CheckStatus.FAILED
        )

    def test_missing_return_type_flagged(self) -> None:
        modules = [
            ModuleInfo(
                file_path="core/engine.py",
                module_path="core/engine.py",
                imports=(),
                from_imports=(("core.helpers", "validate"),),
                classes=(), functions=("process",), protocols=(),
                function_infos=(
                    self._make_func(
                        "process",
                        return_annotation=None,
                        calls=("validate",),
                    ),
                ),
            ),
            ModuleInfo(
                file_path="core/helpers.py",
                module_path="core/helpers.py",
                imports=(), from_imports=(),
                classes=(), functions=("validate",), protocols=(),
                function_infos=(
                    self._make_func(
                        "validate",
                        has_require=True,
                        params=(ParameterInfo(name="x", annotation="int"),),
                    ),
                ),
            ),
        ]
        results = check_data_flow(modules, default_config())
        assert any(
            "return type" in r.details[0].message
            for r in results if r.status == CheckStatus.FAILED
        )

    def test_fully_typed_passes(self) -> None:
        modules = [
            ModuleInfo(
                file_path="core/engine.py",
                module_path="core/engine.py",
                imports=(),
                from_imports=(("core.helpers", "validate"),),
                classes=(), functions=("process",), protocols=(),
                function_infos=(
                    self._make_func(
                        "process",
                        return_annotation="int",
                        calls=("validate",),
                    ),
                ),
            ),
            ModuleInfo(
                file_path="core/helpers.py",
                module_path="core/helpers.py",
                imports=(), from_imports=(),
                classes=(), functions=("validate",), protocols=(),
                function_infos=(
                    self._make_func(
                        "validate",
                        has_require=True,
                        params=(ParameterInfo(name="x", annotation="int"),),
                    ),
                ),
            ),
        ]
        results = check_data_flow(modules, default_config())
        failed = [r for r in results if r.status == CheckStatus.FAILED]
        assert len(failed) == 0

    def test_exempt_module_skipped(self) -> None:
        modules = [
            ModuleInfo(
                file_path="adapters/local_fs.py",
                module_path="adapters/local_fs.py",
                imports=(),
                from_imports=(("core.helpers", "validate"),),
                classes=(), functions=("process",), protocols=(),
                function_infos=(
                    self._make_func(
                        "process",
                        return_annotation=None,
                        calls=("validate",),
                    ),
                ),
            ),
        ]
        results = check_data_flow(modules, default_config())
        assert len(results) == 0

    def test_import_alias_is_resolved(self) -> None:
        callee = parse_module_info(
            textwrap.dedent("""\
                import icontract

                @icontract.require(lambda x: x > 0, "x must be positive")
                @icontract.ensure(lambda result: result > 0, "result must be positive")
                def validate(x: int) -> int:
                    return x
            """),
            "core/helpers.py",
            "core/helpers.py",
        )
        caller = parse_module_info(
            textwrap.dedent("""\
                import core.helpers as helpers

                def process(x: int):
                    return helpers.validate(x)
            """),
            "core/engine.py",
            "core/engine.py",
        )

        results = check_data_flow([caller, callee], default_config())

        assert any(
            "return type" in r.details[0].message
            for r in results if r.status == CheckStatus.FAILED
        )

    def test_unimported_dotted_calls_do_not_bind_by_substring(self) -> None:
        caller = parse_module_info(
            textwrap.dedent("""\
                def process(x: int) -> int:
                    return helpers.validate(x)
            """),
            "core/engine.py",
            "core/engine.py",
        )
        unrelated_callee = parse_module_info(
            textwrap.dedent("""\
                import icontract

                @icontract.require(lambda x: x > 0, "x must be positive")
                @icontract.ensure(lambda result: result > 0, "result must be positive")
                def validate(x: int) -> int:
                    return x
            """),
            "core/helpers_extra.py",
            "core/helpers_extra.py",
        )

        results = check_data_flow([caller, unrelated_callee], default_config())

        assert len(results) == 0


class TestSystemInvariants:
    """Tests for system invariant checking."""

    def test_non_protocol_class_in_ports_flagged(self) -> None:
        modules = [
            ModuleInfo(
                file_path="ports/fs.py",
                module_path="ports/fs.py",
                imports=(), from_imports=(),
                classes=(
                    ClassInfo(
                        name="ConcreteClass",
                        line=5,
                        bases=(),
                        methods=("do_thing",),
                        is_protocol=False,
                    ),
                ),
                functions=(), protocols=(),
            ),
        ]
        results = check_system_invariants(modules, default_config())
        assert any(
            "not a protocol" in r.details[0].message.lower()
            for r in results if r.status == CheckStatus.FAILED
        )

    def test_protocol_without_adapter_info_message(self) -> None:
        modules = [
            ModuleInfo(
                file_path="ports/fs.py",
                module_path="ports/fs.py",
                imports=(), from_imports=(),
                classes=(), functions=(),
                protocols=(
                    ProtocolInfo(
                        name="OrphanProtocol",
                        line=5,
                        methods=(
                            MethodSignature(name="do_thing", parameters=(), has_return_annotation=True),
                        ),
                    ),
                ),
            ),
        ]
        results = check_system_invariants(modules, default_config())
        assert any("no detected adapter" in r.details[0].message for r in results)

    def test_forbidden_import_in_core_flagged(self) -> None:
        modules = [
            ModuleInfo(
                file_path="core/engine.py",
                module_path="core/engine.py",
                imports=("os",),
                from_imports=(),
                classes=(), functions=(), protocols=(),
            ),
        ]
        results = check_system_invariants(modules, default_config())
        assert any(
            "forbidden" in r.details[0].message.lower()
            for r in results if r.status == CheckStatus.FAILED
        )

    def test_adapter_importing_os_passes(self) -> None:
        modules = [
            ModuleInfo(
                file_path="adapters/local_fs.py",
                module_path="adapters/local_fs.py",
                imports=("os",),
                from_imports=(),
                classes=(), functions=(), protocols=(),
            ),
        ]
        results = check_system_invariants(modules, default_config())
        failed = [r for r in results if r.status == CheckStatus.FAILED]
        assert len(failed) == 0

    def test_transports_directory_not_treated_as_ports(self) -> None:
        modules = [
            ModuleInfo(
                file_path="transports/client.py",
                module_path="transports/client.py",
                imports=(),
                from_imports=(),
                classes=(
                    ClassInfo(
                        name="Client",
                        line=3,
                        bases=(),
                        methods=("connect",),
                        is_protocol=False,
                    ),
                ),
                functions=(),
                protocols=(),
            ),
        ]
        results = check_system_invariants(modules, default_config())
        assert len(results) == 0


class TestCheckCompositionalOrchestrator:
    """Tests for the main check_compositional orchestrator."""

    def test_valid_architecture_passes(self) -> None:
        core_source = textwrap.dedent('''\
            """Core module."""

            import ast
            import re
        ''')
        adapter_source = textwrap.dedent('''\
            """Adapter module."""

            import os
            from pathlib import Path
        ''')
        sources = [
            (core_source, "src/serenecode/core/engine.py", "core/engine.py"),
            (adapter_source, "src/serenecode/adapters/local_fs.py", "adapters/local_fs.py"),
        ]
        result = check_compositional(sources, default_config())
        assert result.passed is True

    def test_invalid_architecture_fails(self) -> None:
        core_source = textwrap.dedent('''\
            """Core module importing adapter — violation!"""

            from serenecode.adapters.local_fs import LocalFileReader
        ''')
        sources = [
            (core_source, "src/serenecode/core/engine.py", "core/engine.py"),
        ]
        result = check_compositional(sources, default_config())
        assert result.passed is False
        failures = [r for r in result.results if r.status == CheckStatus.FAILED]
        assert len(failures) >= 1

    def test_empty_sources_passes(self) -> None:
        result = check_compositional([], default_config())
        assert result.passed is True

    def test_duration_recorded(self) -> None:
        result = check_compositional([], default_config())
        assert result.summary.duration_seconds >= 0
