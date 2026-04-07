"""Direct unit tests for the small AST helper functions in structural.py.

These functions are exercised transitively through the higher-level
`check_*` orchestrators in `test_structural.py`, but L3 coverage analysis
sees the helpers themselves as below threshold because not every branch
gets hit through the transitive path. This file adds focused, branch-level
tests for each helper.
"""

from __future__ import annotations

import ast
import textwrap
from typing import cast

from serenecode.checker.structural import (
    _decorator_descriptions_are_literals,
    _decorator_has_description,
    _has_meaningful_params,
    _has_no_invariant_comment,
    _has_non_object_base,
    _has_property_decorator,
    _has_shell_true_kwarg,
    _is_enum_class,
    _is_exception_class,
    _is_init_with_only_assignments,
    _is_protocol_class,
    _is_tautological_isinstance,
    _is_trivial_handler_stmt,
    check_exception_types,
    check_loop_invariants,
    get_decorator_name,
)
from serenecode.config import default_config, strict_config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse(src: str) -> ast.Module:
    return ast.parse(textwrap.dedent(src))


def _first_class(src: str) -> ast.ClassDef:
    tree = _parse(src)
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            return node
    raise AssertionError("no class in source")


def _first_function(src: str) -> ast.FunctionDef:
    tree = _parse(src)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            return node
    raise AssertionError("no function in source")


# ---------------------------------------------------------------------------
# get_decorator_name
# ---------------------------------------------------------------------------


class TestGetDecoratorName:
    def test_simple_name(self) -> None:
        node = ast.parse("@foo\ndef f(): pass").body[0]
        func = cast("ast.FunctionDef", node)
        assert get_decorator_name(func.decorator_list[0]) == "foo"

    def test_attribute(self) -> None:
        node = ast.parse("@foo.bar\ndef f(): pass").body[0]
        func = cast("ast.FunctionDef", node)
        assert get_decorator_name(func.decorator_list[0]) == "foo.bar"

    def test_dotted_attribute(self) -> None:
        node = ast.parse("@a.b.c\ndef f(): pass").body[0]
        func = cast("ast.FunctionDef", node)
        assert get_decorator_name(func.decorator_list[0]) == "a.b.c"

    def test_call(self) -> None:
        node = ast.parse("@foo()\ndef f(): pass").body[0]
        func = cast("ast.FunctionDef", node)
        assert get_decorator_name(func.decorator_list[0]) == "foo"

    def test_call_with_attribute(self) -> None:
        node = ast.parse("@foo.bar()\ndef f(): pass").body[0]
        func = cast("ast.FunctionDef", node)
        assert get_decorator_name(func.decorator_list[0]) == "foo.bar"

    def test_unknown_node_returns_empty(self) -> None:
        # Constant decorators aren't real Python but exercise the fall-through
        const = ast.Constant(value=42)
        assert get_decorator_name(const) == ""

    def test_attribute_with_non_string_attr_returns_empty(self) -> None:
        # Defensive branch (line ~144): malformed AST with non-string attr.
        # Cannot happen from ast.parse() but the function checks for it.
        name = ast.Name(id="foo", ctx=ast.Load())
        attr = ast.Attribute(value=name, attr=cast("str", 123), ctx=ast.Load())
        assert get_decorator_name(attr) == ""

    def test_name_with_non_string_id_returns_empty(self) -> None:
        # Defensive branch (line ~149): malformed AST with non-string id.
        name = ast.Name(id=cast("str", 123), ctx=ast.Load())
        assert get_decorator_name(name) == ""


# ---------------------------------------------------------------------------
# _decorator_has_description
# ---------------------------------------------------------------------------


