# Medical Dosage Calculator — Problem Specification

**Purpose:** A small Python module that calculates safe drug dosages for patients.

---

## Domain Model

### Patient

A patient has:
- `weight_kg`: body weight in kilograms (must be > 0, realistic human range is 0.5 kg for neonates to 300 kg)
- `age_years`: age in years (must be >= 0, max 150)
- `creatinine_clearance`: CrCl in mL/min, a measure of kidney function (must be > 0, max 200)
- `current_medications`: a list of drug identifiers the patient is currently taking

### Drug

A drug has:
- `drug_id`: a unique string identifier (non-empty)
- `dose_per_kg`: standard dose in mg per kg of body weight (must be > 0)
- `concentration_mg_per_ml`: concentration of the liquid form in mg/mL (must be > 0)
- `max_single_dose_mg`: absolute maximum for a single dose in mg (must be > 0)
- `max_daily_dose_mg`: absolute maximum for total daily intake in mg (must be > 0, must be >= max_single_dose_mg)
- `doses_per_day`: number of doses per day (must be > 0, integer)
- `contraindicated_with`: a set of drug_id strings that this drug must not be combined with

---

## Functions to Implement

### 1. `calculate_dose(patient, drug) -> DoseResult`

Compute the dose for a given patient and drug.

**Logic:**
1. Compute raw dose: `weight_kg * dose_per_kg`
2. Cap at `max_single_dose_mg` — the result must never exceed this.
3. Compute volume to administer: `capped_dose / concentration_mg_per_ml`

**Returns** a `DoseResult` containing:
- `dose_mg`: the capped dose in milligrams
- `volume_ml`: the volume to administer in milliliters
- `was_capped`: boolean indicating whether the dose was capped at the maximum

**Properties that must hold:**
- `dose_mg > 0`
- `dose_mg <= drug.max_single_dose_mg`
- `volume_ml > 0`
- If `weight_kg * dose_per_kg <= max_single_dose_mg`, then `was_capped` is `False` and `dose_mg == weight_kg * dose_per_kg`
- If `weight_kg * dose_per_kg > max_single_dose_mg`, then `was_capped` is `True` and `dose_mg == max_single_dose_mg`

---

### 2. `adjust_for_renal_function(dose_mg, creatinine_clearance) -> float`

Adjust a dose based on the patient's kidney function. Impaired kidneys cannot clear drugs effectively, requiring dose reduction.

**Logic — tiered adjustment:**

| CrCl (mL/min) | Category | Adjustment |
|---|---|---|
| >= 60 | Normal | 100% (no change) |
| >= 30 and < 60 | Moderate impairment | 75% of dose |
| >= 15 and < 30 | Severe impairment | 50% of dose |
| < 15 | Critical impairment | 25% of dose |

**Returns** the adjusted dose in mg.

**Properties that must hold:**
- Result is always > 0 (given dose_mg > 0 and creatinine_clearance > 0)
- Result is always <= dose_mg (adjustment never increases a dose)
- Result is exactly dose_mg when creatinine_clearance >= 60
- The tier boundaries are strict: CrCl of exactly 60.0 is "Normal," CrCl of exactly 30.0 is "Moderate," CrCl of exactly 15.0 is "Severe"

---

### 3. `check_daily_safety(dose_mg, drug) -> SafetyResult`

Verify that the prescribed dose, when taken at the drug's prescribed frequency, does not exceed the maximum daily limit.

**Logic:**
1. Compute total daily dose: `dose_mg * drug.doses_per_day`
2. Compare against `drug.max_daily_dose_mg`

**Returns** a `SafetyResult` containing:
- `daily_total_mg`: the computed daily total
- `max_daily_mg`: the drug's max daily dose
- `is_safe`: boolean indicating whether daily total is within limits
- `utilization_pct`: percentage of the max daily dose being used (`daily_total_mg / max_daily_mg * 100`)

**Properties that must hold:**
- `daily_total_mg == dose_mg * drug.doses_per_day` (exact, no floating point drift)
- `is_safe` is `True` if and only if `daily_total_mg <= max_daily_mg`
- `utilization_pct >= 0`
- If `is_safe` is `True`, then `utilization_pct <= 100.0`

---

### 4. `check_contraindications(drug, current_medications) -> ContraindicationResult`

Check whether the drug is safe to prescribe alongside the patient's current medications.

**Logic:**
1. For each medication in `current_medications`, check if it appears in `drug.contraindicated_with`
2. Collect all conflicts found

**Returns** a `ContraindicationResult` containing:
- `is_safe`: boolean, `True` if no conflicts found
- `conflicts`: list of drug_id strings that conflict with the prescribed drug

**Properties that must hold:**
- `is_safe` is `True` if and only if `conflicts` is empty
- `is_safe` is `False` if and only if `conflicts` is non-empty
- Every item in `conflicts` must be present in both `drug.contraindicated_with` and `current_medications`
- If `current_medications` is empty, `is_safe` is always `True`
- The check is deterministic — same inputs always produce same output
- **Symmetry consideration:** this function checks one direction only (does the prescribed drug list the current meds as contraindicated). The caller is responsible for checking both directions if needed. This is a deliberate design choice to keep the function simple.

---

## Data Types for Return Values

### DoseResult
- `dose_mg: float`
- `volume_ml: float`
- `was_capped: bool`

### SafetyResult
- `daily_total_mg: float`
- `max_daily_mg: float`
- `is_safe: bool`
- `utilization_pct: float`

### ContraindicationResult
- `is_safe: bool`
- `conflicts: list[str]`

---

## Input Validation

All functions must reject invalid inputs. Specifically:
- Negative or zero values for weight, age, creatinine clearance, dose amounts, concentrations
- Empty drug_id
- doses_per_day must be a positive integer
- max_daily_dose_mg must be >= max_single_dose_mg

How invalid inputs are rejected (exceptions, return codes, etc.) is left to the implementation.

