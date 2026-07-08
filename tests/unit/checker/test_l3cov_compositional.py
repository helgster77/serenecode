"""Level 3 coverage gap tests for compositional checker helpers.

Targets specific uncovered lines and partial branches flagged by
per-function coverage analysis:

- compositional.py: _class_likely_implements (method-overlap heuristic),
  _is_enum_class (enum base match), _check_module_size_info (large module).
- compositional_parsing.py: _get_call_target_name (unresolvable targets),
  _parse_class (attribute/subscript bases), _check_no_invariant_comment
  (all early-exit paths), _parse_protocol (non-method body items).

These are pure AST/dataclass functions, so tests construct small source
snippets with textwrap.dedent or build the frozen dataclasses directly,
following the conventions of test_compositional_helpers.py.
"""

from __future__ import annotations

import ast
import textwrap

from serenecode.checker.compositional import (
    _check_module_size_info,
    _class_likely_implements,
    _is_enum_class,
)
from serenecode.checker.compositional_parsing import (
    ClassInfo,
    FunctionInfo,
    MethodSignature,
    ModuleInfo,
    ProtocolInfo,
    _check_no_invariant_comment,
    _get_call_target_name,
    _parse_class,
    _parse_protocol,
    resolve_icontract_aliases,
)
from serenecode.models import CheckStatus


def _first_class(src: str) -> ast.ClassDef:
    tree = ast.parse(textwrap.dedent(src))
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            return node
    raise AssertionError("no class in source")


def _make_class(
    name: str,
    bases: tuple[str, ...] = (),
    methods: tuple[str, ...] = (),
) -> ClassInfo:
    return ClassInfo(
        name=name,
        line=1,
        bases=bases,
        methods=methods,
        is_protocol=False,
    )


def _make_protocol(name: str, method_names: tuple[str, ...]) -> ProtocolInfo:
    return ProtocolInfo(
        name=name,
        line=1,
        methods=tuple(
            MethodSignature(name=m, parameters=(), has_return_annotation=True)
            for m in method_names
        ),
    )


def _make_function_info(name: str, *, is_public: bool) -> FunctionInfo:
    return FunctionInfo(
        name=name,
        line=1,
        is_public=is_public,
        parameters=(),
        return_annotation="None",
        has_require=False,
        has_ensure=False,
        calls=(),
    )


def _make_module(function_infos: tuple[FunctionInfo, ...]) -> ModuleInfo:
    return ModuleInfo(
        file_path="pkg/mod.py",
        module_path="pkg.mod",
        imports=(),
        from_imports=(),
        classes=(),
        functions=(),
        protocols=(),
        function_infos=function_infos,
    )


# ---------------------------------------------------------------------------
# _class_likely_implements — method-overlap heuristic (lines 503-508)
# ---------------------------------------------------------------------------


class TestClassLikelyImplementsMethodOverlap:
    def test_protocol_without_methods_returns_false(self) -> None:
        # Base and name heuristics miss; protocol has no methods to overlap.
        cls = _make_class("Widget", bases=("object",), methods=("render",))
        proto = _make_protocol("StorageProtocol", ())
        assert _class_likely_implements(cls, proto) is False

    def test_majority_method_overlap_returns_true(self) -> None:
        # Name shares nothing with the protocol, but all 3 methods match.
        cls = _make_class(
            "DbGateway",
            bases=("object",),
            methods=("save", "load", "delete"),
        )
        proto = _make_protocol("RepoProtocol", ("save", "load", "delete"))
        assert _class_likely_implements(cls, proto) is True

    def test_minority_method_overlap_returns_false(self) -> None:
        # Only 1 of 3 protocol methods present — 33% is not > 50%.
        cls = _make_class("DbGateway", bases=(), methods=("save", "other"))
        proto = _make_protocol("RepoProtocol", ("save", "load", "delete"))
        assert _class_likely_implements(cls, proto) is False

    def test_name_heuristic_returns_true(self) -> None:
        # Class name contains the protocol stem ("Repo") — both directions
        # of the name-heuristic branch are exercised across this class.
        cls = _make_class("PatientRepo", bases=(), methods=())
        proto = _make_protocol("RepoProtocol", ("save",))
        assert _class_likely_implements(cls, proto) is True

    def test_explicit_base_still_short_circuits(self) -> None:
        # Guard: explicit base naming keeps returning True before heuristics.
        cls = _make_class("DbGateway", bases=("ports.RepoProtocol",))
        proto = _make_protocol("RepoProtocol", ("save",))
        assert _class_likely_implements(cls, proto) is True


