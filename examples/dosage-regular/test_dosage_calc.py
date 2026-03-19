"""Tests for the Medical Dosage Calculator module."""

import pytest
from dosage_calc import (
    Patient,
    Drug,
    DoseResult,
    SafetyResult,
    ContraindicationResult,
    calculate_dose,
    adjust_for_renal_function,
    check_daily_safety,
    check_contraindications,
)


# --- Fixtures ---


@pytest.fixture
def standard_patient():
    return Patient(
        weight_kg=70,
        age_years=40,
        creatinine_clearance=90,
        current_medications=["drug_a", "drug_b"],
    )


@pytest.fixture
def standard_drug():
    return Drug(
        drug_id="ibuprofen",
        dose_per_kg=10.0,
        concentration_mg_per_ml=20.0,
        max_single_dose_mg=800.0,
        max_daily_dose_mg=3200.0,
        doses_per_day=4,
        contraindicated_with={"warfarin", "aspirin"},
    )


# --- Patient validation ---


class TestPatientValidation:
    def test_valid_patient(self, standard_patient):
        assert standard_patient.weight_kg == 70

    def test_zero_weight(self):
        with pytest.raises(ValueError):
            Patient(weight_kg=0, age_years=30, creatinine_clearance=90)

    def test_negative_weight(self):
        with pytest.raises(ValueError):
            Patient(weight_kg=-5, age_years=30, creatinine_clearance=90)

    def test_excessive_weight(self):
        with pytest.raises(ValueError):
            Patient(weight_kg=301, age_years=30, creatinine_clearance=90)

    def test_boundary_weight_neonate(self):
        p = Patient(weight_kg=0.5, age_years=0, creatinine_clearance=10)
        assert p.weight_kg == 0.5

    def test_boundary_weight_max(self):
        p = Patient(weight_kg=300, age_years=30, creatinine_clearance=90)
        assert p.weight_kg == 300

    def test_negative_age(self):
        with pytest.raises(ValueError):
            Patient(weight_kg=70, age_years=-1, creatinine_clearance=90)

    def test_excessive_age(self):
        with pytest.raises(ValueError):
            Patient(weight_kg=70, age_years=151, creatinine_clearance=90)

    def test_zero_creatinine(self):
        with pytest.raises(ValueError):
            Patient(weight_kg=70, age_years=30, creatinine_clearance=0)

    def test_excessive_creatinine(self):
        with pytest.raises(ValueError):
            Patient(weight_kg=70, age_years=30, creatinine_clearance=201)


# --- Drug validation ---


class TestDrugValidation:
    def test_valid_drug(self, standard_drug):
        assert standard_drug.drug_id == "ibuprofen"

    def test_empty_drug_id(self):
        with pytest.raises(ValueError):
            Drug(
                drug_id="",
                dose_per_kg=10,
                concentration_mg_per_ml=20,
                max_single_dose_mg=800,
                max_daily_dose_mg=3200,
                doses_per_day=4,
            )

    def test_zero_dose_per_kg(self):
        with pytest.raises(ValueError):
            Drug(
                drug_id="x",
                dose_per_kg=0,
                concentration_mg_per_ml=20,
                max_single_dose_mg=800,
                max_daily_dose_mg=3200,
                doses_per_day=4,
            )

    def test_zero_concentration(self):
        with pytest.raises(ValueError):
            Drug(
                drug_id="x",
                dose_per_kg=10,
                concentration_mg_per_ml=0,
                max_single_dose_mg=800,
                max_daily_dose_mg=3200,
                doses_per_day=4,
            )

    def test_zero_doses_per_day(self):
        with pytest.raises(ValueError):
            Drug(
                drug_id="x",
                dose_per_kg=10,
                concentration_mg_per_ml=20,
                max_single_dose_mg=800,
                max_daily_dose_mg=3200,
                doses_per_day=0,
            )

    def test_float_doses_per_day(self):
        with pytest.raises(ValueError):
            Drug(
                drug_id="x",
                dose_per_kg=10,
                concentration_mg_per_ml=20,
                max_single_dose_mg=800,
                max_daily_dose_mg=3200,
                doses_per_day=2.5,
            )

    def test_max_daily_less_than_max_single(self):
        with pytest.raises(ValueError):
            Drug(
                drug_id="x",
                dose_per_kg=10,
                concentration_mg_per_ml=20,
                max_single_dose_mg=800,
                max_daily_dose_mg=400,
                doses_per_day=4,
            )

    def test_max_daily_equals_max_single(self):
        d = Drug(
            drug_id="x",
            dose_per_kg=10,
            concentration_mg_per_ml=20,
            max_single_dose_mg=800,
            max_daily_dose_mg=800,
            doses_per_day=1,
        )
        assert d.max_daily_dose_mg == d.max_single_dose_mg


