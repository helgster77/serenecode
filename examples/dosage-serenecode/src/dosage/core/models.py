"""Domain models for the medical dosage calculator.

This is a core module — no I/O operations are permitted.
Defines Patient, Drug, and result data types with enforced invariants.
"""

import icontract
from dataclasses import dataclass


@icontract.invariant(lambda self: self.weight_kg > 0, "weight must be positive")
@icontract.invariant(lambda self: self.weight_kg <= 300, "weight must be <= 300 kg")
@icontract.invariant(lambda self: self.age_years >= 0, "age must be non-negative")
@icontract.invariant(lambda self: self.age_years <= 150, "age must be <= 150 years")
@icontract.invariant(
    lambda self: self.creatinine_clearance > 0,
    "creatinine clearance must be positive",
)
@icontract.invariant(
    lambda self: self.creatinine_clearance <= 200,
    "creatinine clearance must be <= 200 mL/min",
)
@dataclass(frozen=True)
class Patient:
    """A patient record with validated clinical attributes.

    Implements: REQ-001, REQ-025
    """

    weight_kg: float
    age_years: float
    creatinine_clearance: float
    current_medications: list[str]


@icontract.invariant(lambda self: len(self.drug_id) > 0, "drug_id must be non-empty")
@icontract.invariant(lambda self: self.dose_per_kg > 0, "dose_per_kg must be positive")
@icontract.invariant(
    lambda self: self.concentration_mg_per_ml > 0,
    "concentration must be positive",
)
@icontract.invariant(
    lambda self: self.max_single_dose_mg > 0, "max single dose must be positive"
)
@icontract.invariant(
    lambda self: self.max_daily_dose_mg > 0, "max daily dose must be positive"
)
@icontract.invariant(
    lambda self: self.max_daily_dose_mg >= self.max_single_dose_mg,
    "max daily dose must be >= max single dose",
)
@icontract.invariant(
    lambda self: isinstance(self.doses_per_day, int) and self.doses_per_day > 0,
    "doses_per_day must be a positive integer",
)
@dataclass(frozen=True)
class Drug:
    """A drug record with dosing parameters and contraindication data.

    Implements: REQ-002
    """

    drug_id: str
    dose_per_kg: float
    concentration_mg_per_ml: float
    max_single_dose_mg: float
    max_daily_dose_mg: float
    doses_per_day: int
    contraindicated_with: set[str]


@icontract.invariant(lambda self: self.dose_mg > 0, "dose must be positive")
@icontract.invariant(lambda self: self.volume_ml > 0, "volume must be positive")
@dataclass(frozen=True)
class DoseResult:
    """Result of a dose calculation.

    Implements: REQ-003
    """

    dose_mg: float
    volume_ml: float
    was_capped: bool


@icontract.invariant(
    lambda self: self.daily_total_mg > 0, "daily total must be positive"
)
@icontract.invariant(
    lambda self: self.max_daily_mg > 0, "max daily must be positive"
)
@icontract.invariant(
    lambda self: self.utilization_pct >= 0, "utilization must be non-negative"
)
@dataclass(frozen=True)
class SafetyResult:
    """Result of a daily safety check.

    Implements: REQ-004
    """

    daily_total_mg: float
    max_daily_mg: float
    is_safe: bool
    utilization_pct: float


@icontract.invariant(
    lambda self: self.is_safe == (len(self.conflicts) == 0),
    "is_safe must be True iff conflicts is empty",
)
@dataclass(frozen=True)
class ContraindicationResult:
    """Result of a contraindication check.

    Implements: REQ-005
    """

    is_safe: bool
    conflicts: list[str]
