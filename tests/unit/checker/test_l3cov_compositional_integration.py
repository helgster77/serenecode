"""Branch-level unit tests for compositional_integration coverage gaps.

The declared-integration semantic helpers are exercised transitively
through check_declared_integrations, but L3 coverage flags them as below
threshold because not every path and branch direction gets hit. This
file adds focused, deterministic tests over pure AST/data inputs for:

- _check_protocol_implementations
- _check_single_integration_point
- _call_integration_is_satisfied
- _implements_integration_issue
- _protocol_signature_issue
- _find_symbol_node
- _type_expr_matches_integration_target
- _node_satisfies_single_integration_target
"""

from __future__ import annotations

import ast
import textwrap

from serenecode.checker.compositional import _check_signature_compatibility
from serenecode.checker.compositional_integration import (
    _call_integration_is_satisfied,
    _check_protocol_implementations,
    _check_single_integration_point,
    _find_symbol_node,
    _implements_integration_issue,
    _node_satisfies_single_integration_target,
    _protocol_signature_issue,
    _type_expr_matches_integration_target,
)
from serenecode.checker.compositional_parsing import (
    ClassInfo,
    MethodSignature,
    ProtocolInfo,
)
from serenecode.checker.spec_traceability import IntegrationPoint
from serenecode.models import CheckStatus


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _point(
    identifier: str = "INT-001",
    kind: str = "call",
    source: str = "process",
    target: str = "Sink",
) -> IntegrationPoint:
    return IntegrationPoint(
        identifier=identifier,
        description="declared integration",
        kind=kind,
        source=source,
        target=target,
        line=1,
    )


class _StubPoint:
    """Duck-typed integration point for edge cases IntegrationPoint's invariants forbid."""

    def __init__(self, source: str, target: str) -> None:
        self.identifier = "INT-999"
        self.kind = "call"
        self.source = source
        self.target = target


def _method(name: str, params: tuple[str, ...] = (), annotated: bool = True) -> MethodSignature:
    return MethodSignature(
        name=name,
        parameters=params,
        has_return_annotation=annotated,
    )


def _cls(
    name: str = "MySink",
    bases: tuple[str, ...] = (),
    signatures: tuple[MethodSignature, ...] = (),
) -> ClassInfo:
    return ClassInfo(
        name=name,
        line=1,
        bases=bases,
        methods=tuple(sig.name for sig in signatures),
        is_protocol=False,
        method_signatures=signatures,
    )


def _first_function(src: str) -> ast.FunctionDef:
    tree = ast.parse(textwrap.dedent(src))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            return node
    raise AssertionError("no function in source")


def _first_expr(src: str) -> ast.expr:
    tree = ast.parse(textwrap.dedent(src), mode="eval")
    return tree.body


# ---------------------------------------------------------------------------
# _check_protocol_implementations
# ---------------------------------------------------------------------------


class TestCheckProtocolImplementations:
    def test_no_protocols_yields_no_results(self) -> None:
        results = _check_protocol_implementations({}, [], lambda cls, proto: True)
        assert results == []

    def test_protocol_with_implementation_yields_no_results(self) -> None:
        proto = ProtocolInfo(name="Sink", line=3, methods=(_method("write", ("data",)),))
        adapter = _cls(name="FileSink")
        results = _check_protocol_implementations(
            {"Sink": ("src/ports/sink.py", proto)},
            [adapter],
            lambda cls, p: True,
        )
        assert results == []

    def test_protocol_without_implementation_is_reported(self) -> None:
        proto = ProtocolInfo(name="Sink", line=3, methods=(_method("write", ("data",)),))
        adapter = _cls(name="Unrelated")
        results = _check_protocol_implementations(
            {"Sink": ("src/ports/sink.py", proto)},
            [adapter],
            lambda cls, p: False,
        )
        assert len(results) == 1
        assert results[0].function == "Sink"
        assert results[0].status == CheckStatus.PASSED
        assert "no detected adapter implementation" in results[0].details[0].message

    def test_protocol_without_any_adapters_is_reported(self) -> None:
        proto = ProtocolInfo(name="Sink", line=3, methods=())
        results = _check_protocol_implementations(
            {"Sink": ("src/ports/sink.py", proto)},
            [],
            lambda cls, p: True,
        )
        assert len(results) == 1
        assert results[0].file == "src/ports/sink.py"


