"""Shared test configuration and fixtures for Serenecode tests."""

from __future__ import annotations

import os
from pathlib import Path

import icontract
import pytest
from hypothesis import HealthCheck, settings


def icontract_enabled() -> bool:
    """Check if icontract invariant checking is still active.

    CrossHair monkey-patches icontract internals when imported,
    which can disable invariant checking in the same process.
    """
    try:
        @icontract.invariant(lambda self: self.x > 0, "x must be positive")
        class _Probe:
            def __init__(self, x: int) -> None:
                self.x = x
        _Probe(-1)
        return False  # invariant was not enforced
    except icontract.ViolationError:
        return True  # invariant is working


def assert_violation_or_skip(fn: object) -> None:
    """Assert that calling fn raises ViolationError, or skip if icontract is patched.

    Use this instead of ``pytest.raises(icontract.ViolationError)``
    in tests that may run after CrossHair has been imported.
    """
    try:
        fn()  # type: ignore[operator]
    except icontract.ViolationError:
        return  # expected
    if not icontract_enabled():
        pytest.skip("icontract invariants disabled by CrossHair monkey-patching")
    pytest.fail("Expected icontract.ViolationError was not raised")

from serenecode.models import (
    CheckStatus,
    Detail,
    FunctionResult,
    VerificationLevel,
)

# ---------------------------------------------------------------------------
# Hypothesis profiles
# ---------------------------------------------------------------------------

settings.register_profile("ci", max_examples=200, deadline=None)
settings.register_profile("dev", max_examples=50, deadline=None)
settings.register_profile(
    "debug",
    max_examples=10,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "dev"))

# ---------------------------------------------------------------------------
# Hypothesis strategies for domain types
# ---------------------------------------------------------------------------

try:
    from hypothesis import strategies as st

    verification_levels = st.sampled_from(list(VerificationLevel))
    check_statuses = st.sampled_from(list(CheckStatus))
    tool_names = st.sampled_from(["structural", "mypy", "hypothesis", "crosshair", "compositional"])
    finding_types = st.sampled_from(["violation", "counterexample", "timeout", "error", "verified"])

    detail_strategy = st.builds(
        Detail,
        level=verification_levels,
        tool=tool_names,
        finding_type=finding_types,
        message=st.text(min_size=1, max_size=100).filter(lambda s: s.strip()),
        counterexample=st.none() | st.dictionaries(
            st.text(min_size=1, max_size=10), st.integers(), max_size=3,
        ),
        suggestion=st.none() | st.text(min_size=1, max_size=50).filter(lambda s: s.strip()),
    )

    function_result_strategy = st.builds(
        FunctionResult,
        function=st.text(min_size=1, max_size=30).filter(lambda s: s.strip()),
        file=st.text(min_size=1, max_size=50).filter(lambda s: s.strip()),
        line=st.integers(min_value=1, max_value=10000),
        level_requested=st.integers(min_value=1, max_value=6),
        level_achieved=st.integers(min_value=0, max_value=6),
        status=check_statuses,
        details=st.tuples(detail_strategy).map(lambda t: t) | st.just(()),
    )
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Path fixtures
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures"
VALID_FIXTURES_DIR = FIXTURES_DIR / "valid"
INVALID_FIXTURES_DIR = FIXTURES_DIR / "invalid"
EDGE_CASE_FIXTURES_DIR = FIXTURES_DIR / "edge_cases"


@pytest.fixture()
def fixtures_dir() -> Path:
    """Return the path to the test fixtures directory."""
    return FIXTURES_DIR


@pytest.fixture()
def valid_fixtures_dir() -> Path:
    """Return the path to valid test fixtures."""
    return VALID_FIXTURES_DIR


@pytest.fixture()
def invalid_fixtures_dir() -> Path:
    """Return the path to invalid test fixtures."""
    return INVALID_FIXTURES_DIR


@pytest.fixture()
def edge_case_fixtures_dir() -> Path:
    """Return the path to edge case test fixtures."""
    return EDGE_CASE_FIXTURES_DIR


@pytest.fixture()
def tmp_project(tmp_path: Path) -> Path:
    """Create a temporary project directory for e2e tests."""
    project = tmp_path / "test_project"
    project.mkdir()
    return project
