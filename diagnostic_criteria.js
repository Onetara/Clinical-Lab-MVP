/* Diagnostic categories from national / international guidelines.
   Separate from reference_ranges.js: a reference range says Normal or Abnormal;
   a guideline category says what the number means clinically.

   Sources
   - ADA:    American Diabetes Association, Standards of Care in Diabetes-2026, Section 2
             (Diabetes Care 2026;49 Suppl 1:S27-S49, doi 10.2337/dc26-S002). Non-pregnant adults.
   - WHO:    World Health Organization, Guideline on haemoglobin cutoffs to define anaemia in
             individuals and populations (2024). Ages 15+; not adjusted for altitude or smoking.
   - ACCAHA: 2026 ACC/AHA/Multisociety Guideline on the Management of Dyslipidemia.
             LDL-C goals by 10-year ASCVD risk (PREVENT). The 10-year risk is entered, not calculated here.
*/
const DIAGNOSTIC_SOURCES = Object.freeze({
  ADA: 'ADA Standards of Care in Diabetes 2026',
  WHO: 'WHO 2024 haemoglobin cutoffs for anaemia',
  ACCAHA: 'ACC/AHA 2026 dyslipidemia guideline',
});

const DIAGNOSIS_LABEL = Object.freeze({
  BELOW_DIABETES: 'Below diabetes range (No sign)',
  PREDIABETES: 'Prediabetes (Higher risk)',
  DIABETES_RANGE: 'Diabetes range (Repeat to confirm)',
  RANDOM_HIGH: 'Random glucose 200 or more (Check symptoms)',
  RANDOM_NOT_DX: 'Random glucose alone (Not diagnostic)',
  ANEMIA_NONE: 'No anemia (Normal blood level)',
  ANEMIA_MILD: 'Mild anemia (Slightly low blood)',
  ANEMIA_MODERATE: 'Moderate anemia (Low blood)',
  ANEMIA_SEVERE: 'Severe anemia (Very low blood)',
  AGE_NEEDED: 'Age needed (Missing age)',
  AGE_NOT_COVERED: 'Not covered (Adults 15 and older only)',
  LDL_AT_GOAL: 'At LDL goal (Target met)',
  LDL_ABOVE_GOAL: 'Above LDL goal (Too high)',
  LDL_NO_GOAL: 'No LDL number goal (Low risk: lifestyle)',
  RISK_NEEDED: 'Risk needed (Missing 10-year risk)',
});

const ASCVD_RISK_LABEL = Object.freeze({
  LOW: 'Low, under 3% (Low)',
  BORDERLINE: 'Borderline, 3% to under 5% (Borderline)',
  INTERMEDIATE: 'Intermediate, 5% to under 10% (Medium)',
  HIGH: 'High, 10% or more (High)',
});

const DX_CONFIRM_NOTE = 'ADA: confirm with a repeat test unless symptoms are clear or two tests agree.';

// canon = canonical test name from reference_ranges.js. Returns null when no guideline applies.
function diagnoseRecord(r, canon) {
  const val = r.value;
  const k = unitKey(r.unit);
  const out = (code, source, note) => ({ code, label: DIAGNOSIS_LABEL[code], source: DIAGNOSTIC_SOURCES[source], note: note || '' });

  if (canon === 'Glucose') {
    const mgdl = k === 'MG/DL' ? val : (k === 'MMOL/L' ? val * 18.016 : null);
    if (mgdl === null) return null;
    if (r.context === 'FASTING') {
      if (mgdl >= 126) return out('DIABETES_RANGE', 'ADA', 'Fasting glucose 126 mg/dL (7.0 mmol/L) or more. ' + DX_CONFIRM_NOTE);
      if (mgdl >= 100) return out('PREDIABETES', 'ADA', 'Fasting glucose 100-125 mg/dL (5.6-6.9 mmol/L).');
      return out('BELOW_DIABETES', 'ADA', 'Fasting glucose under 100 mg/dL (5.6 mmol/L).');
    }
    if (r.context === 'RANDOM') {
      if (mgdl >= 200) return out('RANDOM_HIGH', 'ADA', 'Diabetes if classic symptoms are present (200 mg/dL or more). ' + DX_CONFIRM_NOTE);
      return out('RANDOM_NOT_DX', 'ADA', 'A random glucose under 200 mg/dL does not diagnose or rule out diabetes.');
    }
    return null;
  }

  if (canon === 'Hemoglobin A1c') {
    const pct = k === '%' ? val : (k === 'MMOL/MOL' ? 0.09148 * val + 2.152 : null);
    if (pct === null) return null;
    if (pct >= 6.5) return out('DIABETES_RANGE', 'ADA', 'A1c 6.5% (48 mmol/mol) or more. ' + DX_CONFIRM_NOTE);
    if (pct >= 5.7) return out('PREDIABETES', 'ADA', 'A1c 5.7-6.4% (39-47 mmol/mol).');
    return out('BELOW_DIABETES', 'ADA', 'A1c under 5.7% (39 mmol/mol).');
  }

  if (canon === 'Hemoglobin') {
    const gL = k === 'G/DL' ? val * 10 : (k === 'G/L' ? val : null);
    if (gL === null || !r.sex) return null;
    if (r.age === '' || r.age === undefined) return out('AGE_NEEDED', 'WHO', 'Anemia cutoffs depend on age.');
    if (r.age < 15) return out('AGE_NOT_COVERED', 'WHO', 'Children have different cutoffs; not included.');
    // [no anemia at or above, mild at or above, moderate at or above]; below the last = severe
    let t = (r.sex === 'M') ? [130, 110, 80] : [120, 110, 80];
    if (r.sex === 'F' && (r.context === 'PREGNANT_T1' || r.context === 'PREGNANT_T3')) t = [110, 100, 70];
    if (r.sex === 'F' && r.context === 'PREGNANT_T2') t = [105, 95, 70];
    const note = 'Hemoglobin ' + gL.toFixed(0) + ' g/L. Not adjusted for altitude or smoking.';
    if (gL >= t[0]) return out('ANEMIA_NONE', 'WHO', note);
    if (gL >= t[1]) return out('ANEMIA_MILD', 'WHO', note);
    if (gL >= t[2]) return out('ANEMIA_MODERATE', 'WHO', note);
    return out('ANEMIA_SEVERE', 'WHO', note);
  }

  if (canon === 'LDL cholesterol') {
    const mgdl = k === 'MG/DL' ? val : (k === 'MMOL/L' ? val * 38.67 : null);
    if (mgdl === null) return null;
    if (!r.ascvdRisk) return out('RISK_NEEDED', 'ACCAHA', 'LDL goal depends on 10-year heart risk (PREVENT calculator).');
    if (r.ascvdRisk === 'LOW') return out('LDL_NO_GOAL', 'ACCAHA', 'Low risk: lifestyle optimization, no numeric goal.');
    const goal = (r.ascvdRisk === 'HIGH') ? 70 : 100;
    return mgdl < goal
      ? out('LDL_AT_GOAL', 'ACCAHA', `Goal: LDL under ${goal} mg/dL for ${ASCVD_RISK_LABEL[r.ascvdRisk]} risk.`)
      : out('LDL_ABOVE_GOAL', 'ACCAHA', `Goal: LDL under ${goal} mg/dL for ${ASCVD_RISK_LABEL[r.ascvdRisk]} risk.`);
  }
  return null;
}