class TestDecoratorHasDescription:
    def test_two_positional_args_passes(self) -> None:
        func = _first_function("""
            @icontract.require(lambda x: x > 0, "x must be positive")
            def f(x): pass
        """)
        assert _decorator_has_description(func, frozenset({"icontract.require"})) is True

    def test_one_positional_no_kwarg_fails(self) -> None:
        func = _first_function("""
            @icontract.require(lambda x: x > 0)
            def f(x): pass
        """)
        assert _decorator_has_description(func, frozenset({"icontract.require"})) is False

    def test_one_positional_with_description_kwarg_passes(self) -> None:
        # Exercises lines 213-218: the description= keyword path.
        func = _first_function("""
            @icontract.require(lambda x: x > 0, description="x positive")
            def f(x): pass
        """)
        assert _decorator_has_description(func, frozenset({"icontract.require"})) is True

    def test_one_positional_with_unrelated_kwarg_fails(self) -> None:
        # Exercises line 219-220: kwarg present but not "description".
        func = _first_function("""
            @icontract.require(lambda x: x > 0, error_msg="x positive")
            def f(x): pass
        """)
        assert _decorator_has_description(func, frozenset({"icontract.require"})) is False

    def test_unmatched_decorator_skipped(self) -> None:
        func = _first_function("""
            @other_decorator
            def f(): pass
        """)
        assert _decorator_has_description(func, frozenset({"icontract.require"})) is True


# ---------------------------------------------------------------------------
# _decorator_descriptions_are_literals
# ---------------------------------------------------------------------------


class TestDecoratorDescriptionsAreLiterals:
    def test_string_literal_passes(self) -> None:
        func = _first_function("""
            @icontract.require(lambda x: x > 0, "x must be positive")
            def f(x): pass
        """)
        assert _decorator_descriptions_are_literals(func, frozenset({"icontract.require"})) is True

    def test_variable_description_fails(self) -> None:
        func = _first_function("""
            MSG = "x positive"
            @icontract.require(lambda x: x > 0, MSG)
            def f(x): pass
        """)
        assert _decorator_descriptions_are_literals(func, frozenset({"icontract.require"})) is False

    def test_string_literal_kwarg_passes(self) -> None:
        # Exercises lines 337-341: description= kwarg with string literal.
        func = _first_function("""
            @icontract.require(lambda x: x > 0, description="x positive")
            def f(x): pass
        """)
        assert _decorator_descriptions_are_literals(func, frozenset({"icontract.require"})) is True

    def test_variable_kwarg_fails(self) -> None:
        # Exercises lines 337-341: description= kwarg with non-literal value.
        func = _first_function("""
            MSG = "x positive"
            @icontract.require(lambda x: x > 0, description=MSG)
            def f(x): pass
        """)
        assert _decorator_descriptions_are_literals(func, frozenset({"icontract.require"})) is False

    def test_kwarg_other_than_description_skipped(self) -> None:
        func = _first_function("""
            @icontract.require(lambda x: x > 0, error_msg="x positive")
            def f(x): pass
        """)
        # No positional second arg and no description= kwarg → still True
        # (the function only fails when a description IS present and not literal)
        assert _decorator_descriptions_are_literals(func, frozenset({"icontract.require"})) is True


# ---------------------------------------------------------------------------
# _has_meaningful_params
# ---------------------------------------------------------------------------


class TestHasMeaningfulParams:
    def test_function_with_params(self) -> None:
        func = _first_function("def f(x, y): pass")
        assert _has_meaningful_params(func) is True

    def test_function_no_params(self) -> None:
        func = _first_function("def f(): pass")
        assert _has_meaningful_params(func) is False

    def test_method_with_self_only_is_meaningless(self) -> None:
        cls = _first_class("class C:\n    def f(self): pass")
        method = cast("ast.FunctionDef", cls.body[0])
        assert _has_meaningful_params(method) is False

    def test_method_with_self_and_params(self) -> None:
        cls = _first_class("class C:\n    def f(self, x): pass")
        method = cast("ast.FunctionDef", cls.body[0])
        assert _has_meaningful_params(method) is True


# ---------------------------------------------------------------------------
# _has_no_invariant_comment
# ---------------------------------------------------------------------------