# --- calculate_dose ---


class TestCalculateDose:
    def test_uncapped_dose(self):
        patient = Patient(weight_kg=50, age_years=30, creatinine_clearance=90)
        drug = Drug(
            drug_id="x",
            dose_per_kg=10.0,
            concentration_mg_per_ml=25.0,
            max_single_dose_mg=800.0,
            max_daily_dose_mg=3200.0,
            doses_per_day=4,
        )
        result = calculate_dose(patient, drug)
        assert result.dose_mg == 500.0
        assert result.volume_ml == 20.0
        assert result.was_capped is False

    def test_capped_dose(self):
        patient = Patient(weight_kg=100, age_years=30, creatinine_clearance=90)
        drug = Drug(
            drug_id="x",
            dose_per_kg=10.0,
            concentration_mg_per_ml=25.0,
            max_single_dose_mg=800.0,
            max_daily_dose_mg=3200.0,
            doses_per_day=4,
        )
        # raw = 100 * 10 = 1000, capped to 800
        result = calculate_dose(patient, drug)
        assert result.dose_mg == 800.0
        assert result.volume_ml == 32.0
        assert result.was_capped is True

    def test_exact_cap_boundary(self):
        patient = Patient(weight_kg=80, age_years=30, creatinine_clearance=90)
        drug = Drug(
            drug_id="x",
            dose_per_kg=10.0,
            concentration_mg_per_ml=25.0,
            max_single_dose_mg=800.0,
            max_daily_dose_mg=3200.0,
            doses_per_day=4,
        )
        # raw = 80 * 10 = 800, exactly at cap
        result = calculate_dose(patient, drug)
        assert result.dose_mg == 800.0
        assert result.was_capped is False

    def test_neonate_dose(self):
        patient = Patient(weight_kg=0.5, age_years=0, creatinine_clearance=10)
        drug = Drug(
            drug_id="x",
            dose_per_kg=10.0,
            concentration_mg_per_ml=5.0,
            max_single_dose_mg=800.0,
            max_daily_dose_mg=3200.0,
            doses_per_day=4,
        )
        result = calculate_dose(patient, drug)
        assert result.dose_mg == 5.0
        assert result.volume_ml == 1.0
        assert result.was_capped is False

    def test_dose_always_positive(self, standard_drug):
        patient = Patient(weight_kg=0.5, age_years=0, creatinine_clearance=10)
        result = calculate_dose(patient, standard_drug)
        assert result.dose_mg > 0
        assert result.volume_ml > 0

    def test_dose_never_exceeds_max(self, standard_drug):
        patient = Patient(weight_kg=300, age_years=30, creatinine_clearance=90)
        result = calculate_dose(patient, standard_drug)
        assert result.dose_mg <= standard_drug.max_single_dose_mg


# --- adjust_for_renal_function ---


class TestAdjustForRenalFunction:
    def test_normal_function(self):
        assert adjust_for_renal_function(100.0, 60.0) == 100.0

    def test_normal_above_60(self):
        assert adjust_for_renal_function(100.0, 120.0) == 100.0

    def test_moderate_impairment(self):
        assert adjust_for_renal_function(100.0, 30.0) == 75.0

    def test_moderate_impairment_mid(self):
        assert adjust_for_renal_function(100.0, 45.0) == 75.0

    def test_severe_impairment(self):
        assert adjust_for_renal_function(100.0, 15.0) == 50.0

    def test_severe_impairment_mid(self):
        assert adjust_for_renal_function(100.0, 20.0) == 50.0

    def test_critical_impairment(self):
        assert adjust_for_renal_function(100.0, 14.9) == 25.0

    def test_critical_impairment_low(self):
        assert adjust_for_renal_function(100.0, 1.0) == 25.0

    def test_boundary_exactly_60(self):
        # CrCl of exactly 60 is "Normal"
        assert adjust_for_renal_function(100.0, 60.0) == 100.0

    def test_boundary_just_below_60(self):
        assert adjust_for_renal_function(100.0, 59.9) == 75.0

    def test_boundary_exactly_30(self):
        # CrCl of exactly 30 is "Moderate"
        assert adjust_for_renal_function(100.0, 30.0) == 75.0

    def test_boundary_just_below_30(self):
        assert adjust_for_renal_function(100.0, 29.9) == 50.0

    def test_boundary_exactly_15(self):
        # CrCl of exactly 15 is "Severe"
        assert adjust_for_renal_function(100.0, 15.0) == 50.0

    def test_boundary_just_below_15(self):
        assert adjust_for_renal_function(100.0, 14.9) == 25.0

    def test_never_increases_dose(self):
        for crcl in [1, 10, 14.9, 15, 29.9, 30, 59.9, 60, 100, 200]:
            result = adjust_for_renal_function(100.0, crcl)
            assert result <= 100.0

    def test_always_positive(self):
        for crcl in [0.01, 1, 15, 30, 60, 200]:
            result = adjust_for_renal_function(100.0, crcl)
            assert result > 0

    def test_invalid_zero_dose(self):
        with pytest.raises(ValueError):
            adjust_for_renal_function(0, 60)

    def test_invalid_negative_dose(self):
        with pytest.raises(ValueError):
            adjust_for_renal_function(-10, 60)

    def test_invalid_zero_crcl(self):
        with pytest.raises(ValueError):
            adjust_for_renal_function(100, 0)

    def test_invalid_negative_crcl(self):
        with pytest.raises(ValueError):
            adjust_for_renal_function(100, -5)


