/* Reference ranges used to compute Normal / Abnormal automatically.
   Source: NBME "Laboratory Values" reference sheet (conventional units + SI units).
   Each variant: [sex, context, unit, low, high, siUnit, siLow, siHigh, flags]
     sex      '' = any, 'M', 'F'
     context  '' = none, else FASTING, RANDOM, PREMENOPAUSE, FOLLICULAR, MIDCYCLE, POSTMENOPAUSE,
              PROVOCATIVE, MORNING_0800, AFTERNOON_1600
     null low/high = no bound on that side
     flags    lx/hx = low/high bound exclusive (strict < or >); bl/siBl = borderline band [lo, hi]
*/
const REFERENCE_SOURCE = 'NBME Laboratory Values reference sheet';

const REFERENCE_CONTEXTS = Object.freeze({
  FASTING: 'Fasting (No food first)',
  RANDOM: 'Random, non-fasting (Any time)',
  PREMENOPAUSE: 'Premenopause (Before menopause)',
  FOLLICULAR: 'Follicular phase (Early cycle)',
  MIDCYCLE: 'Midcycle (Mid-cycle)',
  POSTMENOPAUSE: 'Postmenopause (After menopause)',
  PROVOCATIVE: 'Provocative stimuli (Stimulation test)',
  MORNING_0800: '0800 h (Morning)',
  AFTERNOON_1600: '1600 h (Afternoon)',
  PREGNANT_T1: 'Pregnancy, 1st trimester (First 3 months)',
  PREGNANT_T2: 'Pregnancy, 2nd trimester (Months 4 to 6)',
  PREGNANT_T3: 'Pregnancy, 3rd trimester (Last 3 months)',
});