# ---------------------------------------------------------------------------
# _check_single_integration_point
# ---------------------------------------------------------------------------

_SATISFIED_CALL_SOURCE = (
    "def process():\n"
    '    """Implements: INT-001"""\n'
    "    return Sink()\n"
)

_UNSATISFIED_CALL_SOURCE = (
    "def process():\n"
    '    """Implements: INT-001"""\n'
    "    return other()\n"
)


class TestCheckSingleIntegrationPoint:
    def test_point_with_no_refs_returns_none(self) -> None:
        result = _check_single_integration_point(
            _point(), {}, {}, {}, {}, _check_signature_compatibility,
        )
        assert result is None

    def test_satisfied_call_integration_returns_none(self) -> None:
        refs = {"INT-001": [("app.py", "process", 1)]}
        source_map = {"app.py": _SATISFIED_CALL_SOURCE}
        result = _check_single_integration_point(
            _point(kind="call"), refs, source_map, {}, {}, _check_signature_compatibility,
        )
        assert result is None

    def test_unsatisfied_call_integration_is_reported(self) -> None:
        refs = {"INT-001": [("app.py", "process", 1)]}
        source_map = {"app.py": _UNSATISFIED_CALL_SOURCE}
        result = _check_single_integration_point(
            _point(kind="call"), refs, source_map, {}, {}, _check_signature_compatibility,
        )
        assert result is not None
        assert result.status == CheckStatus.FAILED
        assert result.function == "INT-001"
        assert result.file == "app.py"
        assert "call integration" in result.details[0].message
        assert result.details[0].finding_type == "integration_semantics"

    def test_satisfied_implements_integration_returns_none(self) -> None:
        point = _point(kind="implements", source="MySink", target="Sink")
        refs = {"INT-001": [("adapter.py", "MySink", 1)]}
        class_map = {("adapter.py", "MySink"): _cls(name="MySink", bases=("Sink",))}
        result = _check_single_integration_point(
            point, refs, {}, class_map, {}, _check_signature_compatibility,
        )
        assert result is None

    def test_unsatisfied_implements_integration_is_reported(self) -> None:
        point = _point(kind="implements", source="MySink", target="Sink")
        refs = {"INT-001": [("adapter.py", "MySink", 1)]}
        class_map = {("adapter.py", "MySink"): _cls(name="MySink", bases=())}
        result = _check_single_integration_point(
            point, refs, {}, class_map, {}, _check_signature_compatibility,
        )
        assert result is not None
        assert result.status == CheckStatus.FAILED
        assert "does not inherit from 'Sink'" in result.details[0].message


# ---------------------------------------------------------------------------
# _call_integration_is_satisfied
# ---------------------------------------------------------------------------


class TestCallIntegrationIsSatisfied:
    def test_matching_call_in_tagged_symbol_satisfies(self) -> None:
        refs = [("app.py", "process", 1)]
        source_map = {"app.py": _SATISFIED_CALL_SOURCE}
        assert _call_integration_is_satisfied(_point(), refs, source_map) is True

    def test_non_matching_call_does_not_satisfy(self) -> None:
        refs = [("app.py", "process", 1)]
        source_map = {"app.py": _UNSATISFIED_CALL_SOURCE}
        assert _call_integration_is_satisfied(_point(), refs, source_map) is False

    def test_whitespace_only_target_is_never_satisfied(self) -> None:
        refs = [("app.py", "process", 1)]
        source_map = {"app.py": _SATISFIED_CALL_SOURCE}
        stub = _StubPoint(source="process", target=" ")
        assert _call_integration_is_satisfied(stub, refs, source_map) is False  # type: ignore[arg-type]

    def test_uses_all_refs_when_no_symbol_matches_source(self) -> None:
        point = _point(source="pkg.unrelated")
        refs = [("app.py", "process", 1)]
        source_map = {"app.py": _SATISFIED_CALL_SOURCE}
        assert _call_integration_is_satisfied(point, refs, source_map) is True

    def test_missing_source_file_is_skipped(self) -> None:
        refs = [("missing.py", "process", 1)]
        assert _call_integration_is_satisfied(_point(), refs, {}) is False

    def test_symbol_not_found_in_source_is_skipped(self) -> None:
        refs = [("app.py", "process", 99)]
        source_map = {"app.py": _SATISFIED_CALL_SOURCE}
        assert _call_integration_is_satisfied(_point(), refs, source_map) is False

    def test_dotted_source_matches_last_segment(self) -> None:
        point = _point(source="pkg.process")
        refs = [("app.py", "process", 1), ("other.py", "helper", 1)]
        source_map = {"app.py": _SATISFIED_CALL_SOURCE}
        assert _call_integration_is_satisfied(point, refs, source_map) is True


