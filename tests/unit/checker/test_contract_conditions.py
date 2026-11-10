"""Tests for the shape analysis of icontract decorator arguments."""

from __future__ import annotations

import ast

import pytest

from serenecode.checker.contract_conditions import (
    contract_condition,
    is_always_true,
    is_disabled_contract,
)


def _decorator(source: str) -> ast.expr:
    """Parse a single decorator expression."""
    return ast.parse(source, mode="eval").body


def _condition(source: str) -> ast.expr:
    """Parse a contract condition expression."""
    return ast.parse(source, mode="eval").body


class TestContractCondition:
    """The condition is found whichever way it was passed."""

    def test_positional_condition(self) -> None:
        """Verifies: REQ-060"""
        condition = contract_condition(_decorator('icontract.require(lambda x: x > 0, "positive")'))
        assert isinstance(condition, ast.Lambda)

    def test_keyword_condition(self) -> None:
        """Verifies: REQ-060"""
        condition = contract_condition(
            _decorator('icontract.require(description="positive", condition=lambda x: x > 0)'),
        )
        assert isinstance(condition, ast.Lambda)
        assert [arg.arg for arg in condition.args.args] == ["x"]

    def test_decorator_without_a_condition(self) -> None:
        assert contract_condition(_decorator('icontract.require(description="nothing")')) is None

    def test_bare_name_decorator_has_no_condition(self) -> None:
        assert contract_condition(_decorator("staticmethod")) is None


class TestIsDisabledContract:
    """Only a literal enabled=False switches a contract off."""

    def test_literal_false_is_disabled(self) -> None:
        """Verifies: REQ-059"""
        assert is_disabled_contract(_decorator('icontract.require(lambda x: x > 0, "p", enabled=False)'))

    @pytest.mark.parametrize(
        "source",
        [
            'icontract.require(lambda x: x > 0, "p")',
            'icontract.require(lambda x: x > 0, "p", enabled=True)',
            'icontract.require(lambda x: x > 0, "p", enabled=SLOW_CONTRACTS)',
            "icontract.require",
        ],
    )
    def test_enabled_or_unknowable_is_not_disabled(self, source: str) -> None:
        """Verifies: REQ-059"""
        assert not is_disabled_contract(_decorator(source))


class TestIsAlwaysTrue:
    """A condition that cannot be false is a tautology."""

    @pytest.mark.parametrize(
        "source",
        [
            "lambda result: True",
            "lambda: True",
            "lambda x: 1",
            'lambda x: "non-empty"',
            "lambda x: not False",
            "lambda x: not 0",
            "lambda x: True and True",
            "lambda x: x > 0 or True",
            "lambda x: True or x > 0",
            "lambda x: 1 == 1",
            "lambda x: 0 < 1",
            "lambda x: x is x",
            "lambda self: self.items is self.items",
            "lambda x: isinstance(x, object)",
            "lambda x: x is None or isinstance(x, object)",
            "lambda x: not (1 > 2)",
        ],
    )
    def test_always_true_shapes(self, source: str) -> None:
        """Verifies: REQ-061"""
        assert is_always_true(_condition(source))

    @pytest.mark.parametrize(
        "source",
        [
            "lambda x: x > 0",
            "lambda x: x == x",  # false for NaN: a real constraint on floats
            "lambda x: x <= x",
            "lambda x: x >= x",
            "lambda x: False",
            "lambda x: 1 > 2",
            "lambda x: True and x > 0",
            "lambda x: x > 0 or x < 0",
            "lambda x: isinstance(x, int)",
            "lambda x: isinstance(x, object, object)",
            "lambda x: 0 < x < 10",
            'lambda x: 1 < "a"',  # unorderable literals have no fixed value
            "lambda x: len(x)",
            "is_positive",
        ],
    )
    def test_conditions_that_can_be_false(self, source: str) -> None:
        """Verifies: REQ-061"""
        assert not is_always_true(_condition(source))
