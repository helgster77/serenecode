"""Tests for domain model classes."""

from decimal import Decimal

import icontract
import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.core.models import (
    ContraindicationResult,
    DosageError,
    DoseResult,
    Drug,
    Patient,
    SafetyResult,
)

from tests.conftest import drugs, patients


# ---------------------------------------------------------------------------
# DosageError
# ---------------------------------------------------------------------------


class TestDosageError:
    """Tests for the DosageError exception class."""

    def test_create_with_message(self) -> None:
        err = DosageError("something went wrong")
        assert str(err) == "something went wrong"
        assert err.args == ("something went wrong",)

    def test_reject_empty_message(self) -> None:
        with pytest.raises(icontract.ViolationError):
            DosageError("")


# ---------------------------------------------------------------------------
# DoseResult
# ---------------------------------------------------------------------------


class TestDoseResult:
    """Tests for DoseResult construction and immutability."""

    def test_valid_construction(self) -> None:
        r = DoseResult(dose_mg=10.0, volume_ml=2.0, was_capped=False)
        assert r.dose_mg == 10.0
        assert r.volume_ml == 2.0
        assert r.was_capped is False

    def test_reject_zero_dose(self) -> None:
        with pytest.raises(icontract.ViolationError):
            DoseResult(dose_mg=0.0, volume_ml=1.0, was_capped=False)

    def test_reject_negative_volume(self) -> None:
        with pytest.raises(icontract.ViolationError):
            DoseResult(dose_mg=1.0, volume_ml=-1.0, was_capped=False)

    def test_frozen_after_init(self) -> None:
        r = DoseResult(dose_mg=5.0, volume_ml=1.0, was_capped=True)
        with pytest.raises(DosageError, match="frozen"):
            r.dose_mg = 99.0

    def test_frozen_rejects_new_attribute(self) -> None:
        r = DoseResult(dose_mg=5.0, volume_ml=1.0, was_capped=True)
        with pytest.raises(DosageError, match="frozen"):
            r.new_attr = "nope"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# SafetyResult
# ---------------------------------------------------------------------------


class TestSafetyResult:
    """Tests for SafetyResult construction and invariants."""

    def test_safe_result(self) -> None:
        r = SafetyResult(
            daily_total_mg=100.0, max_daily_mg=200.0, is_safe=True, utilization_pct=50.0
        )
        assert r.is_safe is True
        assert r.utilization_pct == 50.0

    def test_unsafe_result(self) -> None:
        r = SafetyResult(
            daily_total_mg=300.0, max_daily_mg=200.0, is_safe=False, utilization_pct=150.0
        )
        assert r.is_safe is False

    def test_reject_inconsistent_is_safe(self) -> None:
        with pytest.raises(icontract.ViolationError):
            SafetyResult(
                daily_total_mg=300.0, max_daily_mg=200.0, is_safe=True, utilization_pct=150.0
            )

    def test_frozen_after_init(self) -> None:
        r = SafetyResult(
            daily_total_mg=100.0, max_daily_mg=200.0, is_safe=True, utilization_pct=50.0
        )
        with pytest.raises(DosageError, match="frozen"):
            r.is_safe = False


# ---------------------------------------------------------------------------
# ContraindicationResult
# ---------------------------------------------------------------------------


class TestContraindicationResult:
    """Tests for ContraindicationResult construction and invariants."""

    def test_safe_no_conflicts(self) -> None:
        r = ContraindicationResult(is_safe=True, conflicts=())
        assert r.is_safe is True
        assert r.conflicts == ()

    def test_unsafe_with_conflicts(self) -> None:
        r = ContraindicationResult(is_safe=False, conflicts=("aspirin",))
        assert r.is_safe is False
        assert r.conflicts == ("aspirin",)

    def test_reject_inconsistent(self) -> None:
        with pytest.raises(icontract.ViolationError):
            ContraindicationResult(is_safe=True, conflicts=("aspirin",))

    def test_frozen_after_init(self) -> None:
        r = ContraindicationResult(is_safe=True, conflicts=())
        with pytest.raises(DosageError, match="frozen"):
            r.conflicts = ("hack",)  # type: ignore[misc]

    def test_conflicts_container_is_immutable(self) -> None:
        r = ContraindicationResult(is_safe=False, conflicts=("aspirin",))
        with pytest.raises(AttributeError):
            r.conflicts.append("hack")  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Patient
# ---------------------------------------------------------------------------


