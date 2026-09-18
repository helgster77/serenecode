"""Minimal tests for core.hypothesis_refinement module.

Note: hypothesis_refinement is re-exported via hypothesis_strategies.
This test verifies the re-exported symbols are accessible.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from serenecode.adapters.hypothesis_adapter import _build_unsatisfiable_finding
from serenecode.adapters.hypothesis_strategies import (
    _refine_strategies_with_preconditions,
)
from serenecode.support.hypothesis_refinement import (
    _numeric_bounds_from_source,
    _try_numeric_bounds,
)


def test_refine_strategies_with_preconditions_is_callable() -> None:
    """The re-exported refinement function is callable."""
    assert callable(_refine_strategies_with_preconditions)


class TestNumericBoundRefinement:
    """Bounds are read from the condition AST, not matched by regex."""

    @staticmethod
    def _derive(source: str, param: str, annotation: object) -> object:
        """Return the strategy _try_numeric_bounds derives, or None."""
        strategies = {
            param: st.floats(
                min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False,
            ),
        }
        derived = _try_numeric_bounds(source, param, strategies, {param: annotation})
        return strategies[param] if derived else None

    def test_open_interval_becomes_excluded_endpoints(self) -> None:
        """A strict float bound excludes the endpoint instead of shifting by 1.

        Verifies: REQ-048

        Measured before the fix: `< 1.0` parsed as `< 1`, was shifted to a
        maximum of 0, and produced floats(-1e6, 0.0) — a domain disjoint from
        the contract, so Level 4 reported Unsatisfiable.
        """
        strategy = self._derive("lambda r: 0.0 < r < 1.0", "r", float)
        assert strategy is not None
        rendered = repr(strategy)
        assert "min_value=0.0" in rendered
        assert "max_value=1.0" in rendered
        assert "exclude_min=True" in rendered
        assert "exclude_max=True" in rendered

    def test_open_interval_generates_only_interior_values(self) -> None:
        """Every example drawn satisfies the original condition.

        Verifies: REQ-048
        """
        strategy = self._derive("lambda r: 0.0 < r < 1.0", "r", float)
        assert strategy is not None

        @given(strategy)
        @settings(max_examples=25, deadline=None)
        def check(value: float) -> None:
            assert 0.0 < value < 1.0

        check()

    def test_lower_bound_left_of_the_parameter_is_kept(self) -> None:
        """`0.0 <= x` is a bound, even written on the left.

        Verifies: REQ-048

        Measured before the fix: the lower bound was dropped entirely and the
        closed interval became floats(-1e6, 1.0).
        """
        strategy = self._derive("lambda r: 0.0 <= r <= 1.0", "r", float)
        assert strategy is not None
        assert "min_value=0.0" in repr(strategy)
        assert "max_value=1.0" in repr(strategy)

    def test_decimal_literals_keep_their_fraction(self) -> None:
        """`>= 0.5` means 0.5, not 0.

        Verifies: REQ-048
        """
        strategy = self._derive("lambda p: p >= 0.5", "p", float)
        assert strategy is not None
        assert "min_value=0.5" in repr(strategy)

    def test_negative_literals_are_bounds(self) -> None:
        """A negated literal is still a numeric bound.

        Verifies: REQ-048
        """
        strategy = self._derive("lambda x: x > -2.5", "x", float)
        assert strategy is not None
        assert "min_value=-2.5" in repr(strategy)

    def test_integer_bounds_still_shift_by_one(self) -> None:
        """Excluding an integer endpoint is a shift of one.

        Verifies: REQ-048
        """
        strategy = self._derive("lambda n: n > 0", "n", int)
        assert strategy is not None
        assert "min_value=1" in repr(strategy)

    def test_reversed_integer_bound_is_read(self) -> None:
        """`0 < n` bounds n below.

        Verifies: REQ-048
        """
        strategy = self._derive("lambda n: 0 < n", "n", int)
        assert strategy is not None
        assert "min_value=1" in repr(strategy)

    def test_chained_integer_range_is_exact(self) -> None:
        """A closed integer range maps to both endpoints.

        Verifies: REQ-048
        """
        strategy = self._derive("lambda n: 1 <= n <= 6", "n", int)
        assert strategy is not None
        assert "min_value=1" in repr(strategy)
        assert "max_value=6" in repr(strategy)

    def test_tighter_of_two_bounds_wins(self) -> None:
        """Conjoined bounds on one parameter are combined.

        Verifies: REQ-048
        """
        strategy = self._derive("lambda x: x > 0 and x > 10", "x", float)
        assert strategy is not None
        assert "min_value=10.0" in repr(strategy)

    def test_empty_range_derives_no_strategy(self) -> None:
        """An unsatisfiable range falls back to filtering.

        Verifies: REQ-048
        """
        assert self._derive("lambda x: 5 < x < 5", "x", float) is None

    def test_boolean_literals_are_not_bounds(self) -> None:
        """`True` is not a numeric bound even though bool subclasses int.

        Verifies: REQ-048
        """
        assert self._derive("lambda flag: flag > True", "flag", int) is None

    def test_non_numeric_parameters_are_left_alone(self) -> None:
        """A length comparison is not a bound on the string itself.

        Verifies: REQ-048
        """
        assert self._derive("lambda t: len(t) > 0", "t", str) is None


class TestUnsatisfiableIsReportedAsUnverified:
    """An exhausted generator is a scope limit, not a defect."""

    def test_finding_is_skipped_and_does_not_fail(self) -> None:
        """Unsatisfiable no longer reports a crash.

        Verifies: REQ-049
        """
        finding = _build_unsatisfiable_finding("decay", "pkg.decay")
        assert finding.finding_type == "skipped"
        assert finding.passed is True

    def test_message_does_not_advise_weakening_the_contract(self) -> None:
        """The suggested remedy must not be a looser precondition.

        Verifies: REQ-049
        """
        message = _build_unsatisfiable_finding("decay", "pkg.decay").message
        assert "nothing was verified" in message
        assert "do not weaken the contract" in message
        assert "crash" not in message


class TestBoundParsingEdges:
    """Shapes that look like bounds but are not."""

    @staticmethod
    def _bounds(source: str, param: str) -> object:
        return _numeric_bounds_from_source(source, param)

    def test_negated_non_literal_is_not_a_bound(self) -> None:
        """`-other` is not a numeric literal.

        Verifies: REQ-048
        """
        assert self._bounds("lambda x: x > -other", "x") is None

    def test_comparison_against_an_expression_is_not_a_bound(self) -> None:
        """A relation between two parameters has no literal bound.

        Verifies: REQ-048
        """
        assert self._bounds("lambda lo: lo < hi", "lo") is None

    def test_unparsable_source_yields_no_bounds(self) -> None:
        """A source fragment that does not parse is not a bound.

        Verifies: REQ-048
        """
        assert self._bounds("lambda x: x <", "x") is None

    def test_equal_bounds_merge_to_the_stricter_one(self) -> None:
        """`x >= 0 and x > 0` keeps the exclusive endpoint.

        Verifies: REQ-048
        """
        bounds = self._bounds("lambda x: x >= 0 and x > 0", "x")
        assert bounds == ((0.0, True), None)

    def test_looser_upper_bound_does_not_replace_the_tighter_one(self) -> None:
        """The smaller upper bound wins regardless of source order.

        Verifies: REQ-048
        """
        assert self._bounds("lambda x: x < 5 and x < 50", "x") == (None, (5.0, True))
        assert self._bounds("lambda x: x < 50 and x < 5", "x") == (None, (5.0, True))

    def test_looser_lower_bound_does_not_replace_the_tighter_one(self) -> None:
        """The larger lower bound wins regardless of source order.

        Verifies: REQ-048
        """
        assert self._bounds("lambda x: x > 50 and x > 5", "x") == ((50.0, True), None)