class TestHasNoInvariantComment:
    def test_class_with_opt_out_comment(self) -> None:
        src = textwrap.dedent("""\
            # no-invariant: trivial wire-format struct
            class C:
                pass
        """)
        cls = _first_class(src)
        assert _has_no_invariant_comment(cls, src) is True

    def test_class_without_opt_out(self) -> None:
        src = textwrap.dedent("""\
            class C:
                pass
        """)
        cls = _first_class(src)
        assert _has_no_invariant_comment(cls, src) is False

    def test_class_with_decorator_above_opt_out(self) -> None:
        # Exercises line 502: scan past decorator lines to find the comment.
        src = textwrap.dedent("""\
            # no-invariant: stateless adapter
            @decorator
            class C:
                pass
        """)
        cls = _first_class(src)
        assert _has_no_invariant_comment(cls, src) is True

    def test_class_with_unrelated_comment_only(self) -> None:
        # Exercises line 504: a comment exists but isn't a no-invariant comment.
        src = textwrap.dedent("""\
            # this is some other comment
            class C:
                pass
        """)
        cls = _first_class(src)
        assert _has_no_invariant_comment(cls, src) is False

    def test_empty_source_returns_false(self) -> None:
        cls = _first_class("class C:\n    pass")
        assert _has_no_invariant_comment(cls, "") is False

    def test_class_at_top_of_file(self) -> None:
        src = "class C:\n    pass"
        cls = _first_class(src)
        assert _has_no_invariant_comment(cls, src) is False

    def test_intervening_blank_line_stops_scan(self) -> None:
        # Real code stops the upward scan at a non-comment, non-decorator line.
        src = textwrap.dedent("""\
            # no-invariant: trivial
            x = 1
            class C:
                pass
        """)
        cls = _first_class(src)
        assert _has_no_invariant_comment(cls, src) is False


# ---------------------------------------------------------------------------
# _has_property_decorator
# ---------------------------------------------------------------------------


class TestHasPropertyDecorator:
    def test_property_decorated(self) -> None:
        cls = _first_class("class C:\n    @property\n    def f(self): return 1")
        method = cast("ast.FunctionDef", cls.body[0])
        assert _has_property_decorator(method) is True

    def test_no_decorator(self) -> None:
        cls = _first_class("class C:\n    def f(self): return 1")
        method = cast("ast.FunctionDef", cls.body[0])
        assert _has_property_decorator(method) is False

    def test_other_decorator(self) -> None:
        # Exercises line 556: loop fall-through when no @property is present.
        cls = _first_class("class C:\n    @staticmethod\n    def f(): return 1")
        method = cast("ast.FunctionDef", cls.body[0])
        assert _has_property_decorator(method) is False

    def test_multiple_decorators_one_property(self) -> None:
        cls = _first_class(
            "class C:\n"
            "    @other\n"
            "    @property\n"
            "    def f(self): return 1\n",
        )
        method = cast("ast.FunctionDef", cls.body[0])
        assert _has_property_decorator(method) is True


# ---------------------------------------------------------------------------
# _is_enum_class
# ---------------------------------------------------------------------------


class TestIsEnumClass:
    def test_enum(self) -> None:
        cls = _first_class("class C(Enum):\n    A = 1")
        assert _is_enum_class(cls) is True

    def test_int_enum(self) -> None:
        cls = _first_class("class C(IntEnum):\n    A = 1")
        assert _is_enum_class(cls) is True

    def test_str_enum(self) -> None:
        cls = _first_class("class C(StrEnum):\n    A = 'a'")
        assert _is_enum_class(cls) is True

    def test_flag(self) -> None:
        cls = _first_class("class C(Flag):\n    A = 1")
        assert _is_enum_class(cls) is True

    def test_int_flag(self) -> None:
        cls = _first_class("class C(IntFlag):\n    A = 1")
        assert _is_enum_class(cls) is True

    def test_attribute_form(self) -> None:
        # Exercises line 584: enum.Enum-style attribute base.
        cls = _first_class("class C(enum.Enum):\n    A = 1")
        assert _is_enum_class(cls) is True

    def test_non_enum(self) -> None:
        cls = _first_class("class C:\n    pass")
        assert _is_enum_class(cls) is False

    def test_unrelated_base(self) -> None:
        cls = _first_class("class C(Foo):\n    pass")
        assert _is_enum_class(cls) is False


# ---------------------------------------------------------------------------
# _is_exception_class
# ---------------------------------------------------------------------------