# ---------------------------------------------------------------------------
# _implements_integration_issue
# ---------------------------------------------------------------------------


class TestImplementsIntegrationIssue:
    def test_no_refs_reports_missing_source_class(self) -> None:
        point = _point(kind="implements", source="MySink", target="Sink")
        issue = _implements_integration_issue(point, [], {}, {})
        assert issue == "no class matching source 'MySink' was found"

    def test_tag_not_on_a_class_is_reported(self) -> None:
        point = _point(kind="implements", source="MySink", target="Sink")
        refs = [("adapter.py", "MySink", 1)]
        issue = _implements_integration_issue(point, refs, {}, {})
        assert issue == "the tag is not attached to an implementing class"

    def test_explicit_inheritance_without_protocol_is_satisfied(self) -> None:
        point = _point(kind="implements", source="MySink", target="Sink")
        refs = [("adapter.py", "MySink", 1)]
        class_map = {("adapter.py", "MySink"): _cls(name="MySink", bases=("Sink",))}
        assert _implements_integration_issue(point, refs, class_map, {}) is None

    def test_dotted_base_inheritance_is_satisfied(self) -> None:
        point = _point(kind="implements", source="MySink", target="ports.Sink")
        refs = [("adapter.py", "MySink", 1)]
        class_map = {("adapter.py", "MySink"): _cls(name="MySink", bases=("ports.Sink",))}
        assert _implements_integration_issue(point, refs, class_map, {}) is None

    def test_missing_inheritance_without_protocol_is_reported(self) -> None:
        point = _point(kind="implements", source="MySink", target="Sink")
        refs = [("adapter.py", "MySink", 1)]
        class_map = {("adapter.py", "MySink"): _cls(name="MySink", bases=("object",))}
        issue = _implements_integration_issue(point, refs, class_map, {})
        assert issue == "class 'MySink' does not inherit from 'Sink'"

    def test_structural_protocol_match_is_satisfied(self) -> None:
        point = _point(kind="implements", source="MySink", target="Sink")
        refs = [("adapter.py", "MySink", 1)]
        write = _method("write", ("data",))
        class_map = {("adapter.py", "MySink"): _cls(name="MySink", signatures=(write,))}
        protocol_map = {"Sink": ProtocolInfo(name="Sink", line=3, methods=(write,))}
        issue = _implements_integration_issue(
            point, refs, class_map, protocol_map, _check_signature_compatibility,
        )
        assert issue is None

    def test_protocol_signature_mismatch_is_reported(self) -> None:
        point = _point(kind="implements", source="MySink", target="Sink")
        refs = [("adapter.py", "MySink", 1)]
        class_map = {("adapter.py", "MySink"): _cls(name="MySink", signatures=())}
        protocol_map = {
            "Sink": ProtocolInfo(name="Sink", line=3, methods=(_method("write", ("data",)),)),
        }
        issue = _implements_integration_issue(
            point, refs, class_map, protocol_map, _check_signature_compatibility,
        )
        assert issue == "class 'MySink' is missing method 'write'"

    def test_falls_back_to_all_refs_when_source_matches_none(self) -> None:
        point = _point(kind="implements", source="OtherName", target="Sink")
        refs = [("adapter.py", "MySink", 1)]
        class_map = {("adapter.py", "MySink"): _cls(name="MySink", bases=("Sink",))}
        assert _implements_integration_issue(point, refs, class_map, {}) is None

    def test_second_non_inheriting_ref_keeps_first_issue(self) -> None:
        point = _point(kind="implements", source="MySink", target="Sink")
        refs = [("a.py", "MySink", 1), ("b.py", "MySink", 1)]
        class_map = {
            ("a.py", "MySink"): _cls(name="MySink", bases=("object",)),
            ("b.py", "MySink"): _cls(name="MySink", bases=("Base",)),
        }
        issue = _implements_integration_issue(point, refs, class_map, {})
        assert issue == "class 'MySink' does not inherit from 'Sink'"

    def test_second_signature_mismatch_keeps_first_issue(self) -> None:
        point = _point(kind="implements", source="MySink", target="Sink")
        refs = [("a.py", "MySink", 1), ("b.py", "MySink", 1)]
        class_map = {
            ("a.py", "MySink"): _cls(name="MySink", signatures=()),
            ("b.py", "MySink"): _cls(name="MySink", signatures=(_method("read", ()),)),
        }
        protocol_map = {
            "Sink": ProtocolInfo(name="Sink", line=3, methods=(_method("write", ("data",)),)),
        }
        issue = _implements_integration_issue(
            point, refs, class_map, protocol_map, _check_signature_compatibility,
        )
        assert issue == "class 'MySink' is missing method 'write'"

    def test_first_issue_wins_across_multiple_refs(self) -> None:
        point = _point(kind="implements", source="MySink", target="Sink")
        refs = [("a.py", "MySink", 1), ("b.py", "MySink", 1)]
        class_map = {
            ("a.py", "MySink"): _cls(name="MySink", bases=("object",)),
            # (b.py, MySink) intentionally absent from class_map
        }
        issue = _implements_integration_issue(point, refs, class_map, {})
        assert issue == "class 'MySink' does not inherit from 'Sink'"


