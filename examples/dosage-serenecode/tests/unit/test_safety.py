"""Tests for daily safety and contraindication check functions.

Verifies: REQ-017, REQ-018, REQ-019, REQ-020, REQ-021, REQ-022, REQ-023, REQ-024
"""

import icontract
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from dosage.core.models import Drug
from dosage.core.safety import check_contraindications, check_daily_safety


def _make_drug(**overrides: object) -> Drug:
    """Build a Drug with sensible defaults."""
    defaults: dict[str, object] = dict(
        drug_id="ibuprofen",
        dose_per_kg=10.0,
        concentration_mg_per_ml=20.0,
        max_single_dose_mg=400.0,
        max_daily_dose_mg=1200.0,
        doses_per_day=3,
        contraindicated_with=set(),
    )
    defaults.update(overrides)
    return Drug(**defaults)  # type: ignore[arg-type]


# -- check_daily_safety (REQ-017, REQ-018, REQ-019) --


def test_daily_safety_within_limit() -> None:
    """Daily total within limit is safe.

    Verifies: REQ-017, REQ-018
    """
    drug = _make_drug(doses_per_day=3, max_daily_dose_mg=1200.0)
    result = check_daily_safety(200.0, drug)
    assert result.daily_total_mg == 600.0
    assert result.max_daily_mg == 1200.0
    assert result.is_safe is True


def test_daily_safety_exceeds_limit() -> None:
    """Daily total exceeding limit is unsafe.

    Verifies: REQ-017, REQ-018
    """
    drug = _make_drug(doses_per_day=4, max_daily_dose_mg=1200.0)
    result = check_daily_safety(400.0, drug)
    assert result.daily_total_mg == 1600.0
    assert result.is_safe is False


def test_daily_safety_exactly_at_limit() -> None:
    """Daily total exactly at limit is safe.

    Verifies: REQ-018
    """
    drug = _make_drug(doses_per_day=3, max_daily_dose_mg=1200.0)
    result = check_daily_safety(400.0, drug)
    assert result.daily_total_mg == 1200.0
    assert result.is_safe is True


def test_daily_safety_exact_computation() -> None:
    """Daily total is exactly dose * doses_per_day.

    Verifies: REQ-017
    """
    drug = _make_drug(doses_per_day=3, max_daily_dose_mg=10000.0)
    result = check_daily_safety(333.33, drug)
    assert result.daily_total_mg == 333.33 * 3


def test_daily_safety_utilization_percent() -> None:
    """Utilization percentage is correctly computed.

    Verifies: REQ-019
    """
    drug = _make_drug(doses_per_day=2, max_daily_dose_mg=1000.0)
    result = check_daily_safety(250.0, drug)
    assert result.utilization_pct == 50.0


def test_daily_safety_utilization_at_100_percent() -> None:
    """Utilization is 100% when daily total equals max.

    Verifies: REQ-019
    """
    drug = _make_drug(doses_per_day=3, max_daily_dose_mg=1200.0)
    result = check_daily_safety(400.0, drug)
    assert result.utilization_pct == 100.0
    assert result.is_safe is True


def test_daily_safety_utilization_above_100_when_unsafe() -> None:
    """Utilization exceeds 100% when unsafe.

    Verifies: REQ-019
    """
    drug = _make_drug(doses_per_day=4, max_daily_dose_mg=1200.0)
    result = check_daily_safety(400.0, drug)
    assert result.utilization_pct > 100.0
    assert result.is_safe is False


def test_daily_safety_zero_dose_rejected() -> None:
    """Zero dose is rejected.

    Verifies: REQ-025
    """
    drug = _make_drug()
    with pytest.raises(icontract.ViolationError):
        check_daily_safety(0.0, drug)


@given(
    dose=st.floats(min_value=0.01, max_value=5000.0, allow_nan=False),
    doses_per_day=st.integers(min_value=1, max_value=10),
    max_daily=st.floats(min_value=1.0, max_value=50000.0, allow_nan=False),
)
@settings(max_examples=200, deadline=None)
def test_daily_safety_utilization_consistency(
    dose: float, doses_per_day: int, max_daily: float
) -> None:
    """Utilization percentage is consistent with is_safe flag.

    Verifies: REQ-018, REQ-019
    """
    max_single = min(dose * 2, max_daily)
    if max_single <= 0:
        max_single = 1.0
    drug = _make_drug(
        doses_per_day=doses_per_day,
        max_daily_dose_mg=max_daily,
        max_single_dose_mg=max_single,
    )
    result = check_daily_safety(dose, drug)
    if result.is_safe:
        assert result.utilization_pct <= 100.0
    else:
        assert result.utilization_pct > 100.0


