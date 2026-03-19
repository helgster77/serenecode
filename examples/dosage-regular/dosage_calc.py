"""Medical Dosage Calculator module."""

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True)
class Patient:
    weight_kg: float
    age_years: float
    creatinine_clearance: float
    current_medications: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.weight_kg <= 0 or self.weight_kg > 300:
            raise ValueError(
                f"weight_kg must be > 0 and <= 300, got {self.weight_kg}"
            )
        if self.age_years < 0 or self.age_years > 150:
            raise ValueError(
                f"age_years must be >= 0 and <= 150, got {self.age_years}"
            )
        if self.creatinine_clearance <= 0 or self.creatinine_clearance > 200:
            raise ValueError(
                f"creatinine_clearance must be > 0 and <= 200, got {self.creatinine_clearance}"
            )


@dataclass(frozen=True)
class Drug:
    drug_id: str
    dose_per_kg: float
    concentration_mg_per_ml: float
    max_single_dose_mg: float
    max_daily_dose_mg: float
    doses_per_day: int
    contraindicated_with: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        if not self.drug_id:
            raise ValueError("drug_id must be non-empty")
        if self.dose_per_kg <= 0:
            raise ValueError(
                f"dose_per_kg must be > 0, got {self.dose_per_kg}"
            )
        if self.concentration_mg_per_ml <= 0:
            raise ValueError(
                f"concentration_mg_per_ml must be > 0, got {self.concentration_mg_per_ml}"
            )
        if self.max_single_dose_mg <= 0:
            raise ValueError(
                f"max_single_dose_mg must be > 0, got {self.max_single_dose_mg}"
            )
        if self.max_daily_dose_mg <= 0:
            raise ValueError(
                f"max_daily_dose_mg must be > 0, got {self.max_daily_dose_mg}"
            )
        if not isinstance(self.doses_per_day, int) or self.doses_per_day <= 0:
            raise ValueError(
                f"doses_per_day must be a positive integer, got {self.doses_per_day}"
            )
        if self.max_daily_dose_mg < self.max_single_dose_mg:
            raise ValueError(
                f"max_daily_dose_mg ({self.max_daily_dose_mg}) must be >= max_single_dose_mg ({self.max_single_dose_mg})"
            )


@dataclass(frozen=True)
class DoseResult:
    dose_mg: float
    volume_ml: float
    was_capped: bool


@dataclass(frozen=True)
class SafetyResult:
    daily_total_mg: float
    max_daily_mg: float
    is_safe: bool
    utilization_pct: float


@dataclass(frozen=True)
class ContraindicationResult:
    is_safe: bool
    conflicts: list[str]


def calculate_dose(patient: Patient, drug: Drug) -> DoseResult:
    """Compute the dose for a given patient and drug."""
    raw_dose = patient.weight_kg * drug.dose_per_kg
    was_capped = raw_dose > drug.max_single_dose_mg
    dose_mg = min(raw_dose, drug.max_single_dose_mg)
    volume_ml = dose_mg / drug.concentration_mg_per_ml
    return DoseResult(dose_mg=dose_mg, volume_ml=volume_ml, was_capped=was_capped)


def adjust_for_renal_function(dose_mg: float, creatinine_clearance: float) -> float:
    """Adjust a dose based on the patient's kidney function."""
    if dose_mg <= 0:
        raise ValueError(f"dose_mg must be > 0, got {dose_mg}")
    if creatinine_clearance <= 0:
        raise ValueError(
            f"creatinine_clearance must be > 0, got {creatinine_clearance}"
        )

    if creatinine_clearance >= 60:
        factor = Decimal("1.0")
    elif creatinine_clearance >= 30:
        factor = Decimal("0.75")
    elif creatinine_clearance >= 15:
        factor = Decimal("0.5")
    else:
        factor = Decimal("0.25")

    return float(Decimal(str(dose_mg)) * factor)


def check_daily_safety(dose_mg: float, drug: Drug) -> SafetyResult:
    """Verify that the prescribed dose does not exceed the maximum daily limit."""
    if dose_mg <= 0:
        raise ValueError(f"dose_mg must be > 0, got {dose_mg}")

    daily_total = Decimal(str(dose_mg)) * Decimal(str(drug.doses_per_day))
    max_daily = Decimal(str(drug.max_daily_dose_mg))
    is_safe = daily_total <= max_daily
    utilization_pct = float(daily_total / max_daily * Decimal("100"))

    return SafetyResult(
        daily_total_mg=float(daily_total),
        max_daily_mg=drug.max_daily_dose_mg,
        is_safe=is_safe,
        utilization_pct=utilization_pct,
    )


def check_contraindications(
    drug: Drug, current_medications: list[str]
) -> ContraindicationResult:
    """Check whether the drug is safe to prescribe alongside current medications."""
    conflicts = [med for med in current_medications if med in drug.contraindicated_with]
    return ContraindicationResult(is_safe=len(conflicts) == 0, conflicts=conflicts)