# ---------------------------------------------------------------------------
# _protocol_signature_issue
# ---------------------------------------------------------------------------


class TestProtocolSignatureIssue:
    def test_matching_class_with_default_compat_fn_passes(self) -> None:
        write = _method("write", ("data",))
        cls = _cls(signatures=(write,))
        proto = ProtocolInfo(name="Sink", line=3, methods=(write,))
        assert _protocol_signature_issue(cls, proto) is None

    def test_missing_method_is_reported(self) -> None:
        cls = _cls(signatures=(_method("read", ()),))
        proto = ProtocolInfo(name="Sink", line=3, methods=(_method("write", ("data",)),))
        issue = _protocol_signature_issue(cls, proto)
        assert issue == "class 'MySink' is missing method 'write'"

    def test_incompatible_signature_reports_first_issue(self) -> None:
        cls = _cls(signatures=(_method("write", ()),))
        proto = ProtocolInfo(name="Sink", line=3, methods=(_method("write", ("data",)),))
        issue = _protocol_signature_issue(cls, proto, _check_signature_compatibility)
        assert issue is not None
        assert "write" in issue

    def test_injected_compat_fn_is_used(self) -> None:
        write = _method("write", ("data",))
        cls = _cls(signatures=(write,))
        proto = ProtocolInfo(name="Sink", line=3, methods=(write,))
        issue = _protocol_signature_issue(cls, proto, lambda a, b: ["injected issue"])
        assert issue == "injected issue"

    def test_protocol_without_methods_always_passes(self) -> None:
        cls = _cls(signatures=())
        proto = ProtocolInfo(name="Sink", line=3, methods=())
        assert _protocol_signature_issue(cls, proto) is None


# ---------------------------------------------------------------------------
# _find_symbol_node
# ---------------------------------------------------------------------------


