# Medical Dosage Calculator — Specification

**Purpose:** A small Python module that calculates safe drug dosages for patients.

---

## Domain Model

### REQ-001: Patient model with validated fields

A `Patient` has the following fields, all enforced by class invariants:

- `weight_kg`: body weight in kilograms. Must be > 0. Realistic human range is 0.5 kg (neonates) to 300 kg.
- `age_years`: age in years. Must be >= 0, max 150.
- `creatinine_clearance`: CrCl in mL/min, a measure of kidney function. Must be > 0, max 200.
- `current_medications`: a list of drug identifier strings the patient is currently taking.

### REQ-002: Drug model with validated fields

A `Drug` has the following fields, all enforced by class invariants:

- `drug_id`: a unique string identifier. Must be non-empty.
- `dose_per_kg`: standard dose in mg per kg of body weight. Must be > 0.
- `concentration_mg_per_ml`: concentration of the liquid form in mg/mL. Must be > 0.
- `max_single_dose_mg`: absolute maximum for a single dose in mg. Must be > 0.
- `max_daily_dose_mg`: absolute maximum for total daily intake in mg. Must be > 0 and >= `max_single_dose_mg`.
- `doses_per_day`: number of doses per day. Must be a positive integer.
- `contraindicated_with`: a set of `drug_id` strings that this drug must not be combined with.

### REQ-003: DoseResult data type

A `DoseResult` contains:

- `dose_mg`: float, the capped dose in milligrams.
- `volume_ml`: float, the volume to administer in milliliters.
- `was_capped`: bool, whether the dose was capped at the maximum.

### REQ-004: SafetyResult data type

A `SafetyResult` contains:

- `daily_total_mg`: float, the computed daily total dose.
- `max_daily_mg`: float, the drug's maximum daily dose.
- `is_safe`: bool, whether the daily total is within limits.
- `utilization_pct`: float, percentage of the max daily dose being used.

### REQ-005: ContraindicationResult data type

A `ContraindicationResult` contains:

- `is_safe`: bool, True if no conflicts found.
- `conflicts`: list of drug_id strings that conflict with the prescribed drug.

---

## Dose Calculation

### REQ-006: Raw dose computation

`calculate_dose(patient, drug)` computes the raw dose as `patient.weight_kg * drug.dose_per_kg`.

### REQ-007: Dose capping at maximum single dose

The computed dose is capped at `drug.max_single_dose_mg`. The result must never exceed this value.

### REQ-008: Volume computation

The volume to administer is computed as `capped_dose / drug.concentration_mg_per_ml`. The result `volume_ml` must be > 0.

### REQ-009: Dose result is always positive and bounded

`dose_mg` must be > 0 and `dose_mg <= drug.max_single_dose_mg` for all valid inputs.

### REQ-010: Capping flag accuracy

- If `weight_kg * dose_per_kg <= max_single_dose_mg`, then `was_capped` is `False` and `dose_mg == weight_kg * dose_per_kg`.
- If `weight_kg * dose_per_kg > max_single_dose_mg`, then `was_capped` is `True` and `dose_mg == max_single_dose_mg`.

---

## Renal Adjustment

### REQ-011: Normal kidney function preserves dose

`adjust_for_renal_function(dose_mg, creatinine_clearance)` returns 100% of `dose_mg` when `creatinine_clearance >= 60`.

### REQ-012: Moderate renal impairment reduces dose to 75%

Returns 75% of `dose_mg` when `creatinine_clearance >= 30` and `creatinine_clearance < 60`.

### REQ-013: Severe renal impairment reduces dose to 50%

Returns 50% of `dose_mg` when `creatinine_clearance >= 15` and `creatinine_clearance < 30`.

### REQ-014: Critical renal impairment reduces dose to 25%

Returns 25% of `dose_mg` when `creatinine_clearance < 15`.

### REQ-015: Renal adjustment tier boundaries

The tier boundaries are strict: CrCl of exactly 60.0 is "Normal," CrCl of exactly 30.0 is "Moderate," CrCl of exactly 15.0 is "Severe."

### REQ-016: Adjusted dose is always positive and never exceeds input

Given `dose_mg > 0` and `creatinine_clearance > 0`, the result is always > 0 and always <= `dose_mg`.

---

## Daily Safety Check

### REQ-017: Daily total computation is exact

`check_daily_safety(dose_mg, drug)` computes `daily_total_mg` as `dose_mg * drug.doses_per_day`. This must be exact with no floating-point drift.

### REQ-018: Safety determination

`is_safe` is `True` if and only if `daily_total_mg <= max_daily_mg`.

### REQ-019: Utilization percentage computation

`utilization_pct` equals `daily_total_mg / max_daily_mg * 100`. It must be >= 0. If `is_safe` is `True`, then `utilization_pct <= 100.0`.

---

## Contraindication Check

### REQ-020: Conflict detection

`check_contraindications(drug, current_medications)` checks each medication in `current_medications` against `drug.contraindicated_with` and collects all conflicts. Every item in `conflicts` must be present in both `drug.contraindicated_with` and `current_medications`.

### REQ-021: Contraindication safety determination

`is_safe` is `True` if and only if `conflicts` is empty. `is_safe` is `False` if and only if `conflicts` is non-empty.

### REQ-022: Empty medications are always safe

If `current_medications` is empty, `is_safe` is always `True`.

### REQ-023: One-directional contraindication check

The function checks one direction only: whether the prescribed drug lists the current medications as contraindicated. The caller is responsible for checking both directions if needed.

### REQ-024: Contraindication check is deterministic

The same inputs always produce the same output.

---

## Input Validation

### REQ-025: All functions reject invalid inputs

All functions must reject invalid inputs:

- Negative or zero values for weight, age (zero age is valid), creatinine clearance, dose amounts, and concentrations.
- Empty `drug_id`.
- `doses_per_day` must be a positive integer.
- `max_daily_dose_mg` must be >= `max_single_dose_mg`.

How invalid inputs are rejected (exceptions, return codes, etc.) is left to the implementation, but rejection must occur before any computation.