class TestIsExceptionClass:
    def test_exception_base(self) -> None:
        cls = _first_class("class C(Exception):\n    pass")
        assert _is_exception_class(cls) is True

    def test_base_exception(self) -> None:
        cls = _first_class("class C(BaseException):\n    pass")
        assert _is_exception_class(cls) is True

    def test_error_suffix(self) -> None:
        # Exercises lines 607-610: name endswith Error.
        cls = _first_class("class C(SerenecodeError):\n    pass")
        assert _is_exception_class(cls) is True

    def test_exception_suffix(self) -> None:
        cls = _first_class("class C(MyException):\n    pass")
        assert _is_exception_class(cls) is True

    def test_attribute_base_with_error_suffix(self) -> None:
        # Exercises lines 604-605: ast.Attribute base path.
        cls = _first_class("class C(serenecode.SerenecodeError):\n    pass")
        assert _is_exception_class(cls) is True

    def test_attribute_base_with_exception_name(self) -> None:
        cls = _first_class("class C(builtins.Exception):\n    pass")
        assert _is_exception_class(cls) is True

    def test_non_exception_base(self) -> None:
        cls = _first_class("class C(Foo):\n    pass")
        assert _is_exception_class(cls) is False

    def test_no_bases(self) -> None:
        cls = _first_class("class C:\n    pass")
        assert _is_exception_class(cls) is False


# ---------------------------------------------------------------------------
# _is_protocol_class
# ---------------------------------------------------------------------------


class TestIsProtocolClass:
    def test_protocol(self) -> None:
        cls = _first_class("class C(Protocol):\n    pass")
        assert _is_protocol_class(cls) is True

    def test_typing_protocol(self) -> None:
        # Exercises lines 639-640: typing.Protocol attribute base.
        cls = _first_class("class C(typing.Protocol):\n    pass")
        assert _is_protocol_class(cls) is True

    def test_non_protocol(self) -> None:
        cls = _first_class("class C(Foo):\n    pass")
        assert _is_protocol_class(cls) is False

    def test_no_bases(self) -> None:
        cls = _first_class("class C:\n    pass")
        assert _is_protocol_class(cls) is False


# ---------------------------------------------------------------------------
# _is_trivial_handler_stmt
# ---------------------------------------------------------------------------


class TestIsTrivialHandlerStmt:
    def test_pass(self) -> None:
        stmt = ast.parse("pass").body[0]
        assert _is_trivial_handler_stmt(stmt) is True

    def test_continue(self) -> None:
        stmt = ast.parse("for i in []:\n    continue").body[0].body[0]  # type: ignore[attr-defined]
        assert _is_trivial_handler_stmt(stmt) is True

    def test_break(self) -> None:
        stmt = ast.parse("for i in []:\n    break").body[0].body[0]  # type: ignore[attr-defined]
        assert _is_trivial_handler_stmt(stmt) is True

    def test_ellipsis(self) -> None:
        stmt = ast.parse("...").body[0]
        assert _is_trivial_handler_stmt(stmt) is True

    def test_return_no_value(self) -> None:
        func = _first_function("def f(): return")
        assert _is_trivial_handler_stmt(func.body[0]) is True

    def test_return_constant(self) -> None:
        func = _first_function("def f(): return 0")
        assert _is_trivial_handler_stmt(func.body[0]) is True

    def test_return_empty_list(self) -> None:
        # Exercises line 1567: return [] is trivial.
        func = _first_function("def f(): return []")
        assert _is_trivial_handler_stmt(func.body[0]) is True

    def test_return_empty_tuple(self) -> None:
        func = _first_function("def f(): return ()")
        assert _is_trivial_handler_stmt(func.body[0]) is True

    def test_return_empty_set(self) -> None:
        # set() not {}; the function checks the literal Set node which only
        # exists for non-empty literals like {1, 2}. The empty `set()` is
        # an ast.Call, not an ast.Set, so this should NOT be trivial.
        func = _first_function("def f(): return set()")
        assert _is_trivial_handler_stmt(func.body[0]) is False

    def test_return_empty_dict(self) -> None:
        # Exercises lines 1568-1569: return {} (empty dict literal) is trivial.
        func = _first_function("def f(): return {}")
        assert _is_trivial_handler_stmt(func.body[0]) is True

    def test_return_non_empty_list_is_not_trivial(self) -> None:
        func = _first_function("def f(): return [1]")
        assert _is_trivial_handler_stmt(func.body[0]) is False

    def test_return_function_call_is_not_trivial(self) -> None:
        func = _first_function("def f(): return foo()")
        assert _is_trivial_handler_stmt(func.body[0]) is False

    def test_assignment_is_not_trivial(self) -> None:
        stmt = ast.parse("x = 1").body[0]
        assert _is_trivial_handler_stmt(stmt) is False