# ---------------------------------------------------------------------------
# _is_enum_class — enum base match (line 534) and both branch directions
# ---------------------------------------------------------------------------


class TestIsEnumClass:
    def test_plain_enum_base_returns_true(self) -> None:
        cls = _make_class("Color", bases=("Enum",))
        assert _is_enum_class(cls) is True

    def test_dotted_enum_base_returns_true(self) -> None:
        cls = _make_class("Flags", bases=("enum.IntFlag",))
        assert _is_enum_class(cls) is True

    def test_non_enum_base_after_iteration_returns_false(self) -> None:
        # Loop runs but never matches — exercises the loop-exit branch.
        cls = _make_class("Service", bases=("BaseService", "Mixin"))
        assert _is_enum_class(cls) is False

    def test_no_bases_returns_false(self) -> None:
        # Loop body never entered.
        cls = _make_class("Bare", bases=())
        assert _is_enum_class(cls) is False


# ---------------------------------------------------------------------------
# _check_module_size_info — large module info (line 675) and both directions
# ---------------------------------------------------------------------------


class TestCheckModuleSizeInfo:
    def test_more_than_ten_public_functions_appends_info(self) -> None:
        infos = tuple(
            _make_function_info(f"func_{i}", is_public=True) for i in range(11)
        )
        results: list = []
        _check_module_size_info(results, _make_module(infos))
        assert len(results) == 1
        result = results[0]
        assert result.function == "<module>"
        assert result.status == CheckStatus.PASSED
        assert "11 public functions" in result.details[0].message

    def test_ten_or_fewer_public_functions_appends_nothing(self) -> None:
        infos = tuple(
            _make_function_info(f"func_{i}", is_public=True) for i in range(10)
        )
        results: list = []
        _check_module_size_info(results, _make_module(infos))
        assert results == []

    def test_private_functions_do_not_count(self) -> None:
        infos = tuple(
            _make_function_info(f"_helper_{i}", is_public=False)
            for i in range(12)
        )
        results: list = []
        _check_module_size_info(results, _make_module(infos))
        assert results == []


# ---------------------------------------------------------------------------
# _get_call_target_name — unresolvable targets (lines 508-509)
# ---------------------------------------------------------------------------


def _call_func_node(expr_src: str) -> ast.expr:
    """Parse an expression and return the .func of its outermost Call."""
    expr = ast.parse(expr_src, mode="eval").body
    assert isinstance(expr, ast.Call)
    return expr.func


class TestGetCallTargetName:
    def test_simple_name(self) -> None:
        assert _get_call_target_name(_call_func_node("process(1)")) == "process"

    def test_dotted_attribute(self) -> None:
        assert _get_call_target_name(_call_func_node("obj.method()")) == "obj.method"

    def test_attribute_on_call_result_returns_bare_attr(self) -> None:
        # factory().method() — the value is a Call, unresolvable, so only
        # the attribute name is returned (line 508).
        assert _get_call_target_name(_call_func_node("factory().method()")) == "method"

    def test_subscript_target_returns_empty_string(self) -> None:
        # handlers[0]() — neither Name nor Attribute (line 509).
        assert _get_call_target_name(_call_func_node("handlers[0]()")) == ""


# ---------------------------------------------------------------------------
# _parse_class — attribute and subscript bases (lines 537-538 + branches)
# ---------------------------------------------------------------------------