# -- check_contraindications (REQ-020 to REQ-024) --


def test_contraindications_no_conflicts() -> None:
    """No conflicts when no contraindications match.

    Verifies: REQ-020, REQ-021
    """
    drug = _make_drug(contraindicated_with={"warfarin", "aspirin"})
    result = check_contraindications(drug, ["metformin", "lisinopril"])
    assert result.is_safe is True
    assert result.conflicts == []


def test_contraindications_with_conflicts() -> None:
    """Conflicts detected when medications match contraindications.

    Verifies: REQ-020, REQ-021
    """
    drug = _make_drug(contraindicated_with={"warfarin", "aspirin"})
    result = check_contraindications(drug, ["warfarin", "metformin"])
    assert result.is_safe is False
    assert result.conflicts == ["warfarin"]


def test_contraindications_multiple_conflicts() -> None:
    """Multiple conflicts all detected.

    Verifies: REQ-020
    """
    drug = _make_drug(contraindicated_with={"warfarin", "aspirin", "naproxen"})
    result = check_contraindications(drug, ["warfarin", "aspirin", "metformin"])
    assert result.is_safe is False
    assert set(result.conflicts) == {"warfarin", "aspirin"}


def test_contraindications_empty_medications() -> None:
    """Empty current_medications is always safe.

    Verifies: REQ-022
    """
    drug = _make_drug(contraindicated_with={"warfarin", "aspirin"})
    result = check_contraindications(drug, [])
    assert result.is_safe is True
    assert result.conflicts == []


def test_contraindications_empty_contraindicated_with() -> None:
    """No contraindications defined means always safe.

    Verifies: REQ-020, REQ-021
    """
    drug = _make_drug(contraindicated_with=set())
    result = check_contraindications(drug, ["warfarin", "aspirin"])
    assert result.is_safe is True
    assert result.conflicts == []


def test_contraindications_one_directional() -> None:
    """Check is one-directional: only the prescribed drug's list is checked.

    Verifies: REQ-023
    """
    drug = _make_drug(drug_id="drug_a", contraindicated_with={"drug_b"})
    result = check_contraindications(drug, ["drug_c"])
    assert result.is_safe is True


def test_contraindications_deterministic() -> None:
    """Same inputs always produce the same output.

    Verifies: REQ-024
    """
    drug = _make_drug(contraindicated_with={"warfarin", "aspirin"})
    meds = ["warfarin", "metformin"]
    result1 = check_contraindications(drug, meds)
    result2 = check_contraindications(drug, meds)
    assert result1.is_safe == result2.is_safe
    assert result1.conflicts == result2.conflicts


@given(
    contraindicated=st.frozensets(
        st.text(min_size=1, max_size=10, alphabet="abcdefghij"), max_size=5
    ),
    current=st.lists(
        st.text(min_size=1, max_size=10, alphabet="abcdefghij"), max_size=5
    ),
)
@settings(max_examples=200, deadline=None)
def test_contraindications_conflicts_always_in_both_sets(
    contraindicated: frozenset[str], current: list[str]
) -> None:
    """Every reported conflict is in both contraindicated_with and current_medications.

    Verifies: REQ-020
    """
    drug = _make_drug(contraindicated_with=set(contraindicated))
    result = check_contraindications(drug, current)
    # Loop invariant: every conflict checked so far is in both sets
    for conflict in result.conflicts:
        assert conflict in contraindicated
        assert conflict in current


@given(
    contraindicated=st.frozensets(
        st.text(min_size=1, max_size=10, alphabet="abcdefghij"), max_size=5
    ),
    current=st.lists(
        st.text(min_size=1, max_size=10, alphabet="abcdefghij"), max_size=5
    ),
)
@settings(max_examples=200, deadline=None)
def test_contraindications_is_safe_consistent_with_conflicts(
    contraindicated: frozenset[str], current: list[str]
) -> None:
    """is_safe is True iff conflicts is empty.

    Verifies: REQ-021
    """
    drug = _make_drug(contraindicated_with=set(contraindicated))
    result = check_contraindications(drug, current)
    assert result.is_safe == (len(result.conflicts) == 0)