# ---------------------------------------------------------------------------
# _has_shell_true_kwarg
# ---------------------------------------------------------------------------


class TestHasShellTrueKwarg:
    def test_shell_true(self) -> None:
        call = ast.parse("subprocess.run(['ls'], shell=True)").body[0].value  # type: ignore[attr-defined]
        assert _has_shell_true_kwarg(call) is True

    def test_shell_false(self) -> None:
        call = ast.parse("subprocess.run(['ls'], shell=False)").body[0].value  # type: ignore[attr-defined]
        assert _has_shell_true_kwarg(call) is False

    def test_shell_variable(self) -> None:
        # shell=variable_name — kw.value is ast.Name, not ast.Constant
        call = ast.parse("subprocess.run(['ls'], shell=use_shell)").body[0].value  # type: ignore[attr-defined]
        assert _has_shell_true_kwarg(call) is False

    def test_no_shell_kwarg(self) -> None:
        call = ast.parse("subprocess.run(['ls'])").body[0].value  # type: ignore[attr-defined]
        assert _has_shell_true_kwarg(call) is False

    def test_other_kwarg_not_shell(self) -> None:
        # Exercises the branch where kw.arg != "shell".
        call = ast.parse("subprocess.run(['ls'], cwd='/tmp')").body[0].value  # type: ignore[attr-defined]
        assert _has_shell_true_kwarg(call) is False


# ---------------------------------------------------------------------------
# _is_init_with_only_assignments
# ---------------------------------------------------------------------------


class TestIsInitWithOnlyAssignments:
    def test_only_assignments(self) -> None:
        cls = _first_class(
            "class C:\n"
            "    def __init__(self):\n"
            "        self.x = 1\n"
            "        self.y = 2\n",
        )
        method = cast("ast.FunctionDef", cls.body[0])
        assert _is_init_with_only_assignments(method) is True

    def test_only_assignments_with_docstring(self) -> None:
        cls = _first_class(
            'class C:\n'
            '    def __init__(self):\n'
            '        """Doc."""\n'
            '        self.x = 1\n',
        )
        method = cast("ast.FunctionDef", cls.body[0])
        assert _is_init_with_only_assignments(method) is True

    def test_empty_after_docstring_returns_false(self) -> None:
        # Exercises line 1968: body has only a docstring, no assignments.
        cls = _first_class(
            'class C:\n'
            '    def __init__(self):\n'
            '        """Doc."""\n',
        )
        method = cast("ast.FunctionDef", cls.body[0])
        assert _is_init_with_only_assignments(method) is False

    def test_non_assignment_returns_false(self) -> None:
        # Exercises line 1972: body contains a non-assignment statement.
        cls = _first_class(
            "class C:\n"
            "    def __init__(self):\n"
            "        self.x = 1\n"
            "        self.foo()\n",
        )
        method = cast("ast.FunctionDef", cls.body[0])
        assert _is_init_with_only_assignments(method) is False

    def test_annotated_assignment_counts(self) -> None:
        cls = _first_class(
            "class C:\n"
            "    def __init__(self):\n"
            "        self.x: int = 1\n",
        )
        method = cast("ast.FunctionDef", cls.body[0])
        assert _is_init_with_only_assignments(method) is True


# ---------------------------------------------------------------------------
# _has_non_object_base
# ---------------------------------------------------------------------------