class TestParseClassBases:
    def _parse(self, src: str) -> ClassInfo:
        dedented = textwrap.dedent(src)
        tree = ast.parse(dedented)
        aliases = resolve_icontract_aliases(tree)
        node = _first_class(src)
        return _parse_class(node, aliases, dedented)

    def test_attribute_base_is_recorded_dotted(self) -> None:
        info = self._parse("""
            class Adapter(abc.ABC):
                pass
        """)
        assert info.bases == ("abc.ABC",)

    def test_dotted_protocol_base_detected(self) -> None:
        info = self._parse("""
            class Port(typing.Protocol):
                def run(self) -> None: ...
        """)
        assert info.bases == ("typing.Protocol",)
        assert info.is_protocol is True

    def test_subscript_base_is_skipped(self) -> None:
        # Generic[T] is neither ast.Name nor ast.Attribute — falls through
        # both branches of the base loop.
        info = self._parse("""
            class Container(Generic[T]):
                pass
        """)
        assert info.bases == ()

    def test_mixed_bases_and_methods(self) -> None:
        info = self._parse("""
            class Store(Base, abc.ABC):
                version: int = 1

                def save(self) -> None:
                    pass

                async def load(self) -> None:
                    pass
        """)
        assert info.bases == ("Base", "abc.ABC")
        assert info.methods == ("save", "load")
        assert info.is_protocol is False

    def test_default_empty_source_means_no_comment(self) -> None:
        # Calling without source exercises the empty-source early return
        # inside _check_no_invariant_comment.
        tree = ast.parse("class Bare:\n    pass\n")
        aliases = resolve_icontract_aliases(tree)
        node = tree.body[0]
        assert isinstance(node, ast.ClassDef)
        info = _parse_class(node, aliases)
        assert info.has_no_invariant_comment is False


# ---------------------------------------------------------------------------
# _check_no_invariant_comment — lines 577, 584, 586 and break/decorator paths
# ---------------------------------------------------------------------------


class TestCheckNoInvariantComment:
    def _check(self, src: str) -> bool:
        dedented = textwrap.dedent(src)
        node = _first_class(dedented)
        return _check_no_invariant_comment(node, dedented)

    def test_empty_source_returns_false(self) -> None:
        node = _first_class("class Foo:\n    pass")
        assert _check_no_invariant_comment(node, "") is False

    def test_immediate_no_invariant_comment_returns_true(self) -> None:
        assert self._check("""
            # no-invariant: stateless adapter
            class Foo:
                pass
        """) is True

    def test_comment_above_other_comment_returns_true(self) -> None:
        # The intervening plain comment hits the continue branch before
        # the no-invariant comment is found.
        assert self._check("""
            # no-invariant: stateless adapter
            # extra explanatory comment
            class Foo:
                pass
        """) is True

    def test_comment_above_decorator_returns_true(self) -> None:
        assert self._check("""
            # no-invariant: frozen config holder
            @dataclass
            class Foo:
                pass
        """) is True

    def test_unrelated_code_line_breaks_scan(self) -> None:
        assert self._check("""
            x = 1
            class Foo:
                pass
        """) is False

    def test_plain_comment_only_returns_false(self) -> None:
        assert self._check("""
            # just a regular comment
            class Foo:
                pass
        """) is False

    def test_class_on_first_line_returns_false(self) -> None:
        # No lines above the class — the scan loop never runs.
        src = "class Foo:\n    pass\n"
        node = _first_class(src)
        assert _check_no_invariant_comment(node, src) is False


# ---------------------------------------------------------------------------
# _parse_protocol — non-method body items (partial branch in the body loop)
# ---------------------------------------------------------------------------


class TestParseProtocol:
    def test_non_method_body_items_are_skipped(self) -> None:
        node = _first_class("""
            class Repo(Protocol):
                \"\"\"Docstring statement.\"\"\"

                marker: int

                def save(self, item: str) -> bool: ...
        """)
        proto = _parse_protocol(node)
        assert proto.name == "Repo"
        assert [m.name for m in proto.methods] == ["save"]

    def test_protocol_without_methods_has_empty_signature_tuple(self) -> None:
        node = _first_class("""
            class Marker(Protocol):
                ...
        """)
        proto = _parse_protocol(node)
        assert proto.methods == ()

    def test_async_method_is_included(self) -> None:
        node = _first_class("""
            class Fetcher(Protocol):
                async def fetch(self, url: str) -> str: ...
        """)
        proto = _parse_protocol(node)
        assert [m.name for m in proto.methods] == ["fetch"]
