"""Diagnostic categories from national / international guidelines (mirrors diagnostic_criteria.js).

Separate from reference_ranges.py: a reference range says Normal or Abnormal;
a guideline category says what the number means clinically.

Sources
- ADA:    American Diabetes Association, Standards of Care in Diabetes-2026, Section 2
          (Diabetes Care 2026;49 Suppl 1:S27-S49, doi 10.2337/dc26-S002). Non-pregnant adults.
- WHO:    World Health Organization, Guideline on haemoglobin cutoffs to define anaemia in
          individuals and populations (2024). Ages 15+; not adjusted for altitude or smoking.
- ACCAHA: 2026 ACC/AHA/Multisociety Guideline on the Management of Dyslipidemia.
          LDL-C goals by 10-year ASCVD risk (PREVENT). The 10-year risk is entered, not calculated here.
"""
from __future__ import annotations

import math

from reference_ranges import unit_key

DIAGNOSTIC_SOURCES = {
    "ADA": "ADA Standards of Care in Diabetes 2026",
    "WHO": "WHO 2024 haemoglobin cutoffs for anaemia",
    "ACCAHA": "ACC/AHA 2026 dyslipidemia guideline",
}

DIAGNOSIS_LABEL = {
    "BELOW_DIABETES": "Below diabetes range (No sign)",
    "PREDIABETES": "Prediabetes (Higher risk)",
    "DIABETES_RANGE": "Diabetes range (Repeat to confirm)",
    "RANDOM_HIGH": "Random glucose 200 or more (Check symptoms)",
    "RANDOM_NOT_DX": "Random glucose alone (Not diagnostic)",
    "ANEMIA_NONE": "No anemia (Normal blood level)",
    "ANEMIA_MILD": "Mild anemia (Slightly low blood)",
    "ANEMIA_MODERATE": "Moderate anemia (Low blood)",
    "ANEMIA_SEVERE": "Severe anemia (Very low blood)",
    "AGE_NEEDED": "Age needed (Missing age)",
    "AGE_NOT_COVERED": "Not covered (Adults 15 and older only)",
    "LDL_AT_GOAL": "At LDL goal (Target met)",
    "LDL_ABOVE_GOAL": "Above LDL goal (Too high)",
    "LDL_NO_GOAL": "No LDL number goal (Low risk: lifestyle)",
    "RISK_NEEDED": "Risk needed (Missing 10-year risk)",
}

ASCVD_RISK_LABEL = {
    "LOW": "Low, under 3% (Low)",
    "BORDERLINE": "Borderline, 3% to under 5% (Borderline)",
    "INTERMEDIATE": "Intermediate, 5% to under 10% (Medium)",
    "HIGH": "High, 10% or more (High)",
}

DX_CONFIRM_NOTE = "ADA: confirm with a repeat test unless symptoms are clear or two tests agree."


def _out(code: str, source: str, note: str = "") -> dict:
    return {"code": code, "label": DIAGNOSIS_LABEL[code], "source": DIAGNOSTIC_SOURCES[source], "note": note}


def _fixed0(x: float) -> str:
    """JavaScript toFixed(0): halves round up."""
    return str(math.floor(x + 0.5))


def diagnose_record(r: dict, canon: str) -> dict | None:
    """canon = canonical test name from the reference table. None when no guideline applies."""
    val = r["value"]
    k = unit_key(r.get("unit"))

    if canon == "Glucose":
        mgdl = val if k == "MG/DL" else (val * 18.016 if k == "MMOL/L" else None)
        if mgdl is None:
            return None
        if r.get("context") == "FASTING":
            if mgdl >= 126:
                return _out("DIABETES_RANGE", "ADA", "Fasting glucose 126 mg/dL (7.0 mmol/L) or more. " + DX_CONFIRM_NOTE)
            if mgdl >= 100:
                return _out("PREDIABETES", "ADA", "Fasting glucose 100-125 mg/dL (5.6-6.9 mmol/L).")
            return _out("BELOW_DIABETES", "ADA", "Fasting glucose under 100 mg/dL (5.6 mmol/L).")
        if r.get("context") == "RANDOM":
            if mgdl >= 200:
                return _out("RANDOM_HIGH", "ADA", "Diabetes if classic symptoms are present (200 mg/dL or more). " + DX_CONFIRM_NOTE)
            return _out("RANDOM_NOT_DX", "ADA", "A random glucose under 200 mg/dL does not diagnose or rule out diabetes.")
        return None

    if canon == "Hemoglobin A1c":
        pct = val if k == "%" else (0.09148 * val + 2.152 if k == "MMOL/MOL" else None)
        if pct is None:
            return None
        if pct >= 6.5:
            return _out("DIABETES_RANGE", "ADA", "A1c 6.5% (48 mmol/mol) or more. " + DX_CONFIRM_NOTE)
        if pct >= 5.7:
            return _out("PREDIABETES", "ADA", "A1c 5.7-6.4% (39-47 mmol/mol).")
        return _out("BELOW_DIABETES", "ADA", "A1c under 5.7% (39 mmol/mol).")

    if canon == "Hemoglobin":
        g_l = val * 10 if k == "G/DL" else (val if k == "G/L" else None)
        if g_l is None or not r.get("sex"):
            return None
        age = r.get("age", "")
        if age == "" or age is None:
            return _out("AGE_NEEDED", "WHO", "Anemia cutoffs depend on age.")
        if age < 15:
            return _out("AGE_NOT_COVERED", "WHO", "Children have different cutoffs; not included.")
        # [no anemia at or above, mild at or above, moderate at or above]; below the last = severe
        t = [130, 110, 80] if r["sex"] == "M" else [120, 110, 80]
        if r["sex"] == "F" and r.get("context") in ("PREGNANT_T1", "PREGNANT_T3"):
            t = [110, 100, 70]
        if r["sex"] == "F" and r.get("context") == "PREGNANT_T2":
            t = [105, 95, 70]
        note = "Hemoglobin " + _fixed0(g_l) + " g/L. Not adjusted for altitude or smoking."
        if g_l >= t[0]:
            return _out("ANEMIA_NONE", "WHO", note)
        if g_l >= t[1]:
            return _out("ANEMIA_MILD", "WHO", note)
        if g_l >= t[2]:
            return _out("ANEMIA_MODERATE", "WHO", note)
        return _out("ANEMIA_SEVERE", "WHO", note)

    if canon == "LDL cholesterol":
        mgdl = val if k == "MG/DL" else (val * 38.67 if k == "MMOL/L" else None)
        if mgdl is None:
            return None
        risk = r.get("ascvdRisk", "")
        if not risk:
            return _out("RISK_NEEDED", "ACCAHA", "LDL goal depends on 10-year heart risk (PREVENT calculator).")
        if risk == "LOW":
            return _out("LDL_NO_GOAL", "ACCAHA", "Low risk: lifestyle optimization, no numeric goal.")
        goal = 70 if risk == "HIGH" else 100
        note = f"Goal: LDL under {goal} mg/dL for {ASCVD_RISK_LABEL.get(risk, risk)} risk."
        return _out("LDL_AT_GOAL" if mgdl < goal else "LDL_ABOVE_GOAL", "ACCAHA", note)

    return None