# --- check_daily_safety ---


class TestCheckDailySafety:
    def test_safe_dose(self, standard_drug):
        # 800 * 4 = 3200, exactly at max
        result = check_daily_safety(800.0, standard_drug)
        assert result.daily_total_mg == 3200.0
        assert result.max_daily_mg == 3200.0
        assert result.is_safe is True
        assert result.utilization_pct == 100.0

    def test_unsafe_dose(self, standard_drug):
        # 900 * 4 = 3600 > 3200
        result = check_daily_safety(900.0, standard_drug)
        assert result.daily_total_mg == 3600.0
        assert result.is_safe is False
        assert result.utilization_pct > 100.0

    def test_low_utilization(self, standard_drug):
        # 400 * 4 = 1600, 50% utilization
        result = check_daily_safety(400.0, standard_drug)
        assert result.daily_total_mg == 1600.0
        assert result.is_safe is True
        assert result.utilization_pct == 50.0

    def test_exact_arithmetic(self):
        # Verify no floating point drift: 0.1 * 3 should be exactly 0.3
        drug = Drug(
            drug_id="x",
            dose_per_kg=1,
            concentration_mg_per_ml=1,
            max_single_dose_mg=10,
            max_daily_dose_mg=10,
            doses_per_day=3,
        )
        result = check_daily_safety(0.1, drug)
        assert result.daily_total_mg == 0.3

    def test_utilization_non_negative(self, standard_drug):
        result = check_daily_safety(1.0, standard_drug)
        assert result.utilization_pct >= 0

    def test_safe_implies_utilization_le_100(self, standard_drug):
        result = check_daily_safety(500.0, standard_drug)
        assert result.is_safe is True
        assert result.utilization_pct <= 100.0

    def test_invalid_zero_dose(self, standard_drug):
        with pytest.raises(ValueError):
            check_daily_safety(0, standard_drug)

    def test_invalid_negative_dose(self, standard_drug):
        with pytest.raises(ValueError):
            check_daily_safety(-10, standard_drug)


# --- check_contraindications ---


class TestCheckContraindications:
    def test_no_conflicts(self, standard_drug):
        result = check_contraindications(standard_drug, ["drug_c", "drug_d"])
        assert result.is_safe is True
        assert result.conflicts == []

    def test_one_conflict(self, standard_drug):
        result = check_contraindications(standard_drug, ["warfarin", "drug_c"])
        assert result.is_safe is False
        assert result.conflicts == ["warfarin"]

    def test_multiple_conflicts(self, standard_drug):
        result = check_contraindications(standard_drug, ["warfarin", "aspirin"])
        assert result.is_safe is False
        assert set(result.conflicts) == {"warfarin", "aspirin"}

    def test_empty_medications(self, standard_drug):
        result = check_contraindications(standard_drug, [])
        assert result.is_safe is True
        assert result.conflicts == []

    def test_empty_contraindications(self):
        drug = Drug(
            drug_id="safe_drug",
            dose_per_kg=5,
            concentration_mg_per_ml=10,
            max_single_dose_mg=500,
            max_daily_dose_mg=2000,
            doses_per_day=4,
            contraindicated_with=set(),
        )
        result = check_contraindications(drug, ["anything", "at", "all"])
        assert result.is_safe is True
        assert result.conflicts == []

    def test_conflicts_subset_of_both(self, standard_drug):
        meds = ["warfarin", "drug_c", "aspirin"]
        result = check_contraindications(standard_drug, meds)
        for conflict in result.conflicts:
            assert conflict in standard_drug.contraindicated_with
            assert conflict in meds

    def test_deterministic(self, standard_drug):
        meds = ["warfarin", "aspirin", "drug_c"]
        r1 = check_contraindications(standard_drug, meds)
        r2 = check_contraindications(standard_drug, meds)
        assert r1 == r2
