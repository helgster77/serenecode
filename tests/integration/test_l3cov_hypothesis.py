"""L3 coverage-gap tests for hypothesis adapter/strategy/refinement helpers.

Targets specific uncovered lines and branches flagged by Level 3 verification:

- hypothesis_adapter: _is_pathlike_annotation, _annotation_may_represent_path_text,
  _handle_violation, _handle_generic_exception, _build_postcondition_finding
- hypothesis_strategies: _strategy_for_icontract_names, _strategy_for_compositional_type
- hypothesis_refinement: _try_membership_pattern

Importing private helpers directly follows the established practice in
tests/integration/test_hypothesis_adapter_helpers.py.
"""

from __future__ import annotations

import inspect
import os
import pathlib
import typing
from unittest import mock

import icontract
import pytest

from serenecode.adapters.hypothesis_adapter import (
    _annotation_may_represent_path_text,
    _build_postcondition_finding,
    _handle_generic_exception,
    _handle_violation,
    _is_pathlike_annotation,
)
from serenecode.adapters.hypothesis_strategies import (
    _strategy_for_compositional_type,
    _strategy_for_icontract_names,
)
from serenecode.support.hypothesis_refinement import _try_membership_pattern


# ---------------------------------------------------------------------------
# Helpers to produce icontract.ViolationError instances
#
# The errors are constructed directly with messages frozen from real
# icontract output rather than by triggering live contracts: CrossHair
# (imported by other tests in the same pytest process) patches icontract
# and disables enforcement, which would make triggered violations vanish
# depending on test order. Direct construction is order-independent.
# ---------------------------------------------------------------------------


def _make_postcondition_violation() -> icontract.ViolationError:
    """Build a ViolationError matching icontract's postcondition format."""
    return icontract.ViolationError(
        "File <string>, line 3 in <module>:\n"
        "result must exceed input: result > x:\n"
        "result was 4\n"
        "x was 5"
    )


def _make_precondition_violation() -> icontract.ViolationError:
    """Build a ViolationError whose message mentions 'Precondition'."""
    return icontract.ViolationError(
        "File <string>, line 3 in <module>:\n"
        "Precondition: x must be positive: x > 0:\n"
        "x was -3"
    )


# ---------------------------------------------------------------------------
# _is_pathlike_annotation
# ---------------------------------------------------------------------------


class TestIsPathlikeAnnotation:
    def test_empty_annotation_is_not_pathlike(self) -> None:
        assert _is_pathlike_annotation(inspect.Parameter.empty) is False

    def test_forward_ref_to_path_is_pathlike(self) -> None:
        assert _is_pathlike_annotation(typing.ForwardRef("Path")) is True

    def test_forward_ref_to_int_is_not_pathlike(self) -> None:
        assert _is_pathlike_annotation(typing.ForwardRef("int")) is False

    def test_string_annotation_with_pathlib_path(self) -> None:
        assert _is_pathlike_annotation("pathlib.Path") is True

    def test_string_annotation_without_path_names(self) -> None:
        assert _is_pathlike_annotation("dict[str, int]") is False

    def test_union_containing_path(self) -> None:
        assert _is_pathlike_annotation(pathlib.Path | None) is True

    def test_union_without_path(self) -> None:
        assert _is_pathlike_annotation(int | None) is False

    def test_generic_with_pathlike_origin(self) -> None:
        # os.PathLike[str] has origin os.PathLike, which is itself pathlike.
        assert _is_pathlike_annotation(os.PathLike[str]) is True

    def test_generic_with_pathlike_argument(self) -> None:
        assert _is_pathlike_annotation(list[pathlib.Path]) is True

    def test_generic_with_ellipsis_and_path(self) -> None:
        assert _is_pathlike_annotation(tuple[pathlib.Path, ...]) is True

    def test_generic_without_pathlike_parts(self) -> None:
        assert _is_pathlike_annotation(list[int]) is False

    def test_pathlib_classes_are_pathlike(self) -> None:
        assert _is_pathlike_annotation(pathlib.Path) is True
        assert _is_pathlike_annotation(pathlib.PurePosixPath) is True

    def test_pathlike_subclass_via_issubclass(self) -> None:
        class MyPath(pathlib.PurePath):
            pass

        assert _is_pathlike_annotation(MyPath) is True

    def test_plain_class_is_not_pathlike(self) -> None:
        assert _is_pathlike_annotation(str) is False

    def test_pseudo_class_raising_type_error_returns_false(self) -> None:
        # Mock(spec=type) passes inspect.isclass but makes issubclass raise
        # TypeError, exercising the defensive except branch.
        pseudo_class = mock.Mock(spec=type)
        assert _is_pathlike_annotation(pseudo_class) is False

    def test_non_class_object_returns_false(self) -> None:
        assert _is_pathlike_annotation(42) is False