class TestFindSymbolNode:
    def test_finds_function_by_name_and_line(self) -> None:
        source = "x = 1\ndef target():\n    pass\n"
        node = _find_symbol_node(source, "target", 2)
        assert isinstance(node, ast.FunctionDef)
        assert node.name == "target"

    def test_finds_class_by_name_and_line(self) -> None:
        source = "class Widget:\n    pass\n"
        node = _find_symbol_node(source, "Widget", 1)
        assert isinstance(node, ast.ClassDef)

    def test_finds_async_function(self) -> None:
        source = "async def fetch():\n    pass\n"
        node = _find_symbol_node(source, "fetch", 1)
        assert isinstance(node, ast.AsyncFunctionDef)

    def test_wrong_line_returns_none(self) -> None:
        source = "def target():\n    pass\n"
        assert _find_symbol_node(source, "target", 5) is None

    def test_wrong_name_returns_none(self) -> None:
        source = "def target():\n    pass\n"
        assert _find_symbol_node(source, "other", 1) is None

    def test_syntax_error_returns_none(self) -> None:
        assert _find_symbol_node("def broken(:\n", "broken", 1) is None

    def test_source_without_definitions_returns_none(self) -> None:
        assert _find_symbol_node("x = 1\ny = 2\n", "x", 1) is None


# ---------------------------------------------------------------------------
# _type_expr_matches_integration_target
# ---------------------------------------------------------------------------


class TestTypeExprMatchesIntegrationTarget:
    def test_simple_name_match(self) -> None:
        expr = _first_expr("Sink")
        assert _type_expr_matches_integration_target(expr, "Sink", "Sink") is True

    def test_dotted_attribute_matches_full_target(self) -> None:
        expr = _first_expr("ports.Sink")
        assert _type_expr_matches_integration_target(expr, "ports.Sink", "Sink") is True

    def test_attribute_matching_simple_suffix(self) -> None:
        expr = _first_expr("mod.sub.Sink")
        assert _type_expr_matches_integration_target(expr, "ports.Sink", "Sink") is True

    def test_non_matching_name_returns_false(self) -> None:
        expr = _first_expr("Other")
        assert _type_expr_matches_integration_target(expr, "Sink", "Sink") is False

    def test_tuple_with_matching_element_returns_true(self) -> None:
        expr = _first_expr("(Other, Sink)")
        assert _type_expr_matches_integration_target(expr, "Sink", "Sink") is True

    def test_tuple_without_matching_element_returns_false(self) -> None:
        expr = _first_expr("(Other, Another)")
        assert _type_expr_matches_integration_target(expr, "Sink", "Sink") is False

    def test_unresolvable_expression_returns_false(self) -> None:
        expr = _first_expr("42")
        assert _type_expr_matches_integration_target(expr, "Sink", "Sink") is False


# ---------------------------------------------------------------------------
# _node_satisfies_single_integration_target
# ---------------------------------------------------------------------------


class TestNodeSatisfiesSingleIntegrationTarget:
    def test_direct_call_to_target_satisfies(self) -> None:
        node = _first_function("def f():\n    return Sink()\n")
        assert _node_satisfies_single_integration_target(node, "Sink") is True

    def test_dotted_call_matching_simple_suffix_satisfies(self) -> None:
        node = _first_function("def f():\n    return adapters.Sink()\n")
        assert _node_satisfies_single_integration_target(node, "ports.Sink") is True

    def test_full_dotted_call_satisfies(self) -> None:
        node = _first_function("def f():\n    return ports.Sink()\n")
        assert _node_satisfies_single_integration_target(node, "ports.Sink") is True

    def test_non_matching_call_does_not_satisfy(self) -> None:
        node = _first_function("def f():\n    return other()\n")
        assert _node_satisfies_single_integration_target(node, "Sink") is False

    def test_isinstance_check_against_target_satisfies(self) -> None:
        node = _first_function("def f(x):\n    return isinstance(x, Sink)\n")
        assert _node_satisfies_single_integration_target(node, "Sink") is True

    def test_isinstance_against_other_type_does_not_satisfy(self) -> None:
        node = _first_function("def f(x):\n    return isinstance(x, Other)\n")
        assert _node_satisfies_single_integration_target(node, "Sink") is False

    def test_isinstance_with_single_argument_does_not_satisfy(self) -> None:
        node = _first_function("def f(x):\n    return isinstance(x)\n")
        assert _node_satisfies_single_integration_target(node, "Sink") is False

    def test_body_without_calls_does_not_satisfy(self) -> None:
        node = _first_function("def f():\n    x = 1\n    return x\n")
        assert _node_satisfies_single_integration_target(node, "Sink") is False