class TestPatient:
    """Tests for Patient construction, bounds, and immutability."""

    def test_valid_construction(self) -> None:
        p = Patient(
            weight_kg=70.0, age_years=30.0, creatinine_clearance=90.0, current_medications=()
        )
        assert p.weight_kg == 70.0

    def test_reject_zero_weight(self) -> None:
        with pytest.raises(icontract.ViolationError):
            Patient(weight_kg=0.0, age_years=30.0, creatinine_clearance=90.0, current_medications=())

    def test_reject_weight_over_300(self) -> None:
        with pytest.raises(icontract.ViolationError):
            Patient(weight_kg=301.0, age_years=30.0, creatinine_clearance=90.0, current_medications=())

    def test_reject_negative_age(self) -> None:
        with pytest.raises(icontract.ViolationError):
            Patient(weight_kg=70.0, age_years=-1.0, creatinine_clearance=90.0, current_medications=())

    def test_reject_creatinine_over_200(self) -> None:
        with pytest.raises(icontract.ViolationError):
            Patient(weight_kg=70.0, age_years=30.0, creatinine_clearance=201.0, current_medications=())

    def test_medications_are_copied(self) -> None:
        meds = ["aspirin"]
        p = Patient(weight_kg=70.0, age_years=30.0, creatinine_clearance=90.0, current_medications=tuple(meds))
        meds.append("ibuprofen")
        assert p.current_medications == ("aspirin",)

    def test_frozen_after_init(self) -> None:
        p = Patient(weight_kg=70.0, age_years=30.0, creatinine_clearance=90.0, current_medications=())
        with pytest.raises(DosageError, match="frozen"):
            p.weight_kg = 999.0

    def test_medications_container_is_immutable(self) -> None:
        p = Patient(weight_kg=70.0, age_years=30.0, creatinine_clearance=90.0, current_medications=("aspirin",))
        with pytest.raises(AttributeError):
            p.current_medications.append("ibuprofen")  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Drug
# ---------------------------------------------------------------------------


class TestDrug:
    """Tests for Drug construction, bounds, and immutability."""

    def test_valid_construction(self) -> None:
        d = Drug(
            drug_id="amox",
            dose_per_kg=10.0,
            concentration_mg_per_ml=50.0,
            max_single_dose_mg=500.0,
            max_daily_dose_mg=1500.0,
            doses_per_day=3,
            contraindicated_with={"penicillin"},
        )
        assert d.drug_id == "amox"
        assert isinstance(d.contraindicated_with, frozenset)

    def test_reject_empty_drug_id(self) -> None:
        with pytest.raises(icontract.ViolationError):
            Drug(
                drug_id="",
                dose_per_kg=10.0,
                concentration_mg_per_ml=50.0,
                max_single_dose_mg=500.0,
                max_daily_dose_mg=1500.0,
                doses_per_day=3,
                contraindicated_with=set(),
            )

    def test_reject_max_daily_less_than_max_single(self) -> None:
        with pytest.raises(icontract.ViolationError):
            Drug(
                drug_id="bad",
                dose_per_kg=10.0,
                concentration_mg_per_ml=50.0,
                max_single_dose_mg=500.0,
                max_daily_dose_mg=400.0,
                doses_per_day=3,
                contraindicated_with=set(),
            )

    def test_reject_float_doses_per_day(self) -> None:
        with pytest.raises(icontract.ViolationError):
            Drug(
                drug_id="bad",
                dose_per_kg=10.0,
                concentration_mg_per_ml=50.0,
                max_single_dose_mg=500.0,
                max_daily_dose_mg=1500.0,
                doses_per_day=2.5,  # type: ignore[arg-type]
                contraindicated_with=set(),
            )

    def test_contraindicated_with_is_frozenset(self) -> None:
        d = Drug(
            drug_id="x",
            dose_per_kg=1.0,
            concentration_mg_per_ml=1.0,
            max_single_dose_mg=100.0,
            max_daily_dose_mg=100.0,
            doses_per_day=1,
            contraindicated_with={"a", "b"},
        )
        assert d.contraindicated_with == frozenset({"a", "b"})

    def test_frozen_after_init(self) -> None:
        d = Drug(
            drug_id="x",
            dose_per_kg=1.0,
            concentration_mg_per_ml=1.0,
            max_single_dose_mg=100.0,
            max_daily_dose_mg=100.0,
            doses_per_day=1,
            contraindicated_with=set(),
        )
        with pytest.raises(DosageError, match="frozen"):
            d.dose_per_kg = 999.0


# ---------------------------------------------------------------------------
# Hypothesis property-based tests
# ---------------------------------------------------------------------------


class TestModelsHypothesis:
    """Property-based tests for model construction."""

    @given(patient=patients())
    def test_patient_invariants_hold(self, patient: Patient) -> None:
        assert 0 < patient.weight_kg <= 300
        assert 0 <= patient.age_years <= 150
        assert 0 < patient.creatinine_clearance <= 200

    @given(drug=drugs())
    def test_drug_invariants_hold(self, drug: Drug) -> None:
        assert drug.dose_per_kg > 0
        assert drug.concentration_mg_per_ml > 0
        assert drug.max_daily_dose_mg >= drug.max_single_dose_mg
        assert isinstance(drug.doses_per_day, int) and drug.doses_per_day > 0

    @given(patient=patients())
    def test_patient_is_frozen(self, patient: Patient) -> None:
        with pytest.raises(DosageError, match="frozen"):
            patient.weight_kg = 1.0

    @given(drug=drugs())
    def test_drug_is_frozen(self, drug: Drug) -> None:
        with pytest.raises(DosageError, match="frozen"):
            drug.dose_per_kg = 1.0

    @given(
        dose_mg=st.floats(min_value=0.01, max_value=10000.0, allow_nan=False, allow_infinity=False),
        volume_ml=st.floats(min_value=0.01, max_value=10000.0, allow_nan=False, allow_infinity=False),
        was_capped=st.booleans(),
    )
    def test_dose_result_fields_match_inputs(
        self, dose_mg: float, volume_ml: float, was_capped: bool
    ) -> None:
        r = DoseResult(dose_mg=dose_mg, volume_ml=volume_ml, was_capped=was_capped)
        assert r.dose_mg == dose_mg
        assert r.volume_ml == volume_ml
        assert r.was_capped == was_capped