# ---------------------------------------------------------------------------
# _annotation_may_represent_path_text
# ---------------------------------------------------------------------------


class TestAnnotationMayRepresentPathText:
    def test_textual_base_annotations_accepted(self) -> None:
        assert _annotation_may_represent_path_text(inspect.Parameter.empty) is True
        assert _annotation_may_represent_path_text(str) is True
        assert _annotation_may_represent_path_text(bytes) is True
        assert _annotation_may_represent_path_text(object) is True

    def test_forward_ref_to_str_accepted(self) -> None:
        assert _annotation_may_represent_path_text(typing.ForwardRef("str")) is True

    def test_forward_ref_to_path_accepted(self) -> None:
        assert _annotation_may_represent_path_text(typing.ForwardRef("Path")) is True

    def test_forward_ref_to_int_rejected(self) -> None:
        assert _annotation_may_represent_path_text(typing.ForwardRef("int")) is False

    def test_string_annotation_with_pathlike_name(self) -> None:
        assert _annotation_may_represent_path_text("pathlib.Path") is True

    def test_string_annotation_without_path_names(self) -> None:
        assert _annotation_may_represent_path_text("int") is False

    def test_pathlike_annotation_accepted(self) -> None:
        assert _annotation_may_represent_path_text(pathlib.Path) is True

    def test_union_with_textual_member(self) -> None:
        assert _annotation_may_represent_path_text(int | str) is True

    def test_union_without_textual_member(self) -> None:
        assert _annotation_may_represent_path_text(int | float) is False

    def test_list_of_str_accepted(self) -> None:
        assert _annotation_may_represent_path_text(list[str]) is True

    def test_list_of_int_rejected(self) -> None:
        assert _annotation_may_represent_path_text(list[int]) is False

    def test_bare_list_origin_rejected(self) -> None:
        # typing.List has origin list but zero args -> len(args) == 1 is False.
        assert _annotation_may_represent_path_text(typing.List) is False  # noqa: UP006

    def test_set_and_frozenset_of_str_accepted(self) -> None:
        assert _annotation_may_represent_path_text(set[str]) is True
        assert _annotation_may_represent_path_text(frozenset[str]) is True

    def test_variadic_tuple_of_str_accepted(self) -> None:
        assert _annotation_may_represent_path_text(tuple[str, ...]) is True

    def test_variadic_tuple_of_int_rejected(self) -> None:
        assert _annotation_may_represent_path_text(tuple[int, ...]) is False

    def test_fixed_tuple_with_str_member_accepted(self) -> None:
        assert _annotation_may_represent_path_text(tuple[int, str]) is True

    def test_fixed_tuple_without_str_member_rejected(self) -> None:
        assert _annotation_may_represent_path_text(tuple[int, float]) is False

    def test_longer_tuple_with_str_member_accepted(self) -> None:
        assert _annotation_may_represent_path_text(tuple[int, int, str]) is True

    def test_dict_with_str_key_accepted(self) -> None:
        assert _annotation_may_represent_path_text(dict[str, int]) is True

    def test_dict_without_textual_parts_rejected(self) -> None:
        assert _annotation_may_represent_path_text(dict[int, float]) is False

    def test_bare_dict_origin_rejected(self) -> None:
        # typing.Dict has origin dict but zero args -> len(args) == 2 is False.
        assert _annotation_may_represent_path_text(typing.Dict) is False  # noqa: UP006

    def test_plain_int_rejected(self) -> None:
        assert _annotation_may_represent_path_text(int) is False


# ---------------------------------------------------------------------------
# _handle_violation
# ---------------------------------------------------------------------------


class TestHandleViolation:
    def test_precondition_violation_becomes_skipped(self) -> None:
        exc = _make_precondition_violation()
        assert "Precondition" in str(exc)
        finding = _handle_violation("my_func", "pkg.mod", exc)
        assert finding.passed is True
        assert finding.finding_type == "skipped"
        assert "my_func" in finding.message
        assert "preconditions too restrictive" in finding.message

    def test_postcondition_violation_becomes_failure(self) -> None:
        exc = _make_postcondition_violation()
        assert "Precondition" not in str(exc)
        finding = _handle_violation("my_func", "pkg.mod", exc)
        assert finding.passed is False
        assert finding.finding_type == "postcondition_violated"
        assert "Postcondition violated for 'my_func'" in finding.message