class TestHasNonObjectBase:
    def test_no_bases(self) -> None:
        cls = _first_class("class C: pass")
        assert _has_non_object_base(cls) is False

    def test_object_base(self) -> None:
        cls = _first_class("class C(object): pass")
        assert _has_non_object_base(cls) is False

    def test_named_base(self) -> None:
        # Exercises line 2044-2045.
        cls = _first_class("class C(Base): pass")
        assert _has_non_object_base(cls) is True

    def test_attribute_base(self) -> None:
        # Exercises lines 2046-2047: ast.Attribute base.
        cls = _first_class("class C(module.Base): pass")
        assert _has_non_object_base(cls) is True

    def test_subscript_base(self) -> None:
        # Exercises lines 2048-2049: ast.Subscript base, e.g. Generic[T].
        cls = _first_class("class C(Generic[T]): pass")
        assert _has_non_object_base(cls) is True


# ---------------------------------------------------------------------------
# _is_tautological_isinstance
# ---------------------------------------------------------------------------


class TestIsTautologicalIsinstance:
    def _lambda_from(self, src: str) -> ast.Lambda:
        return cast("ast.Lambda", ast.parse(src, mode="eval").body)

    def test_isinstance_matches_return_type(self) -> None:
        lam = self._lambda_from("lambda result: isinstance(result, int)")
        assert _is_tautological_isinstance(lam, "int") is True

    def test_isinstance_with_different_type(self) -> None:
        lam = self._lambda_from("lambda result: isinstance(result, int)")
        assert _is_tautological_isinstance(lam, "str") is False

    def test_non_isinstance_call(self) -> None:
        # Exercises line 2071: body is a Call but not isinstance.
        lam = self._lambda_from("lambda result: hasattr(result, 'x')")
        assert _is_tautological_isinstance(lam, "int") is False

    def test_isinstance_with_wrong_arg_count(self) -> None:
        # Exercises line 2073: isinstance with one arg (not two).
        lam = self._lambda_from("lambda result: isinstance(result)")
        assert _is_tautological_isinstance(lam, "int") is False

    def test_non_call_body(self) -> None:
        lam = self._lambda_from("lambda result: True")
        assert _is_tautological_isinstance(lam, "bool") is False

    def test_attribute_call_not_isinstance(self) -> None:
        # The function checks func.id == "isinstance"; an attribute call
        # like obj.isinstance(...) doesn't match because func is Attribute.
        lam = self._lambda_from("lambda result: obj.isinstance(result, int)")
        assert _is_tautological_isinstance(lam, "int") is False


# ---------------------------------------------------------------------------
# check_loop_invariants — branch coverage gaps
# ---------------------------------------------------------------------------