// [name, specimen, aliases[], variants[]]
const REFERENCE_TESTS = [
  /* ---------- SERUM: electrolytes ---------- */
  ['Sodium', 'SERUM', ['Na', 'Na+'], [['', '', 'mEq/L', 136, 146, 'mmol/L', 136, 146]]],
  ['Potassium', 'SERUM', ['K', 'K+'], [['', '', 'mEq/L', 3.5, 5.0, 'mmol/L', 3.5, 5.0]]],
  ['Chloride', 'SERUM', ['Cl', 'Cl-'], [['', '', 'mEq/L', 95, 105, 'mmol/L', 95, 105]]],
  ['Bicarbonate', 'SERUM', ['HCO3', 'HCO3-'], [['', '', 'mEq/L', 22, 28, 'mmol/L', 22, 28]]],
  ['Urea nitrogen', 'SERUM', ['BUN'], [['', '', 'mg/dL', 7, 18, 'mmol/L', 2.5, 6.4]]],
  ['Creatinine', 'SERUM', [], [['', '', 'mg/dL', 0.6, 1.2, 'µmol/L', 53, 106]]],
  ['Glucose', 'SERUM', [], [
    ['', 'FASTING', 'mg/dL', 70, 100, 'mmol/L', 3.8, 5.6],
    ['', 'RANDOM', 'mg/dL', null, 140, 'mmol/L', null, 7.77, { hx: true }],
  ]],
  ['Calcium', 'SERUM', [], [['', '', 'mg/dL', 8.4, 10.2, 'mmol/L', 2.1, 2.6]]],
  ['Magnesium', 'SERUM', ['Mg', 'Mg2+'], [['', '', 'mg/dL', 1.5, 2.0, 'mmol/L', 0.75, 1.0]]],
  ['Phosphorus', 'SERUM', ['Phosphorus (inorganic)'], [['', '', 'mg/dL', 3.0, 4.5, 'mmol/L', 1.0, 1.5]]],

  /* ---------- SERUM: hepatic ---------- */
  ['Alanine aminotransferase', 'SERUM', ['ALT'], [['', '', 'U/L', 10, 40, 'U/L', 10, 40]]],
  ['Aspartate aminotransferase', 'SERUM', ['AST'], [['', '', 'U/L', 12, 38, 'U/L', 12, 38]]],
  ['Alkaline phosphatase', 'SERUM', ['ALP'], [['', '', 'U/L', 25, 100, 'U/L', 25, 100]]],
  ['Bilirubin, total', 'SERUM', ['Total bilirubin'], [['', '', 'mg/dL', 0.1, 1.0, 'µmol/L', 2, 17]]],
  ['Bilirubin, direct', 'SERUM', ['Direct bilirubin'], [['', '', 'mg/dL', 0.0, 0.3, 'µmol/L', 0, 5]]],
  ['Proteins, total', 'SERUM', ['Total protein'], [['', '', 'g/dL', 6.0, 7.8, 'g/L', 60, 78]]],
  ['Albumin', 'SERUM', [], [['', '', 'g/dL', 3.5, 5.5, 'g/L', 35, 55]]],
  ['Globulin', 'SERUM', [], [['', '', 'g/dL', 2.3, 3.5, 'g/L', 23, 35]]],

  /* ---------- SERUM: other ---------- */
  ['Amylase', 'SERUM', [], [['', '', 'U/L', 25, 125, 'U/L', 25, 125]]],
  ['Lipase', 'SERUM', [], [['', '', 'U/L', 13, 60, 'U/L', 13, 60]]],
  ['Creatinine clearance', 'SERUM', [], [
    ['M', '', 'mL/min', 97, 137, 'mL/min', 97, 137],
    ['F', '', 'mL/min', 88, 128, 'mL/min', 88, 128],
  ]],
  ['Creatine kinase', 'SERUM', ['CK'], [
    ['M', '', 'U/L', 25, 90, 'U/L', 25, 90],
    ['F', '', 'U/L', 10, 70, 'U/L', 10, 70],
  ]],
  ['Lactate dehydrogenase', 'SERUM', ['LDH'], [['', '', 'U/L', 45, 200, 'U/L', 45, 200]]],
  ['Osmolality', 'SERUM', ['Serum osmolality'], [['', '', 'mOsmol/kg', 275, 295, 'mOsmol/kg', 275, 295]]],
  ['Troponin I', 'SERUM', [], [['', '', 'ng/mL', null, 0.04, 'µg/L', null, 0.04]]],
  ['Uric acid', 'SERUM', [], [['', '', 'mg/dL', 3.0, 8.2, 'mmol/L', 0.18, 0.48]]],

  /* ---------- Lipids ---------- */
  ['Cholesterol, total', 'SERUM', ['Total cholesterol', 'Cholesterol'], [
    ['', '', 'mg/dL', null, 200, 'mmol/L', null, 5.2, { hx: true, bl: [200, 240], siBl: [5.2, 6.2] }],
  ]],
  ['HDL cholesterol', 'SERUM', ['HDL'], [['', '', 'mg/dL', 40, 60, 'mmol/L', 1.0, 1.6]]],
  ['LDL cholesterol', 'SERUM', ['LDL'], [['', '', 'mg/dL', null, 160, 'mmol/L', null, 4.2, { hx: true }]]],
  ['Triglycerides', 'SERUM', [], [
    ['', '', 'mg/dL', null, 150, 'mmol/L', null, 1.70, { hx: true, bl: [150, 199], siBl: [1.70, 2.25] }],
  ]],

  /* ---------- Iron studies ---------- */
  ['Ferritin', 'SERUM', [], [
    ['M', '', 'ng/mL', 20, 250, 'µg/L', 20, 250],
    ['F', '', 'ng/mL', 10, 120, 'µg/L', 10, 120],
  ]],
  ['Iron', 'SERUM', [], [
    ['M', '', 'µg/dL', 65, 175, 'µmol/L', 11.6, 31.3],
    ['F', '', 'µg/dL', 50, 170, 'µmol/L', 9.0, 30.4],
  ]],
  ['Total iron-binding capacity', 'SERUM', ['TIBC'], [['', '', 'µg/dL', 250, 400, 'µmol/L', 44.8, 71.6]]],
  ['Transferrin', 'SERUM', [], [['', '', 'mg/dL', 200, 360, 'g/L', 2.0, 3.6]]],

  /* ---------- Endocrine ---------- */
  ['Follicle-stimulating hormone', 'SERUM', ['FSH'], [
    ['M', '', 'mIU/mL', 4, 25, 'IU/L', 4, 25],
    ['F', 'PREMENOPAUSE', 'mIU/mL', 4, 30, 'IU/L', 4, 30],
    ['F', 'MIDCYCLE', 'mIU/mL', 10, 90, 'IU/L', 10, 90],
    ['F', 'POSTMENOPAUSE', 'mIU/mL', 40, 250, 'IU/L', 40, 250],
  ]],
  ['Luteinizing hormone', 'SERUM', ['LH'], [
    ['M', '', 'mIU/mL', 6, 23, 'IU/L', 6, 23],
    ['F', 'FOLLICULAR', 'mIU/mL', 5, 30, 'IU/L', 5, 30],
    ['F', 'MIDCYCLE', 'mIU/mL', 75, 150, 'IU/L', 75, 150],
    ['F', 'POSTMENOPAUSE', 'mIU/mL', 30, 200, 'IU/L', 30, 200],
  ]],
  ['Growth hormone', 'SERUM', ['Growth hormone - arginine stimulation', 'GH'], [
    ['', 'FASTING', 'ng/mL', null, 5, 'µg/L', null, 5, { hx: true }],
    ['', 'PROVOCATIVE', 'ng/mL', 7, null, 'µg/L', 7, null, { lx: true }],
  ]],
  ['Prolactin', 'SERUM', ['hPRL', 'PRL'], [
    ['M', '', 'ng/mL', null, 17, 'µg/L', null, 17, { hx: true }],
    ['F', '', 'ng/mL', null, 25, 'µg/L', null, 25, { hx: true }],
  ]],
  ['Cortisol', 'SERUM', [], [
    ['', 'MORNING_0800', 'µg/dL', 5, 23, 'nmol/L', 138, 635],
    ['', 'AFTERNOON_1600', 'µg/dL', 3, 15, 'nmol/L', 82, 413],
  ]],
  ['TSH', 'SERUM', ['Thyroid-stimulating hormone'], [['', '', 'µIU/mL', 0.4, 4.0, 'mIU/L', 0.4, 4.0]]],
  ['Triiodothyronine (T3)', 'SERUM', ['T3', 'T3 (RIA)'], [['', '', 'ng/dL', 100, 200, 'nmol/L', 1.5, 3.1]]],
  ['T3 resin uptake', 'SERUM', ['Triiodothyronine (T3) resin uptake'], [['', '', '%', 25, 35, 'fraction', 0.25, 0.35]]],
  ['Thyroxine (T4)', 'SERUM', ['T4'], [['', '', 'µg/dL', 5, 12, 'nmol/L', 64, 155]]],
  ['Free T4', 'SERUM', ['Free thyroxine'], [['', '', 'ng/dL', 0.9, 1.7, 'pmol/L', 12.0, 21.9]]],
  ['Thyroidal iodine (123I) uptake', 'SERUM', ['Thyroid iodine uptake'], [['', '', '%/24h', 8, 30, 'fraction/24h', 0.08, 0.30]]],
  ['Intact PTH', 'SERUM', ['PTH', 'Parathyroid hormone'], [['', '', 'pg/mL', 10, 60, 'ng/L', 10, 60]]],
  ['17-Hydroxycorticosteroids', 'URINE', ['17-OHCS'], [
    ['M', '', 'mg/24h', 3.0, 10.0, 'µmol/24h', 8.2, 27.6],
    ['F', '', 'mg/24h', 2.0, 8.0, 'µmol/24h', 5.5, 22.0],
  ]],
  ['17-Ketosteroids, total', 'URINE', ['17-KS'], [
    ['M', '', 'mg/24h', 8, 20, 'µmol/24h', 28, 70],
    ['F', '', 'mg/24h', 6, 15, 'µmol/24h', 21, 52],
  ]],

  /* ---------- Immunoglobulins ---------- */
  ['IgA', 'SERUM', [], [['', '', 'mg/dL', 76, 390, 'g/L', 0.76, 3.90]]],
  ['IgE', 'SERUM', [], [['', '', 'IU/mL', 0, 380, 'kIU/L', 0, 380]]],
  ['IgG', 'SERUM', [], [['', '', 'mg/dL', 650, 1500, 'g/L', 6.5, 15.0]]],
  ['IgM', 'SERUM', [], [['', '', 'mg/dL', 50, 300, 'g/L', 0.5, 3.0]]],

  /* ---------- Arterial blood gases (room air) ---------- */
  ['PO2', 'BLOOD', ['Po2', 'Arterial PO2'], [['', '', 'mm Hg', 75, 105, 'kPa', 10.0, 14.0]]],
  ['PCO2', 'BLOOD', ['Pco2', 'Arterial PCO2'], [['', '', 'mm Hg', 33, 45, 'kPa', 4.4, 5.9]]],
  ['pH', 'BLOOD', ['Arterial pH'], [['', '', 'pH', 7.35, 7.45, 'nmol/L', 36, 44]]],

  /* ---------- Cerebrospinal fluid ---------- */
  ['Cell count, CSF', 'CSF', ['CSF cell count'], [['', '', '/mm3', 0, 5, '10^6/L', 0, 5]]],
  ['Chloride, CSF', 'CSF', ['CSF chloride'], [['', '', 'mEq/L', 118, 132, 'mmol/L', 118, 132]]],
  ['Gamma globulin, CSF', 'CSF', ['CSF gamma globulin'], [['', '', '%', 3, 12, 'fraction', 0.03, 0.12]]],
  ['Glucose, CSF', 'CSF', ['CSF glucose'], [['', '', 'mg/dL', 40, 70, 'mmol/L', 2.2, 3.9]]],
  ['Pressure, CSF', 'CSF', ['CSF pressure'], [['', '', 'mm H2O', 70, 180, 'mm H2O', 70, 180]]],
  ['Proteins, total, CSF', 'CSF', ['CSF protein', 'CSF total protein'], [['', '', 'mg/dL', null, 40, 'g/L', null, 0.40, { hx: true }]]],

  /* ---------- Hematologic: CBC ---------- */
  ['Hematocrit', 'BLOOD', ['Hct'], [
    ['M', '', '%', 41, 53, 'fraction', 0.41, 0.53],
    ['F', '', '%', 36, 46, 'fraction', 0.36, 0.46],
  ]],
  ['Hemoglobin', 'BLOOD', ['Hb', 'Hgb'], [
    ['M', '', 'g/dL', 13.5, 17.5, 'g/L', 135, 175],
    ['F', '', 'g/dL', 12.0, 16.0, 'g/L', 120, 160],
  ]],
  ['Mean corpuscular hemoglobin', 'BLOOD', ['MCH'], [['', '', 'pg/cell', 25, 35, 'fmol/cell', 0.39, 0.54]]],
  ['Mean corpuscular hemoglobin concentration', 'BLOOD', ['MCHC'], [['', '', '%', 31, 36, 'mmol Hb/L', 4.8, 5.6]]],
  ['Mean corpuscular volume', 'BLOOD', ['MCV'], [['', '', 'µm3', 80, 100, 'fL', 80, 100]]],
  ['Plasma volume', 'BLOOD', [], [
    ['M', '', 'mL/kg', 25, 43, 'L/kg', 0.025, 0.043],
    ['F', '', 'mL/kg', 28, 45, 'L/kg', 0.028, 0.045],
  ]],
  ['Red cell volume', 'BLOOD', [], [
    ['M', '', 'mL/kg', 20, 36, 'L/kg', 0.020, 0.036],
    ['F', '', 'mL/kg', 19, 31, 'L/kg', 0.019, 0.031],
  ]],
  ['Leukocyte count', 'BLOOD', ['WBC', 'White blood cell count'], [['', '', '/mm3', 4500, 11000, '10^9/L', 4.5, 11.0]]],
  ['Neutrophils, segmented', 'BLOOD', ['Segmented neutrophils'], [['', '', '%', 54, 62, 'fraction', 0.54, 0.62]]],
  ['Neutrophils, bands', 'BLOOD', ['Band neutrophils', 'Bands'], [['', '', '%', 3, 5, 'fraction', 0.03, 0.05]]],
  ['Lymphocytes', 'BLOOD', [], [['', '', '%', 25, 33, 'fraction', 0.25, 0.33]]],
  ['Monocytes', 'BLOOD', [], [['', '', '%', 3, 7, 'fraction', 0.03, 0.07]]],
  ['Eosinophils', 'BLOOD', [], [['', '', '%', 1, 3, 'fraction', 0.01, 0.03]]],
  ['Basophils', 'BLOOD', [], [['', '', '%', 0, 0.75, 'fraction', 0, 0.0075]]],
  ['Platelet count', 'BLOOD', ['Platelets', 'PLT'], [['', '', '/mm3', 150000, 400000, '10^9/L', 150, 400]]],

  /* ---------- Hematologic: coagulation and other ---------- */
  ['Partial thromboplastin time', 'PLASMA', ['PTT', 'aPTT'], [['', '', 'seconds', 25, 40, 'seconds', 25, 40]]],
  ['Prothrombin time', 'PLASMA', ['PT'], [['', '', 'seconds', 11, 15, 'seconds', 11, 15]]],
  ['D-dimer', 'PLASMA', [], [['', '', 'ng/mL', null, 250, 'nmol/L', null, 1.4]]],
  ['Reticulocyte count', 'BLOOD', ['Reticulocytes'], [['', '', '%', 0.5, 1.5, 'fraction', 0.005, 0.015]]],
  ['Erythrocyte count', 'BLOOD', ['RBC', 'Red blood cell count'], [
    ['M', '', 'million/mm3', 4.3, 5.9, '10^12/L', 4.3, 5.9],
    ['F', '', 'million/mm3', 3.5, 5.5, '10^12/L', 3.5, 5.5],
  ]],
  ['Erythrocyte sedimentation rate', 'BLOOD', ['ESR'], [
    ['M', '', 'mm/h', 0, 15, 'mm/h', 0, 15],
    ['F', '', 'mm/h', 0, 20, 'mm/h', 0, 20],
  ]],
  ['CD4+ T-lymphocyte count', 'BLOOD', ['CD4', 'CD4 count'], [['', '', '/mm3', 500, null, '10^9/L', 0.5, null]]],
  ['Hemoglobin A1c', 'BLOOD', ['HbA1c', 'A1c'], [['', '', '%', null, 6, 'mmol/mol', null, 42]]],

  /* ---------- Urine ---------- */
  ['Calcium, urine', 'URINE', ['Urine calcium'], [['', '', 'mg/24h', 100, 300, 'mmol/24h', 2.5, 7.5]]],
  ['Osmolality, urine', 'URINE', ['Urine osmolality'], [['', '', 'mOsmol/kg', 50, 1200, 'mOsmol/kg', 50, 1200]]],
  ['Oxalate, urine', 'URINE', ['Urine oxalate'], [['', '', 'µg/mL', 8, 40, 'µmol/L', 90, 445]]],
  ['Proteins, total, urine', 'URINE', ['Urine protein', 'Urine total protein'], [['', '', 'mg/24h', null, 150, 'g/24h', null, 0.15, { hx: true }]]],

  /* ---------- Body mass index ---------- */
  ['Body mass index', 'OTHER', ['BMI'], [['', '', 'kg/m2', 19, 25, 'kg/m2', 19, 25]]],
];