# ---------------------------------------------------------------------------
# _handle_generic_exception
# ---------------------------------------------------------------------------


class TestHandleGenericException:
    def test_wrapped_violation_is_unwrapped(self) -> None:
        violation = _make_postcondition_violation()
        wrapper = ValueError("hypothesis wrapper")
        wrapper.__cause__ = violation
        finding = _handle_generic_exception("my_func", "pkg.mod", wrapper)
        assert finding.passed is False
        assert finding.finding_type == "postcondition_violated"
        assert "Postcondition violated for 'my_func'" in finding.message

    def test_plain_exception_becomes_crash(self) -> None:
        finding = _handle_generic_exception(
            "my_func", "pkg.mod", ValueError("boom"),
        )
        assert finding.passed is False
        assert finding.finding_type == "crash"
        assert "crashed during testing" in finding.message
        assert finding.exception_type == "ValueError"
        assert finding.exception_message == "boom"


# ---------------------------------------------------------------------------
# _build_postcondition_finding
# ---------------------------------------------------------------------------


class TestBuildPostconditionFinding:
    def test_real_violation_extracts_condition_and_counterexample(self) -> None:
        exc = _make_postcondition_violation()
        finding = _build_postcondition_finding("my_func", "pkg.mod", exc)
        assert finding.passed is False
        assert finding.finding_type == "postcondition_violated"
        assert "condition 'result > x'" in finding.message
        assert finding.counterexample is not None
        assert finding.counterexample.get("x") == "5"
        assert finding.counterexample.get("result") == "4"
        assert "x=5" in finding.message

    def test_bare_violation_falls_back_to_error_string(self) -> None:
        exc = icontract.ViolationError("opaque single-line failure")
        finding = _build_postcondition_finding("my_func", "pkg.mod", exc)
        assert finding.passed is False
        assert finding.counterexample is None
        assert (
            finding.message
            == "Postcondition violated for 'my_func': opaque single-line failure"
        )


# ---------------------------------------------------------------------------
# _strategy_for_icontract_names
# ---------------------------------------------------------------------------


class TestStrategyForIcontractNames:
    def test_returns_hypothesis_strategy(self) -> None:
        strategy = _strategy_for_icontract_names()
        assert strategy is not None
        assert hasattr(strategy, "map")


# ---------------------------------------------------------------------------
# _strategy_for_compositional_type
# ---------------------------------------------------------------------------


class TestStrategyForCompositionalType:
    @pytest.mark.parametrize(
        "type_name",
        [
            "MethodSignature",
            "ParameterInfo",
            "FunctionInfo",
            "ClassInfo",
            "ProtocolInfo",
            "ModuleInfo",
        ],
    )
    def test_known_types_return_strategy(self, type_name: str) -> None:
        strategy = _strategy_for_compositional_type(type_name, frozenset())
        assert strategy is not None
        assert hasattr(strategy, "map")

    def test_unknown_type_returns_none(self) -> None:
        assert _strategy_for_compositional_type("NotAType", frozenset()) is None


# ---------------------------------------------------------------------------
# _try_membership_pattern
# ---------------------------------------------------------------------------


class TestTryMembershipPattern:
    def test_literal_collection_builds_sampled_from(self) -> None:
        strategies: dict[str, object] = {}
        matched = _try_membership_pattern(
            "lambda mode: mode in ('fast', 'slow')", "mode", strategies,
        )
        assert matched is True
        assert "mode" in strategies
        assert "sampled_from" in repr(strategies["mode"])

    def test_non_literal_collection_returns_false(self) -> None:
        # `in (...)` matches, but the members are names, not literals, so
        # _parse_literal_collection yields nothing and the helper declines.
        strategies: dict[str, object] = {}
        matched = _try_membership_pattern(
            "lambda mode: mode in (allowed, other)", "mode", strategies,
        )
        assert matched is False
        assert strategies == {}

    def test_source_without_membership_returns_false(self) -> None:
        strategies: dict[str, object] = {}
        matched = _try_membership_pattern("lambda x: x > 0", "x", strategies)
        assert matched is False
        assert strategies == {}