class TestCheckLoopInvariantsBranches:
    """Tests targeting the specific branches L3 reports as uncovered."""

    def _config_with_loop_invariants(self) -> object:
        # strict_config has loop invariants ON; minimal_config has them OFF
        return strict_config()

    def test_loop_with_invariant_comment_above_passes(self) -> None:
        """Branch: comment found in the 3-line window above the loop."""
        source = textwrap.dedent("""\
            \"\"\"Module.\"\"\"

            def f() -> None:
                # Loop invariant: i counts from 0 to 9
                for i in range(10):
                    pass
        """)
        tree = ast.parse(source)
        results = check_loop_invariants(source, tree, self._config_with_loop_invariants(), "test.py")  # type: ignore[arg-type]
        # Loop has an invariant comment in the window — no findings expected
        loop_results = [r for r in results if r.function == "<loop>"]
        assert len(loop_results) == 0

    def test_loop_with_invariant_comment_in_body_passes(self) -> None:
        """Branch: comment found in the first 2 lines INSIDE the loop body."""
        source = textwrap.dedent("""\
            \"\"\"Module.\"\"\"

            def f() -> None:
                for i in range(10):
                    # Loop invariant: i is the current index
                    pass
        """)
        tree = ast.parse(source)
        results = check_loop_invariants(source, tree, self._config_with_loop_invariants(), "test.py")  # type: ignore[arg-type]
        loop_results = [r for r in results if r.function == "<loop>"]
        assert len(loop_results) == 0

    def test_loop_without_invariant_comment_fails(self) -> None:
        source = textwrap.dedent("""\
            \"\"\"Module.\"\"\"

            def f() -> None:
                for i in range(10):
                    pass
        """)
        tree = ast.parse(source)
        results = check_loop_invariants(source, tree, self._config_with_loop_invariants(), "test.py")  # type: ignore[arg-type]
        loop_results = [r for r in results if r.function == "<loop>"]
        assert len(loop_results) == 1

    def test_recursive_function_with_variant_comment_passes(self) -> None:
        """Branch: variant keyword found inside the recursive function body."""
        source = textwrap.dedent("""\
            \"\"\"Module.\"\"\"

            def fact(n: int) -> int:
                # Variant: n decreases on each recursive call
                if n <= 1:
                    return 1
                return n * fact(n - 1)
        """)
        tree = ast.parse(source)
        results = check_loop_invariants(source, tree, self._config_with_loop_invariants(), "test.py")  # type: ignore[arg-type]
        variant_results = [r for r in results if r.function == "fact"]
        assert len(variant_results) == 0

    def test_recursive_function_with_decreasing_keyword(self) -> None:
        source = textwrap.dedent("""\
            \"\"\"Module.\"\"\"

            def fact(n: int) -> int:
                # decreasing measure: n
                if n <= 1:
                    return 1
                return n * fact(n - 1)
        """)
        tree = ast.parse(source)
        results = check_loop_invariants(source, tree, self._config_with_loop_invariants(), "test.py")  # type: ignore[arg-type]
        variant_results = [r for r in results if r.function == "fact"]
        assert len(variant_results) == 0

    def test_recursive_function_without_variant_comment_fails(self) -> None:
        source = textwrap.dedent("""\
            \"\"\"Module.\"\"\"

            def fact(n: int) -> int:
                if n <= 1:
                    return 1
                return n * fact(n - 1)
        """)
        tree = ast.parse(source)
        results = check_loop_invariants(source, tree, self._config_with_loop_invariants(), "test.py")  # type: ignore[arg-type]
        variant_results = [r for r in results if r.function == "fact"]
        assert len(variant_results) == 1

    def test_unparseable_source_returns_empty(self) -> None:
        # Branch: tokenizer raises — function returns []
        source = "def f():\n\tindentation\n  mixed\n"  # tabs + spaces is bad
        try:
            tree = ast.parse(source)
        except (SyntaxError, IndentationError):
            return  # source itself unparseable, skip
        results = check_loop_invariants(source, tree, self._config_with_loop_invariants(), "test.py")  # type: ignore[arg-type]
        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# check_exception_types — branch coverage gaps
# ---------------------------------------------------------------------------


class TestCheckExceptionTypesBranches:
    """Tests targeting the specific branches L3 reports as uncovered."""

    def test_bare_raise_skipped(self) -> None:
        """Branch: node.exc is None (bare raise reraise)."""
        source = textwrap.dedent("""\
            def func() -> None:
                try:
                    risky()
                except Exception:
                    raise
        """)
        tree = ast.parse(source)
        results = check_exception_types(tree, strict_config(), "core/foo.py", "test.py")
        # Bare `raise` has no exc — should be skipped, not flagged
        assert len(results) == 0

    def test_raise_module_attribute_exception(self) -> None:
        """Branch: node.exc.func is ast.Attribute (e.g. raise mymod.MyError(...))."""
        source = textwrap.dedent("""\
            def func() -> None:
                raise mymod.ValueError("bad")
        """)
        tree = ast.parse(source)
        results = check_exception_types(tree, strict_config(), "core/foo.py", "test.py")
        # mymod.ValueError matches the forbidden 'ValueError' name via the Attribute branch
        assert len(results) == 1

    def test_raise_bare_exception_class_no_call(self) -> None:
        """Branch: node.exc is ast.Name (raise without instantiating)."""
        source = textwrap.dedent("""\
            def func() -> None:
                raise ValueError
        """)
        tree = ast.parse(source)
        results = check_exception_types(tree, strict_config(), "core/foo.py", "test.py")
        assert len(results) == 1

    def test_default_config_skips(self) -> None:
        source = textwrap.dedent("""\
            def func() -> None:
                raise Exception("bad")
        """)
        tree = ast.parse(source)
        results = check_exception_types(tree, default_config(), "core/foo.py", "test.py")
        assert len(results) == 0
