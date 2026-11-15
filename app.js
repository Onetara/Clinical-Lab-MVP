/* Author: Stacy Toriola | Code Reviewer: Anthony O. */
// CLSDW – Clinical Lab & Sample Data Workflows (No external deps, deterministic simulation)

/* ===========================
   Global State (immutable-ish)
   =========================== */
const STCDP = (() => {
  const state = {
    runId: createRunId(),
    rawInput: '',
    inputFormat: 'json', // 'json' | 'xml'
    records: [],           // intake-normalized (clinical)
    validRecords: [],
    quarantined: [],       // {record, reason, code}
    errors: [],            // [{idx, id, code, message, field}]
    processed: [],         // post-transform
    acks: [],              // reconciliation results
    audit: [],             // audit entries
    resultInfo: {},        // id -> {result, flag, rangeText, typed}  (computed from reference ranges)
    metrics: {
      total: 0,
      valid: 0,
      errors: 0,
      quarantine: 0,
      acked: 0,
      slaBreaches: 0,
      errorByCode: {},     // for chart
    },
    config: {
      deadlineHours: { STAT: 48, ROUTINE: 168 }  // turnaround-time targets (STAT 48h, ROUTINE 7 days)
    },
    ui: {
      dashAnimEnabled: true
    }
  };
  return state;
})();

/* ===========================
   Utilities
   =========================== */
function createRunId() {
  const rand = Math.floor(Math.random() * 1e9).toString(36);
  return `run_${Date.now().toString(36)}_${rand}`;
}
function nowIso() { return new Date().toISOString(); }
function safeJsonStringify(obj) {
  // Stable output: object keys sorted at every level; arrays kept as-is.
  // (A key-list replacer would drop every property of the objects inside an array.)
  try {
    return JSON.stringify(obj, (k, v) =>
      (v && typeof v === 'object' && !Array.isArray(v))
        ? Object.fromEntries(Object.keys(v).sort().map(key => [key, v[key]])) : v, 2);
  } catch { return JSON.stringify(obj); }
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, ch => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[ch]));
}

/* ===========================
   Clinical Normalization
   =========================== */
function normSex(v) {
  const t = String(v ?? '').trim().toUpperCase();
  return (t === 'M' || t === 'MALE') ? 'M' : (t === 'F' || t === 'FEMALE') ? 'F' : '';
}
function normalizeRecord(r) {
  // Clinical schema: id, patientId, specimenType, status, value, unit, collectedAt, accession, source,
  // plus efficiency fields: result, priority, expectedAt, receivedAt, reportedAt
  return {
    id: String(r.id ?? '').trim(),
    patientId: String(r.patientId ?? '').trim(),
    specimenType: String(r.specimenType ?? '').trim().toUpperCase(),
    status: String(r.status ?? '').trim().toUpperCase(),
    value: (typeof r.value === 'number') ? r.value : Number(r.value),
    unit: String(r.unit ?? '').trim(),   // kept as typed; matching ignores case (see unitKey)
    collectedAt: String(r.collectedAt ?? '').trim(),
    accession: r.accession != null ? String(r.accession).trim().toUpperCase() : '',
    source: String(r.source ?? 'json').toLowerCase(), // provenance (json|xml)
    test: String(r.test ?? '').trim(),                                   // analyte name, e.g. Sodium (see reference_ranges.js)
    sex: normSex(r.sex),
    age: (r.age === '' || r.age == null) ? '' : Number(r.age),             // years (needed for anemia cutoffs)
    ascvdRisk: String(r.ascvdRisk ?? '').trim().toUpperCase(),            // LOW | BORDERLINE | INTERMEDIATE | HIGH (10-year heart risk, entered)                                                 // M | F | ''
    context: String(r.context ?? '').trim().toUpperCase().replace(/[\s-]+/g, '_'), // e.g. FASTING
    result: String(r.result ?? '').trim().toUpperCase().replace(/[\s-]+/g, '_'),  // computed automatically for REPORTED
    priority: String(r.priority ?? 'ROUTINE').trim().toUpperCase() || 'ROUTINE', // STAT | ROUTINE
    expectedAt: String(r.expectedAt ?? '').trim(),   // when the sample was expected to arrive
    receivedAt: String(r.receivedAt ?? '').trim(),   // when the sample actually arrived
    reportedAt: String(r.reportedAt ?? '').trim(),   // when the result was reported
  };
}

/* CRC32 (IEEE 802.3) */
function crc32(str) {
  let crc = 0 ^ (-1);
  for (let i = 0; i < str.length; i++) {
    const byte = str.charCodeAt(i);
    crc = (crc >>> 8) ^ CRC32_TABLE[(crc ^ byte) & 0xFF];
  }
  return (crc ^ (-1)) >>> 0;
}
const CRC32_TABLE = (() => {
  const table = new Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) {
      c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
    }
    table[n] = c >>> 0;
  }
  return table;
})();

/* Seeded PRNG (Mulberry32) for deterministic reconciliation */
function mulberry32(a) {
  return function() {
    a |= 0; a = a + 0x6D2B79F5 | 0;
    let t = Math.imul(a ^ a >>> 15, 1 | a);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
    return ((t ^ t >>> 14) >>> 0) / 4294967296;
  };
}

/* ===========================
   Parsing & Intake
   =========================== */
function parseJson(text) {
  try {
    const data = JSON.parse(text);
    if (!Array.isArray(data)) {
      return { error: 'JSON must be an array of clinical sample records.' };
    }
    const normalized = data.map(d => normalizeRecord({ ...d, source: 'json' }));
    return { records: normalized };
  } catch (e) {
    return { error: `E010 JSON parse error: ${e.message}` };
  }
}

function parseXml(text) {
  try {
    const parser = new DOMParser();
    const xml = parser.parseFromString(text, 'application/xml');
    const err = xml.querySelector('parsererror');
    if (err) return { error: 'E009 XML parse error: Malformed XML.' };

    // Expect: <samples><sample>...</sample></samples>
    const nodes = Array.from(xml.querySelectorAll('samples > sample'));
    if (!nodes.length) return { error: 'XML must contain <samples><sample>..</sample></samples>.' };

    const records = nodes.map(x => {
      const g = tag => x.querySelector(tag)?.textContent ?? '';
      return normalizeRecord({
        id: g('id'),
        patientId: g('patientId'),
        specimenType: g('specimenType'),
        status: g('status'),
        value: Number(g('value')),
        unit: g('unit'),
        collectedAt: g('collectedAt'),
        accession: g('accession'),
        test: g('test'),
        sex: g('sex'),
        age: g('age'),
        ascvdRisk: g('ascvdRisk'),
        context: g('context'),
        result: g('result'),
        priority: g('priority'),
        expectedAt: g('expectedAt'),
        receivedAt: g('receivedAt'),
        reportedAt: g('reportedAt'),
        source: 'xml'
      });
    });
    return { records };
  } catch (e) {
    return { error: `E009 XML parse error: ${e.message}` };
  }
}

function intakeAndValidate(raw, format) {
  STCDP.runId = createRunId();
  STCDP.rawInput = raw;
  STCDP.inputFormat = format;
  STCDP.records = [];
  STCDP.validRecords = [];
  STCDP.quarantined = [];
  STCDP.errors = [];
  STCDP.processed = [];
  STCDP.acks = [];
  STCDP.audit = [];
  STCDP.resultInfo = {};
  STCDP.metrics = { total: 0, valid: 0, errors: 0, quarantine: 0, acked: 0, slaBreaches: 0, errorByCode: {} };

  const parsed = (format === 'xml') ? parseXml(raw) : parseJson(raw);
  if (parsed.error) {
    logAudit('intake', '-', 'error', '-', parsed.error);
    STCDP.metrics.errors = 1;
    return { ok: false, message: parsed.error };
  }

  STCDP.records = parsed.records;
  STCDP.metrics.total = STCDP.records.length;
  logAudit('intake', '-', 'ok', '-', `Ingested ${STCDP.records.length} records via ${format}`);

  const vres = validateRecords(STCDP.records);
  STCDP.validRecords = vres.valid;
  STCDP.errors = vres.errors;
  STCDP.quarantined = vres.quarantine;
  STCDP.metrics.valid = STCDP.validRecords.length;
  STCDP.metrics.errors = STCDP.errors.length;
  STCDP.metrics.quarantine = STCDP.quarantined.length;
  classifyRecords(STCDP.validRecords);

  logAudit('validate', '-', 'ok', '-', `Valid=${STCDP.metrics.valid} Errors=${STCDP.metrics.errors} Quarantine=${STCDP.metrics.quarantine}`);

  return { ok: true, message: `Ingested ${STCDP.records.length} records. Valid: ${STCDP.validRecords.length}. Errors: ${STCDP.errors.length}. Quarantine: ${STCDP.quarantined.length}.` };
}

/* ===========================
   Clinical Data Quality Gates
   =========================== */
const ENUM = Object.freeze({
  SPECIMEN: new Set(['BLOOD', 'URINE', 'SWAB', 'SALIVA', 'PLASMA', 'SERUM', 'CSF']),
  STATUS: new Set(['RECEIVED', 'IN_PROGRESS', 'REPORTED']),
  UNIT: new Set(['MG/DL', 'MMOL/L', 'IU/L', 'CELLS/µL']),
  RESULT: new Set(['NORMAL', 'ABNORMAL', 'INCONCLUSIVE', 'BORDERLINE', 'NO_RANGE', 'UNIT_MISMATCH', 'SEX_NEEDED', 'CONTEXT_NEEDED']),
  SEX: new Set(['M', 'F']),
  PRIORITY: new Set(['STAT', 'ROUTINE']),
});

// On-screen wording: Lab/tech term (Short word)
const LABEL = Object.freeze({
  NORMAL: 'Normal (In range)',
  ABNORMAL: 'Abnormal (Out of range)',
  INCONCLUSIVE: 'Inconclusive (Unclear)',
  BORDERLINE: 'Borderline (Near limit)',
  NO_RANGE: 'No range (Not listed)',
  UNIT_MISMATCH: 'Unit mismatch (Wrong unit)',
  SEX_NEEDED: 'Sex needed (Missing sex)',
  CONTEXT_NEEDED: 'Context needed (Missing detail)',
});

/* ===========================
   Reference ranges (from reference_ranges.js) and automatic result
   =========================== */
const UNIT_ALIAS = Object.freeze({ 'CELLS/UL': '/MM3', 'CELLS/MM3': '/MM3' });
function unitKey(u) {
  const k = String(u ?? '').toUpperCase().replace(/[\u039C\u03BC\u00B5]/g, 'U').replace(/\s+/g, '')
    .replace(/\u00B3/g, '3').replace(/\u00B2/g, '2');
  return UNIT_ALIAS[k] || k;
}
const REF_INDEX = new Map();
const KNOWN_UNIT_KEYS = new Set(Object.keys(UNIT_ALIAS));
for (const [name, specimen, aliases, variants] of REFERENCE_TESTS) {
  const entry = { name, specimen, variants: variants.map(v => ({
    sex: v[0], ctx: v[1], unit: v[2], lo: v[3], hi: v[4], siUnit: v[5], siLo: v[6], siHi: v[7], flags: v[8] || {} })) };
  for (const n of [name, ...aliases]) REF_INDEX.set(n.trim().toLowerCase().replace(/\s+/g, ' '), entry);
  for (const v of entry.variants) { KNOWN_UNIT_KEYS.add(unitKey(v.unit)); KNOWN_UNIT_KEYS.add(unitKey(v.siUnit)); }
}
function isKnownUnit(u) { return KNOWN_UNIT_KEYS.has(unitKey(u)); }

function rangeText(lo, hi, unit, f) {
  if (lo !== null && hi !== null) return `${lo}\u2013${hi} ${unit}`;
  if (hi !== null) return `${f.hx ? '<' : '\u2264'}${hi} ${unit}`;
  return `${f.lx ? '>' : '\u2265'}${lo} ${unit}`;
}
// Returns { result, flag, rangeText } for one record, using the reference table.
function classifyRecord(r) {
  const entry = REF_INDEX.get(String(r.test || '').trim().toLowerCase().replace(/\s+/g, ' '));
  if (!entry) return { result: 'NO_RANGE', flag: '', rangeText: '' };

  let cands = entry.variants;
  if (cands.some(v => v.sex)) {
    if (!r.sex) return { result: 'SEX_NEEDED', flag: '', rangeText: '' };
    cands = cands.filter(v => !v.sex || v.sex === r.sex);
  }
  if (cands.length > 1) {
    cands = cands.filter(v => v.ctx === r.context);
    if (!cands.length) return { result: 'CONTEXT_NEEDED', flag: '', rangeText: '' };
  }
  const v = cands[0];
  const k = unitKey(r.unit);
  let lo, hi, unit, bl;
  if (k === unitKey(v.unit)) { lo = v.lo; hi = v.hi; unit = v.unit; bl = v.flags.bl; }
  else if (k === unitKey(v.siUnit)) { lo = v.siLo; hi = v.siHi; unit = v.siUnit; bl = v.flags.siBl; }
  else return { result: 'UNIT_MISMATCH', flag: '', rangeText: '' };

  const f = v.flags;
  const text = rangeText(lo, hi, unit, f) + (v.ctx ? ` (${REFERENCE_CONTEXTS[v.ctx] || v.ctx})` : '');
  const val = r.value;
  const low = lo !== null && (f.lx ? val <= lo : val < lo);
  const high = hi !== null && (f.hx ? val >= hi : val > hi);
  if (!low && !high) return { result: 'NORMAL', flag: '', rangeText: text };
  if (high && bl && val >= bl[0] && val <= bl[1]) return { result: 'BORDERLINE', flag: 'HIGH', rangeText: text };
  return { result: 'ABNORMAL', flag: low ? 'LOW' : 'HIGH', rangeText: text };
}
// Computed result replaces any typed result for REPORTED records.
// Exception: a typed INCONCLUSIVE is kept (the lab could not determine a result).
function classifyRecords(records) {
  STCDP.resultInfo = {};
  for (const r of records) {
    if (r.status !== 'REPORTED') continue;
    const typed = r.result;
    const info = classifyRecord(r);
    const final = (typed === 'INCONCLUSIVE') ? 'INCONCLUSIVE' : info.result;
    r.result = final;
    const entry = REF_INDEX.get(String(r.test || '').trim().toLowerCase().replace(/\s+/g, ' '));
    const dx = entry ? diagnoseRecord(r, entry.name) : null;
    STCDP.resultInfo[r.id] = { ...info, result: final, typed, dx };
    if (dx) logAudit('diagnosis', 'ok', 'ok', r.id, `${dx.code} (${dx.source})`);
    const changed = typed && typed !== final;
    logAudit('result', 'ok', 'ok', r.id,
      `computed=${info.result}${info.flag ? '/' + info.flag : ''} range=${info.rangeText || 'n/a'}${changed ? ` (typed ${typed} replaced)` : ''}`);
  }
}

const ERROR_BOOK = Object.freeze({
  E001: { title: 'Missing Required Field', short: 'Blank field',
    fix: ['Identify the missing field.',
          'Ensure source systems populate it (LIS/LIMS (Lab software)).',
          'If unavailable, quarantine or enrich upstream.',
          'Re-run intake after correction.'] },
  E002: { title: 'Invalid Enumeration', short: 'Bad choice',
    fix: ['Compare value to allowed list (specimenType/status/unit/result/priority).',
          'Correct mapping in source or extend enum after review.',
          'Re-run validation.'] },
  E003: { title: 'Out-of-Range Value', short: 'Impossible value',
    fix: ['Verify biological thresholds and units.',
          'Check unit conversions.',
          'Correct anomalous values or quarantine.'] },
  E004: { title: 'Chronology Violation', short: 'Wrong time order',
    fix: ['Check time order: collectedAt, then receivedAt, then reportedAt.',
          'Ensure timestamps are not in the future.',
          'Re-intake after ordering fixes.'] },
  E005: { title: 'Plausibility Failure', short: 'Does not make sense',
    fix: ['Check clinical rules (e.g., REPORTED must have a valid value, a test name and a reportedAt time).',
          'Review specimen/unit pairing.',
          'Fix data or quarantine.'] },
  E006: { title: 'Duplicate Detected', short: 'Repeat entry',
    fix: ['Use id+accession as a natural key.',
          'Deduplicate upstream.',
          'Keep first occurrence, quarantine duplicates.'] },
  E007: { title: 'Format Check Failed', short: 'Bad format',
    fix: ['Use ISO-8601 timestamps (collectedAt, expectedAt, receivedAt, reportedAt).',
          'Ensure id/accession match regex.',
          'Correct formatting upstream.'] },
  E009: { title: 'XML Parse Error', short: 'Unreadable XML',
    fix: ['Validate XML structure.',
          'Fix closing tags & nesting.',
          'Re-submit.'] },
  E010: { title: 'JSON Parse Error', short: 'Unreadable JSON',
    fix: ['Validate JSON syntax.',
          'Remove trailing commas.',
          'Re-submit.'] },
});

function addError(idx, id, code, message, field) {
  STCDP.errors.push({ idx, id, code, message, field });
  STCDP.metrics.errorByCode[code] = (STCDP.metrics.errorByCode[code] || 0) + 1;
}
function quarantine(record, reason, code='E005') {
  STCDP.quarantined.push({ record, reason, code });
}
function isIsoDate(s) {
  return /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(?:\.\d+)?)?Z$/.test(s);
}
function idPattern(s) { return /^[A-Za-z0-9\-_]{4,40}$/.test(s); }
function accessionPattern(s) { return s === '' || /^[A-Z0-9\-]{8,30}$/.test(s); }

function validateRecords(records) {
  const valid = [];
  const seenKey = new Set();
  const quarantineBin = [];

  const now = Date.now();
  const threeYearsAgo = now - 1000 * 60 * 60 * 24 * 365 * 3;

  let lastTs = -Infinity;

  records.forEach((r, idx) => {
    const id = r.id || `row_${idx+1}`;

    // Required fields
    const requiredFields = ['id','patientId','specimenType','status','value','unit','collectedAt'];
    for (const f of requiredFields) {
      if (r[f] === '' || r[f] === undefined || r[f] === null || (f === 'value' && Number.isNaN(r[f]))) {
        addError(idx, id, 'E001', `Required field '${f}' is missing`, f);
      }
    }

    // Enumerations
    if (r.specimenType && !ENUM.SPECIMEN.has(r.specimenType)) addError(idx, id, 'E002', `Invalid specimenType '${r.specimenType}'`, 'specimenType');
    if (r.status && !ENUM.STATUS.has(r.status)) addError(idx, id, 'E002', `Invalid status '${r.status}'`, 'status');
    if (r.unit && !isKnownUnit(r.unit)) addError(idx, id, 'E002', `Invalid unit '${r.unit}'`, 'unit');
    if (r.result && !ENUM.RESULT.has(r.result)) addError(idx, id, 'E002', `Invalid result '${r.result}'`, 'result');
    if (r.age !== '' && (typeof r.age !== 'number' || Number.isNaN(r.age))) addError(idx, id, 'E007', 'Invalid age (must be a number)', 'age');
    else if (r.age !== '' && (r.age < 0 || r.age > 120)) addError(idx, id, 'E003', `Age out of range: ${r.age}`, 'age');
    if (r.ascvdRisk && !ASCVD_RISK_LABEL[r.ascvdRisk]) addError(idx, id, 'E002', `Invalid ascvdRisk '${r.ascvdRisk}'`, 'ascvdRisk');
    if (r.sex && !ENUM.SEX.has(r.sex)) addError(idx, id, 'E002', `Invalid sex '${r.sex}'`, 'sex');
    if (r.context && !REFERENCE_CONTEXTS[r.context]) addError(idx, id, 'E002', `Invalid context '${r.context}'`, 'context');
    if (r.priority && !ENUM.PRIORITY.has(r.priority)) addError(idx, id, 'E002', `Invalid priority '${r.priority}'`, 'priority');

    // Value ranges (basic biological plausibility bounds; domain-neutral)
    if (typeof r.value === 'number' && !Number.isNaN(r.value)) {
      if (r.value < 0) addError(idx, id, 'E003', `Value cannot be negative: ${r.value}`, 'value');
      if (r.value > 1_000_000) addError(idx, id, 'E003', `Value out of range: ${r.value}`, 'value');
    }

    // Plausibility: REPORTED implies a measured value >= small epsilon
    if (r.status === 'REPORTED' && !(typeof r.value === 'number') || Number.isNaN(r.value)) {
      addError(idx, id, 'E005', 'REPORTED requires a numeric value', 'value');
    }

    // REPORTED needs a test name (to look up the range) and a reported time (needed for wait time)
    if (r.status === 'REPORTED' && (!r.test || !r.reportedAt)) {
      addError(idx, id, 'E005', 'REPORTED requires test and reportedAt', 'test/reportedAt');
    }

    // Efficiency timestamps: optional, but must be ISO and in order
    for (const f of ['expectedAt', 'receivedAt', 'reportedAt']) {
      if (r[f] && !isIsoDate(r[f])) addError(idx, id, 'E007', `Invalid ISO timestamp (${f})`, f);
    }
    {
      const tc = Date.parse(r.collectedAt), tr = Date.parse(r.receivedAt), tp = Date.parse(r.reportedAt);
      if (isIsoDate(r.receivedAt) && isIsoDate(r.collectedAt) && tr < tc) addError(idx, id, 'E004', 'receivedAt is before collectedAt', 'receivedAt');
      if (isIsoDate(r.reportedAt) && isIsoDate(r.receivedAt) && tp < tr) addError(idx, id, 'E004', 'reportedAt is before receivedAt', 'reportedAt');
      if (isIsoDate(r.reportedAt) && tp > now) addError(idx, id, 'E004', 'reportedAt cannot be in the future', 'reportedAt');
    }

    // Format checks
    if (r.id && !idPattern(r.id)) addError(idx, id, 'E007', 'Invalid id format', 'id');
    if (r.accession && !accessionPattern(r.accession)) addError(idx, id, 'E007', 'Invalid accession format', 'accession');
    if (r.collectedAt && !isIsoDate(r.collectedAt)) addError(idx, id, 'E007', 'Invalid ISO timestamp (collectedAt)', 'collectedAt');

    // Timestamp plausibility
    if (isIsoDate(r.collectedAt)) {
      const t = Date.parse(r.collectedAt);
      if (Number.isNaN(t)) addError(idx, id, 'E007', 'Unparseable collectedAt', 'collectedAt');
      else {
        if (t > now) addError(idx, id, 'E004', 'collectedAt cannot be in the future', 'collectedAt');
        if (t < threeYearsAgo) addError(idx, id, 'E003', 'collectedAt older than 3 years', 'collectedAt');
        if (t < lastTs) addError(idx, id, 'E004', 'Dataset chronology must be non-decreasing', 'collectedAt');
        lastTs = Math.max(lastTs, t);
      }
    }

    // Duplicate (id + accession)
    const dedupKey = `${r.id}::${r.accession || ''}`;
    if (seenKey.has(dedupKey)) addError(idx, id, 'E006', 'Duplicate id+accession', 'id/accession');
    else seenKey.add(dedupKey);

    // Audit: per-record checksum (pre-transform)
    const checksum = crc32(safeJsonStringify(r)).toString(16).padStart(8, '0');
    logAudit('validate:item', 'ok', 'ok', r.id, `preCRC=${checksum}`);

    // Valid vs quarantine
    const hadErrors = STCDP.errors.some(e => e.id === id && e.idx === idx);
    if (hadErrors) {
      quarantineBin.push({ record: r, reason: 'Validation failure', code: 'E005' });
    } else {
      valid.push(r);
    }
  });

  return { valid, errors: STCDP.errors, quarantine: quarantineBin };
}

/* ===========================
   Processing
   =========================== */
function processRecords() {
  const out = [];
  for (const r of STCDP.validRecords) {
    const category =
      (r.unit.includes('/µL') ? 'HEMATOLOGY' :
       r.specimenType === 'SWAB' ? 'MICROBIOLOGY' :
       'CHEMISTRY');

    const processed = {
      ...r,
      processedAt: nowIso(),
      normalizedValue: Number(r.value),
      category,
    };
    const checksum = crc32(safeJsonStringify(processed)).toString(16).padStart(8, '0');
    logAudit('process:item', 'ok', 'ok', r.id, `postCRC=${checksum}`);
    out.push(processed);
  }
  STCDP.processed = out;
  logAudit('process', '-', 'ok', '-', `Processed ${out.length} records`);
  return out.length;
}

/* ===========================
   Efficiency metrics (real timestamps)
   - Wait time (TAT) = reportedAt - receivedAt, in hours
   - Deadline (SLA): STAT 48h, ROUTINE 168h (7 days); editable on the Reconcile tab
   - Delivery delay = receivedAt - expectedAt, in hours (positive = late)
   =========================== */
const HOUR_MS = 3600 * 1000;
function hoursBetween(a, b) { return (Date.parse(b) - Date.parse(a)) / HOUR_MS; }
function hasTs(s) { return typeof s === 'string' && isIsoDate(s); }

function deadlineFor(rec) {
  const d = STCDP.config.deadlineHours;
  return rec.priority === 'STAT' ? d.STAT : d.ROUTINE;
}
function waitHours(rec) {
  return (rec.status === 'REPORTED' && hasTs(rec.receivedAt) && hasTs(rec.reportedAt))
    ? hoursBetween(rec.receivedAt, rec.reportedAt) : null;
}
function computeEfficiency(records) {
  const out = {
    resultCounts: Object.fromEntries([...ENUM.RESULT].map(k => [k, 0])),
    waits: [],                 // hours, reported samples only
    breaches: 0,
    deliveryDelays: [],        // hours, positive = late
    lateDeliveries: 0,
    delayBySpecimen: {},       // specimenType -> {sum, n}
    reportedCount: 0,
    dailyOutput: 0,
  };
  let minT = Infinity, maxT = -Infinity;
  for (const r of records) {
    if (r.status === 'REPORTED' && r.result && out.resultCounts[r.result] !== undefined) out.resultCounts[r.result]++;

    const w = waitHours(r);
    if (w !== null) {
      out.waits.push(w);
      if (w > deadlineFor(r)) out.breaches++;
    }
    if (r.status === 'REPORTED' && hasTs(r.reportedAt)) {
      out.reportedCount++;
      minT = Math.min(minT, Date.parse(hasTs(r.receivedAt) ? r.receivedAt : r.collectedAt));
      maxT = Math.max(maxT, Date.parse(r.reportedAt));
    }
    if (hasTs(r.expectedAt) && hasTs(r.receivedAt)) {
      const d = hoursBetween(r.expectedAt, r.receivedAt);
      out.deliveryDelays.push(d);
      if (d > 0) out.lateDeliveries++;
      const k = r.specimenType;
      out.delayBySpecimen[k] = out.delayBySpecimen[k] || { sum: 0, n: 0 };
      out.delayBySpecimen[k].sum += Math.max(0, d);
      out.delayBySpecimen[k].n++;
    }
  }
  if (out.reportedCount && maxT > minT) {
    const days = Math.max(1, (maxT - minT) / (24 * HOUR_MS));
    out.dailyOutput = out.reportedCount / days;
  } else if (out.reportedCount) {
    out.dailyOutput = out.reportedCount;
  }
  return out;
}

/* ===========================
   Reconciliation (Simulated ACKs (Receipts) + real deadline check)
   =========================== */
function reconcileRecords(deadlines) {
  if (deadlines) STCDP.config.deadlineHours = { ...STCDP.config.deadlineHours, ...deadlines };
  const acks = [];
  STCDP.metrics.acked = 0;
  STCDP.metrics.slaBreaches = 0;

  for (const r of STCDP.processed) {
    const rand = mulberry32(crc32(r.id) >>> 0);
    const ok = rand() > 0.12;                       // ~88% receipt success (simulated)
    const ackCode = ok ? 200 : 500;
    const wait = waitHours(r);
    const limit = deadlineFor(r);
    const sla = (wait === null) ? null : (wait <= limit);   // null = not reported yet

    if (ok) STCDP.metrics.acked++;
    if (sla === false) STCDP.metrics.slaBreaches++;

    acks.push({ id: r.id, ok, ackCode, wait, limit, priority: r.priority, sla });

    logAudit('reconcile:item', ok ? 'ok' : 'error', ok ? 'ok' : 'failed', r.id,
      `ack=${ackCode} wait=${wait === null ? 'n/a' : wait.toFixed(1) + 'h'} deadline=${limit}h sla=${sla === null ? 'n/a' : (sla ? 'met' : 'breach')}`);
  }

  STCDP.acks = acks;
  logAudit('reconcile', '-', 'ok', '-', `ACKed=${STCDP.metrics.acked} SLA breaches=${STCDP.metrics.slaBreaches}`);
  return acks.length;
}

/* ===========================
   Audit & Export
   =========================== */
function logAudit(stage, status, outcome, recordId, notes) {
  const entry = {
    time: nowIso(),
    runId: STCDP.runId,
    stage, status, recordId,
    checksum: crc32(`${STCDP.runId}|${stage}|${recordId}|${notes ?? ''}`).toString(16).padStart(8, '0'),
    notes: notes ?? ''
  };
  STCDP.audit.push(entry);
}
function auditToCsv() {
  const rows = [['time','runId','stage','status','recordId','checksum','notes']];
  for (const a of STCDP.audit) {
    rows.push([a.time, a.runId, a.stage, a.status, a.recordId, a.checksum, a.notes.replace(/\n/g, ' ')]);
  }
  return rows.map(r => r.map(cell => `"${String(cell).replace(/"/g,'""')}"`).join(',')).join('\n');
}
function download(filename, text, type='text/plain') {
  const blob = new Blob([text], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
}

/* ===========================
   Rendering
   =========================== */
function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}
function renderValidation() {
  setText('kpiTotal', STCDP.metrics.total);
  setText('kpiValid', STCDP.metrics.valid);
  setText('kpiErrors', STCDP.metrics.errors);
  setText('kpiQuarantine', STCDP.metrics.quarantine);

  // Errors table
  const tbody = document.getElementById('tblErrors');
  tbody.innerHTML = '';
  let row = 1;
  for (const e of STCDP.errors) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${row++}</td>
      <td><code class="stcdp-chip">${escapeHtml(e.id)}</code></td>
      <td><button type="button" class="stcdp-btn stcdp-btn--ghost stcdp-btn--tiny" data-playbook="${e.code}">${e.code}</button></td>
      <td>${escapeHtml(e.message)}</td>
      <td>${escapeHtml(e.field || '')}</td>
      <td><span class="stcdp-chip">${escapeHtml(ERROR_BOOK[e.code] ? `${ERROR_BOOK[e.code].title} (${ERROR_BOOK[e.code].short})` : 'See playbook')}</span></td>`;
    tbody.appendChild(tr);
  }

  // Quarantine table
  const tq = document.getElementById('tblQuarantine');
  tq.innerHTML = '';
  let qn = 1;
  for (const q of STCDP.quarantined) {
    const sample = safeJsonStringify(q.record).slice(0, 180).replace(/\n/g, ' ');
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${qn++}</td>
      <td><code class="stcdp-chip">${escapeHtml(q.record.id)}</code></td>
      <td>${escapeHtml(q.reason)} (${q.code})</td>
      <td><code>${escapeHtml(sample)}${sample.length >= 180 ? '…' : ''}</code></td>`;
    tq.appendChild(tr);
  }
}
function renderResults() {
  const tbody = document.getElementById('tblResults');
  if (!tbody) return;
  tbody.innerHTML = '';
  let i = 1;
  for (const r of STCDP.validRecords) {
    const info = STCDP.resultInfo[r.id];
    if (!info) continue;
    const cls = info.result === 'NORMAL' ? 'stcdp-ok' : (info.result === 'ABNORMAL' ? 'stcdp-bad' : 'stcdp-warn');
    const typedNote = (info.typed && info.typed !== info.result) ? `${LABEL[info.typed] || info.typed} (replaced)` : '';
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${i++}</td>
      <td><code class="stcdp-chip">${escapeHtml(r.id)}</code></td>
      <td>${escapeHtml(r.test)}${r.sex ? ' (' + r.sex + ')' : ''}</td>
      <td>${escapeHtml(String(r.value))} ${escapeHtml(r.unit)}</td>
      <td>${escapeHtml(info.rangeText || '-')}</td>
      <td><span class="${cls}">${escapeHtml(LABEL[info.result] || info.result)}</span></td>
      <td>${info.flag === 'HIGH' ? 'High' : info.flag === 'LOW' ? 'Low' : '-'}</td>
      <td>${escapeHtml(typedNote)}</td>
      <td title="${escapeHtml(info.dx ? info.dx.note : '')}">${info.dx ? escapeHtml(info.dx.label) : '-'}</td>
      <td>${info.dx ? escapeHtml(info.dx.source) : '-'}</td>`;
    tbody.appendChild(tr);
  }
}
function renderProcessSummary(count) {
  const el = document.getElementById('processSummary');
  el.textContent = `Processed ${count} records.`;
}
function renderReconcile() {
  setText('kpiAck', STCDP.metrics.acked);
  setText('kpiSla', STCDP.metrics.slaBreaches);

  const tbody = document.getElementById('tblAcks');
  tbody.innerHTML = '';
  let i = 1;
  for (const a of STCDP.acks) {
    const slaCell = a.sla === null ? '<span class="stcdp-warn">NOT REPORTED</span>'
      : (a.sla ? '<span class="stcdp-ok">MET</span>' : '<span class="stcdp-bad">BREACH</span>');
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${i++}</td>
      <td><code class="stcdp-chip">${escapeHtml(a.id)}</code></td>
      <td>${a.ok ? '<span class="stcdp-ok">OK</span>' : '<span class="stcdp-bad">FAILED</span>'}</td>
      <td>${a.ackCode}</td>
      <td>${escapeHtml(a.priority)}</td>
      <td>${a.wait === null ? '-' : a.wait.toFixed(1)}</td>
      <td>${a.limit}</td>
      <td>${slaCell}</td>`;
    tbody.appendChild(tr);
  }
}
function renderAudit() {
  const tbody = document.getElementById('tblAudit');
  tbody.innerHTML = '';
  let i = 1;
  for (const a of STCDP.audit) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${i++}</td>
      <td>${escapeHtml(a.time)}</td>
      <td><code class="stcdp-chip">${escapeHtml(a.runId)}</code></td>
      <td>${escapeHtml(a.stage)}</td>
      <td><code>${escapeHtml(a.recordId || '-')}</code></td>
      <td>${escapeHtml(a.status)}</td>
      <td><code>${escapeHtml(a.checksum)}</code></td>
      <td>${escapeHtml(a.notes)}</td>`;
    tbody.appendChild(tr);
  }
}
function renderDashboard() {
  const total = STCDP.metrics.total;
  const qsize = STCDP.metrics.quarantine;
  const eff = computeEfficiency(STCDP.validRecords);

  const errorRate = total ? Math.round((qsize / total) * 100) : 0;   // share of records that failed validation
  const slaRate = eff.waits.length ? Math.round((eff.breaches / eff.waits.length) * 100) : 0;
  const avgWait = eff.waits.length ? (eff.waits.reduce((a, b) => a + b, 0) / eff.waits.length) : 0;
  const onTime = eff.deliveryDelays.length
    ? Math.round(((eff.deliveryDelays.length - eff.lateDeliveries) / eff.deliveryDelays.length) * 100) : 0;
  const lateOnly = eff.deliveryDelays.filter(d => d > 0);
  const avgLate = lateOnly.length ? (lateOnly.reduce((a, b) => a + b, 0) / lateOnly.length) : 0;

  setText('metricErrorRate', `${errorRate}%`);
  setText('metricSlaRate', eff.waits.length ? `${slaRate}%` : 'n/a');
  setText('metricQuarantine', String(qsize));
  setText('metricThroughput', eff.reportedCount ? eff.dailyOutput.toFixed(1) : '0');
  setText('metricAvgWait', eff.waits.length ? `${avgWait.toFixed(1)} h` : 'n/a');
  setText('metricOnTime', eff.deliveryDelays.length ? `${onTime}%` : 'n/a');
  setText('metricAvgLate', eff.deliveryDelays.length ? `${avgLate.toFixed(1)} h` : 'n/a');

  const errEntries = Object.entries(STCDP.metrics.errorByCode).sort((a, b) => b[1] - a[1]);
  drawValueBars('chartErrors', errEntries.map(([c]) => c), errEntries.map(([, n]) => n), ['#4cc9f0']);
  renderErrorLegend(errEntries);

  // Results split
  const RES_COLOR = { NORMAL: '#80ffdb', ABNORMAL: '#ff6b6b', INCONCLUSIVE: '#ffd166', BORDERLINE: '#ff9f1c' };
  const resKeys = Object.keys(eff.resultCounts).filter(k => ['NORMAL', 'ABNORMAL', 'INCONCLUSIVE'].includes(k) || eff.resultCounts[k] > 0);
  drawValueBars('chartResults', resKeys.map(k => LABEL[k]), resKeys.map(k => eff.resultCounts[k]), resKeys.map(k => RES_COLOR[k] || '#8d99ae'));

  // Wait time (TAT) distribution in 24h buckets
  const BUCKET = 24;
  const maxWait = eff.waits.length ? Math.max(...eff.waits) : 0;
  const nb = Math.max(1, Math.ceil((maxWait + 0.0001) / BUCKET));
  const counts = new Array(nb).fill(0);
  eff.waits.forEach(w => { counts[Math.min(nb - 1, Math.floor(w / BUCKET))]++; });
  drawValueBars('chartWait', counts.map((_, i) => `${i * BUCKET}-${(i + 1) * BUCKET}h`), eff.waits.length ? counts : [], ['#80ffdb']);

  // Slowdown (bottleneck): average hours late, by specimen
  const spec = Object.entries(eff.delayBySpecimen).map(([k, v]) => [k, v.sum / v.n]).sort((a, b) => b[1] - a[1]);
  drawValueBars('chartDelivery', spec.map(([k]) => k), spec.map(([, v]) => Number(v.toFixed(1))), ['#ff9f1c']);
}

/* Charts */
// Defines every error code shown on the chart (code, plain meaning, count)
function renderErrorLegend(entries) {
  const host = document.getElementById('errorLegend');
  if (!host) return;
  host.innerHTML = '';
  if (!entries.length) { host.textContent = 'No errors recorded.'; return; }
  for (const [code, n] of entries) {
    const meta = ERROR_BOOK[code];
    const li = document.createElement('li');
    li.innerHTML = `<strong>${escapeHtml(code)}</strong> — ${escapeHtml(meta ? `${meta.title} (${meta.short})` : 'See playbook')}: ${n}`;
    host.appendChild(li);
  }
}
// Generic labelled bar chart with value labels on top. colors cycle per bar.
function drawValueBars(canvasId, labels, values, colors) {
  const c = document.getElementById(canvasId);
  if (!c) return;
  const ctx = c.getContext('2d');
  ctx.clearRect(0, 0, c.width, c.height);
  if (!values.length) {
    ctx.fillStyle = '#a8b0bf';
    ctx.font = '12px system-ui';
    ctx.fillText('No data yet', 20, c.height / 2);
    return;
  }
  const max = Math.max(...values, 1);
  const gap = 10;
  const barW = Math.max(18, Math.min(90, Math.floor((c.width - 40) / values.length) - gap));
  let x = 20;
  values.forEach((v, i) => {
    const h = Math.round((v / max) * (c.height - 74));
    const color = colors[i % colors.length];
    ctx.fillStyle = color;
    ctx.fillRect(x, c.height - 44 - h, barW, h);
    ctx.strokeStyle = '#314257';
    ctx.strokeRect(x, c.height - 44 - h, barW, h);

    ctx.fillStyle = '#e6edf7';
    ctx.font = 'bold 15px system-ui';
    ctx.fillText(String(v), x, c.height - 49 - h);
    ctx.fillStyle = '#a8b0bf';
    ctx.font = '13px system-ui';
    // wrap label onto up to 2 lines so full wording shows
    const words = String(labels[i]).split(' ');
    const lines = []; let cur = '';
    for (const w of words) {
      const t = cur ? cur + ' ' + w : w;
      if (cur && ctx.measureText(t).width > barW + gap - 2) { lines.push(cur); cur = w; } else cur = t;
    }
    if (cur) lines.push(cur);
    lines.slice(0, 2).forEach((ln, k) => ctx.fillText(ln, x, c.height - 34 + 14 * (k + 1)));
    x += barW + gap;
  });
}

/* ===========================
   Playbooks rendering
   =========================== */
function renderPlaybooks() {
  const host = document.getElementById('playbookList');
  host.innerHTML = '';
  Object.entries(ERROR_BOOK).forEach(([code, meta]) => {
    const det = document.createElement('details');
    det.id = `playbook-${code}`;
    const summary = document.createElement('summary');
    summary.innerHTML = `<strong>${code}</strong> — ${escapeHtml(meta.title)} (${escapeHtml(meta.short)})`;
    det.appendChild(summary);

    const ul = document.createElement('ol');
    (meta.fix || []).forEach(step => {
      const li = document.createElement('li');
      li.textContent = step;
      ul.appendChild(li);
    });
    det.appendChild(ul);
    host.appendChild(det);
  });
}

/* ===========================
   Editor Helpers (new)
   =========================== */
function recordsToJsonText() {
  return safeJsonStringify(STCDP.records);
}
function recordsToXmlText() {
  const esc = s => String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  const rows = STCDP.records.map(r => `
  <sample>
    <id>${esc(r.id)}</id>
    <patientId>${esc(r.patientId)}</patientId>
    <sex>${esc(r.sex)}</sex>
    <age>${esc(r.age)}</age>
    <ascvdRisk>${esc(r.ascvdRisk)}</ascvdRisk>
    <specimenType>${esc(r.specimenType)}</specimenType>
    <test>${esc(r.test)}</test>
    <context>${esc(r.context)}</context>
    <status>${esc(r.status)}</status>
    <value>${Number(r.value)}</value>
    <unit>${esc(r.unit)}</unit>
    <collectedAt>${esc(r.collectedAt)}</collectedAt>
    <accession>${esc(r.accession)}</accession>
    <result>${esc(r.result)}</result>
    <priority>${esc(r.priority)}</priority>
    <expectedAt>${esc(r.expectedAt)}</expectedAt>
    <receivedAt>${esc(r.receivedAt)}</receivedAt>
    <reportedAt>${esc(r.reportedAt)}</reportedAt>
  </sample>`.trim()).join('\n  ');
  return `<samples>\n  ${rows}\n</samples>\n`;
}

/* ===========================
   DOM Helpers & Events
   =========================== */
function switchTab(target) {
  document.querySelectorAll('.stcdp-tab').forEach(b => b.classList.remove('is-active'));
  document.querySelectorAll('.stcdp-panel').forEach(p => p.classList.add('stcdp-hidden'));
  document.querySelector(`.stcdp-tab[data-tab="${target}"]`)?.classList.add('is-active');
  document.getElementById(`stcdp-panel-${target}`)?.classList.remove('stcdp-hidden');
}
function onClickPlaybook(e) {
  const code = e.target?.dataset?.playbook;
  if (!code) return;
  switchTab('playbooks');
  const d = document.getElementById(`playbook-${code}`);
  if (d) { d.open = true; d.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
}

/* ===========================
   Clinical Examples (12 records each)
   =========================== */
const JSON_EXAMPLE_TEXT = `[
  {"id":"SMP-1001","patientId":"PAT-001","sex":"F","age":45,"ascvdRisk":"","specimenType":"SERUM","test":"Glucose","context":"FASTING","status":"REPORTED","value":118,"unit":"mg/dL","collectedAt":"2026-01-15T09:00:00Z","accession":"ACC-2026-0001","result":"","priority":"ROUTINE","expectedAt":"2026-01-15T11:00:00Z","receivedAt":"2026-01-15T10:50:00Z","reportedAt":"2026-01-16T11:00:00Z"},
  {"id":"SMP-1002","patientId":"PAT-002","sex":"M","age":60,"ascvdRisk":"","specimenType":"SERUM","test":"Sodium","context":"","status":"IN_PROGRESS","value":139,"unit":"mEq/L","collectedAt":"2026-01-15T09:05:00Z","accession":"ACC-2026-0002","result":"","priority":"ROUTINE","expectedAt":"2026-01-15T11:00:00Z","receivedAt":"2026-01-15T14:30:00Z","reportedAt":""},
  {"id":"SMP-1003","patientId":"PAT-003","sex":"F","age":38,"ascvdRisk":"","specimenType":"SERUM","test":"Potassium","context":"","status":"REPORTED","value":5.6,"unit":"mEq/L","collectedAt":"2026-01-15T09:10:00Z","accession":"ACC-2026-0003","result":"","priority":"STAT","expectedAt":"2026-01-15T11:00:00Z","receivedAt":"2026-01-15T16:10:00Z","reportedAt":"2026-01-17T20:00:00Z"},
  {"id":"SMP-1004","patientId":"PAT-004","sex":"F","age":29,"ascvdRisk":"","specimenType":"SALIVA","test":"Salivary cortisol","context":"","status":"REPORTED","value":0.8,"unit":"ng/mL","collectedAt":"2026-01-15T09:12:00Z","accession":"ACC-2026-0004","result":"","priority":"ROUTINE","expectedAt":"2026-01-15T11:00:00Z","receivedAt":"2026-01-15T11:30:00Z","reportedAt":"2026-01-16T14:30:00Z"},
  {"id":"SMP-1005","patientId":"PAT-005","sex":"F","age":52,"ascvdRisk":"","specimenType":"BLOOD","test":"Hemoglobin A1c","context":"","status":"REPORTED","value":6.8,"unit":"%","collectedAt":"2026-01-15T09:15:00Z","accession":"ACC-2026-0005","result":"NORMAL","priority":"ROUTINE","expectedAt":"2026-01-15T11:00:00Z","receivedAt":"2026-01-15T10:45:00Z","reportedAt":"2026-01-16T15:45:00Z"},
  {"id":"SMP-1006","patientId":"PAT-006","sex":"M","age":41,"ascvdRisk":"","specimenType":"BLOOD","test":"Hemoglobin","context":"","status":"IN_PROGRESS","value":14.8,"unit":"g/dL","collectedAt":"2026-01-15T09:20:00Z","accession":"ACC-2026-0006","result":"","priority":"ROUTINE","expectedAt":"2026-01-15T11:00:00Z","receivedAt":"2026-01-15T11:00:00Z","reportedAt":""},
  {"id":"SMP-1007","patientId":"PAT-007","sex":"M","age":33,"ascvdRisk":"","specimenType":"SERUM","test":"Ferritin","context":"","status":"RECEIVED","value":12,"unit":"ng/mL","collectedAt":"2026-01-15T09:25:00Z","accession":"ACC-2026-0007","result":"","priority":"ROUTINE","expectedAt":"2026-01-15T11:30:00Z","receivedAt":"2026-01-15T13:00:00Z","reportedAt":""},
  {"id":"SMP-1008","patientId":"PAT-008","sex":"M","age":70,"ascvdRisk":"","specimenType":"SERUM","test":"Potassium","context":"","status":"REPORTED","value":4.1,"unit":"mEq/L","collectedAt":"2026-01-15T09:30:00Z","accession":"ACC-2026-0008","result":"INCONCLUSIVE","priority":"ROUTINE","expectedAt":"2026-01-15T11:30:00Z","receivedAt":"2026-01-15T15:30:00Z","reportedAt":"2026-01-18T09:30:00Z"},
  {"id":"SMP-1009","patientId":"PAT-009","sex":"M","age":25,"ascvdRisk":"","specimenType":"BLOOD","test":"Leukocyte count","context":"","status":"REPORTED","value":4500,"unit":"CELLS/µL","collectedAt":"2026-01-15T09:35:00Z","accession":"ACC-2026-0009","result":"","priority":"STAT","expectedAt":"2026-01-15T11:30:00Z","receivedAt":"2026-01-15T17:45:00Z","reportedAt":"2026-01-16T13:45:00Z"},
  {"id":"SMP-1010","patientId":"PAT-010","sex":"F","age":47,"ascvdRisk":"","specimenType":"SERUM","test":"TSH","context":"","status":"IN_PROGRESS","value":2.1,"unit":"µIU/mL","collectedAt":"2026-01-15T09:40:00Z","accession":"ACC-2026-0010","result":"","priority":"ROUTINE","expectedAt":"2026-01-15T11:30:00Z","receivedAt":"2026-01-15T11:20:00Z","reportedAt":""},
  {"id":"bad id!","patientId":"PAT-011","sex":"F","age":30,"ascvdRisk":"","specimenType":"SERUM","test":"Sodium","context":"","status":"REPORTED","value":-3,"unit":"mEq/L","collectedAt":"2026-01-15T09:42:00Z","accession":"TOO-LONG-REF-123456-OVER","result":"","priority":"ROUTINE","expectedAt":"2026-01-15T11:30:00Z","receivedAt":"2026-01-15T12:00:00Z","reportedAt":""},
  {"id":"SMP-1002","patientId":"PAT-002","sex":"M","age":60,"ascvdRisk":"","specimenType":"SERUM","test":"Sodium","context":"","status":"RECEIVED","value":1.1,"unit":"XYZ","collectedAt":"2026-01-15T09:45:00Z","accession":"ACC-2026-0002","result":"","priority":"ROUTINE","expectedAt":"2026-01-15T11:30:00Z","receivedAt":"2026-01-15T11:40:00Z","reportedAt":""}
]`;

const XML_EXAMPLE_TEXT = `
<samples>
  <sample>
    <id>SMP-2001</id>
    <patientId>PAT-021</patientId>
    <sex>M</sex>
    <age>62</age>
    <ascvdRisk></ascvdRisk>
    <specimenType>SERUM</specimenType>
    <test>Sodium</test>
    <context></context>
    <status>RECEIVED</status>
    <value>150</value>
    <unit>mEq/L</unit>
    <collectedAt>2026-01-16T10:00:00Z</collectedAt>
    <accession>ACC-2026-0101</accession>
    <result></result>
    <priority>ROUTINE</priority>
    <expectedAt>2026-01-16T12:00:00Z</expectedAt>
    <receivedAt>2026-01-16T11:55:00Z</receivedAt>
    <reportedAt></reportedAt>
  </sample>
  <sample>
    <id>SMP-2002</id>
    <patientId>PAT-022</patientId>
    <sex>M</sex>
    <age>58</age>
    <ascvdRisk>INTERMEDIATE</ascvdRisk>
    <specimenType>SERUM</specimenType>
    <test>LDL cholesterol</test>
    <context></context>
    <status>REPORTED</status>
    <value>128</value>
    <unit>mg/dL</unit>
    <collectedAt>2026-01-16T10:05:00Z</collectedAt>
    <accession>ACC-2026-0102</accession>
    <result></result>
    <priority>ROUTINE</priority>
    <expectedAt>2026-01-16T12:00:00Z</expectedAt>
    <receivedAt>2026-01-16T15:10:00Z</receivedAt>
    <reportedAt>2026-01-17T15:10:00Z</reportedAt>
  </sample>
  <sample>
    <id>SMP-2003</id>
    <patientId>PAT-023</patientId>
    <sex>F</sex>
    <age>44</age>
    <ascvdRisk></ascvdRisk>
    <specimenType>SERUM</specimenType>
    <test>Alanine aminotransferase</test>
    <context></context>
    <status>REPORTED</status>
    <value>95</value>
    <unit>U/L</unit>
    <collectedAt>2026-01-16T10:06:00Z</collectedAt>
    <accession>ACC-2026-0103</accession>
    <result></result>
    <priority>STAT</priority>
    <expectedAt>2026-01-16T12:00:00Z</expectedAt>
    <receivedAt>2026-01-16T17:00:00Z</receivedAt>
    <reportedAt>2026-01-17T13:00:00Z</reportedAt>
  </sample>
  <sample>
    <id>SMP-2004</id>
    <patientId>PAT-024</patientId>
    <sex>M</sex>
    <age>35</age>
    <ascvdRisk></ascvdRisk>
    <specimenType>SALIVA</specimenType>
    <test>Salivary cortisol</test>
    <context></context>
    <status>RECEIVED</status>
    <value>0.7</value>
    <unit>ng/mL</unit>
    <collectedAt>2026-01-16T10:08:00Z</collectedAt>
    <accession>ACC-2026-0104</accession>
    <result></result>
    <priority>ROUTINE</priority>
    <expectedAt>2026-01-16T12:00:00Z</expectedAt>
    <receivedAt>2026-01-16T12:20:00Z</receivedAt>
    <reportedAt></reportedAt>
  </sample>
  <sample>
    <id>SMP-2005</id>
    <patientId>PAT-025</patientId>
    <sex>M</sex>
    <age>66</age>
    <ascvdRisk></ascvdRisk>
    <specimenType>SERUM</specimenType>
    <test>Troponin I</test>
    <context></context>
    <status>REPORTED</status>
    <value>0.02</value>
    <unit>ng/mL</unit>
    <collectedAt>2026-01-16T10:10:00Z</collectedAt>
    <accession>ACC-2026-0105</accession>
    <result></result>
    <priority>ROUTINE</priority>
    <expectedAt>2026-01-16T12:00:00Z</expectedAt>
    <receivedAt>2026-01-16T11:50:00Z</receivedAt>
    <reportedAt>2026-01-17T16:50:00Z</reportedAt>
  </sample>
  <sample>
    <id>SMP-2006</id>
    <patientId>PAT-026</patientId>
    <sex>F</sex>
    <age>34</age>
    <ascvdRisk></ascvdRisk>
    <specimenType>BLOOD</specimenType>
    <test>Hemoglobin</test>
    <context></context>
    <status>REPORTED</status>
    <value>11.0</value>
    <unit>g/dL</unit>
    <collectedAt>2026-01-16T10:15:00Z</collectedAt>
    <accession>ACC-2026-0106</accession>
    <result></result>
    <priority>ROUTINE</priority>
    <expectedAt>2026-01-16T12:00:00Z</expectedAt>
    <receivedAt>2026-01-16T12:00:00Z</receivedAt>
    <reportedAt>2026-01-17T08:00:00Z</reportedAt>
  </sample>
  <sample>
    <id>SMP-2007</id>
    <patientId>PAT-027</patientId>
    <sex>F</sex>
    <age>51</age>
    <ascvdRisk></ascvdRisk>
    <specimenType>SERUM</specimenType>
    <test>Triglycerides</test>
    <context></context>
    <status>REPORTED</status>
    <value>180</value>
    <unit>mg/dL</unit>
    <collectedAt>2026-01-16T10:20:00Z</collectedAt>
    <accession>ACC-2026-0107</accession>
    <result></result>
    <priority>ROUTINE</priority>
    <expectedAt>2026-01-16T12:30:00Z</expectedAt>
    <receivedAt>2026-01-16T13:45:00Z</receivedAt>
    <reportedAt>2026-01-17T09:00:00Z</reportedAt>
  </sample>
  <sample>
    <id>SMP-2008</id>
    <patientId>PAT-028</patientId>
    <sex>F</sex>
    <age>49</age>
    <ascvdRisk></ascvdRisk>
    <specimenType>SERUM</specimenType>
    <test>Glucose</test>
    <context>RANDOM</context>
    <status>REPORTED</status>
    <value>160</value>
    <unit>mg/dL</unit>
    <collectedAt>2026-01-16T10:25:00Z</collectedAt>
    <accession>ACC-2026-0108</accession>
    <result></result>
    <priority>ROUTINE</priority>
    <expectedAt>2026-01-16T12:30:00Z</expectedAt>
    <receivedAt>2026-01-16T16:30:00Z</receivedAt>
    <reportedAt>2026-01-24T08:30:00Z</reportedAt>
  </sample>
  <sample>
    <id>SMP-2009</id>
    <patientId>PAT-029</patientId>
    <sex>M</sex>
    <age>40</age>
    <ascvdRisk></ascvdRisk>
    <specimenType>SERUM</specimenType>
    <test>Creatinine</test>
    <context></context>
    <status>REPORTED</status>
    <value>1.0</value>
    <unit>mmol/L</unit>
    <collectedAt>2026-01-16T10:30:00Z</collectedAt>
    <accession>ACC-2026-0109</accession>
    <result></result>
    <priority>STAT</priority>
    <expectedAt>2026-01-16T12:30:00Z</expectedAt>
    <receivedAt>2026-01-16T18:30:00Z</receivedAt>
    <reportedAt>2026-01-17T10:30:00Z</reportedAt>
  </sample>
  <sample>
    <id>SMP-2010</id>
    <patientId>PAT-030</patientId>
    <sex>F</sex>
    <age>28</age>
    <ascvdRisk></ascvdRisk>
    <specimenType>BLOOD</specimenType>
    <test>Platelet count</test>
    <context></context>
    <status>IN_PROGRESS</status>
    <value>250000</value>
    <unit>CELLS/µL</unit>
    <collectedAt>2026-01-16T10:35:00Z</collectedAt>
    <accession>ACC-2026-0110</accession>
    <result></result>
    <priority>ROUTINE</priority>
    <expectedAt>2026-01-16T12:30:00Z</expectedAt>
    <receivedAt>2026-01-16T12:15:00Z</receivedAt>
    <reportedAt></reportedAt>
  </sample>
  <!-- Intentionally invalid to show quarantine -->
  <sample>
    <id>bad id!</id>
    <patientId>PAT-031</patientId>
    <sex>F</sex>
    <age>30</age>
    <ascvdRisk></ascvdRisk>
    <specimenType>SERUM</specimenType>
    <test>Sodium</test>
    <context></context>
    <status>REPORTED</status>
    <value>-2</value>
    <unit>mEq/L</unit>
    <collectedAt>2027-05-01T00:00:00Z</collectedAt>
    <accession>TOO-LONG-REF-123456-OVER</accession>
    <result></result>
    <priority>ROUTINE</priority>
    <expectedAt>2026-01-16T12:30:00Z</expectedAt>
    <receivedAt>2026-01-16T13:00:00Z</receivedAt>
    <reportedAt></reportedAt>
  </sample>
  <sample>
    <id>SMP-2002</id>
    <patientId>PAT-022</patientId>
    <sex>M</sex>
    <age>58</age>
    <ascvdRisk>INTERMEDIATE</ascvdRisk>
    <specimenType>SERUM</specimenType>
    <test>LDL cholesterol</test>
    <context></context>
    <status>RECEIVED</status>
    <value>1.1</value>
    <unit>XYZ</unit>
    <collectedAt>2026-01-16T10:40:00Z</collectedAt>
    <accession>ACC-2026-0102</accession>
    <result></result>
    <priority>ROUTINE</priority>
    <expectedAt>2026-01-16T12:30:00Z</expectedAt>
    <receivedAt>2026-01-16T12:40:00Z</receivedAt>
    <reportedAt></reportedAt>
  </sample>
</samples>
`;

/* ===========================
   Initialization
   =========================== */
function init() {
  document.getElementById('year').textContent = new Date().getFullYear();

  // Fill test-name / unit lists and context choices from the reference table
  const tl = document.getElementById('testList');
  if (tl) REFERENCE_TESTS.forEach(t => { const o = document.createElement('option'); o.value = t[0]; tl.appendChild(o); });
  const ul = document.getElementById('unitList');
  if (ul) {
    const seen = new Set();
    REFERENCE_TESTS.forEach(t => t[3].forEach(v => [v[2], v[5]].forEach(u => {
      if (!seen.has(u)) { seen.add(u); const o = document.createElement('option'); o.value = u; ul.appendChild(o); }
    })));
  }
  const rs = document.getElementById('newRisk');
  if (rs) Object.entries(ASCVD_RISK_LABEL).forEach(([k, label]) => { const o = document.createElement('option'); o.value = k; o.textContent = label; rs.appendChild(o); });
  const cs = document.getElementById('newContext');
  if (cs) Object.entries(REFERENCE_CONTEXTS).forEach(([k, label]) => { const o = document.createElement('option'); o.value = k; o.textContent = label; cs.appendChild(o); });

  // Tabs
  document.querySelectorAll('.stcdp-tab').forEach(btn => {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab));
  });

  // Intake format selection
  document.querySelectorAll('input[name="format"]').forEach(r => {
    r.addEventListener('change', e => { STCDP.inputFormat = e.target.value; });
  });

  // Intake actions
  document.getElementById('btnLoadJson').addEventListener('click', () => {
    STCDP.inputFormat = 'json';
    document.querySelector('input[name="format"][value="json"]').checked = true;
    document.getElementById('inputData').value = JSON_EXAMPLE_TEXT;
  });
  document.getElementById('btnLoadXml').addEventListener('click', () => {
    STCDP.inputFormat = 'xml';
    document.querySelector('input[name="format"][value="xml"]').checked = true;
    document.getElementById('inputData').value = XML_EXAMPLE_TEXT.trim();
  });
  document.getElementById('btnClear').addEventListener('click', () => {
    document.getElementById('inputData').value = '';
    document.getElementById('intakeSummary').textContent = '';
  });

  document.getElementById('btnIntake').addEventListener('click', () => {
    const raw = document.getElementById('inputData').value.trim();
    const fmt = STCDP.inputFormat;
    const res = intakeAndValidate(raw, fmt);
    const summary = document.getElementById('intakeSummary');
    summary.textContent = res.ok ? res.message : `Intake failed: ${res.message}`;
    renderValidation();
    renderResults();
    renderAudit();
    renderDashboard();
    switchTab('validate');
  });

  // Process
  document.getElementById('btnProcess').addEventListener('click', () => {
    const count = processRecords();
    renderProcessSummary(count);
    renderAudit();
    renderDashboard();
  });

  // Reconcile
  document.getElementById('btnReconcile').addEventListener('click', () => {
    const stat = Number(document.getElementById('slaStatHours').value) || 48;
    const routine = Number(document.getElementById('slaRoutineHours').value) || 168;
    const n = reconcileRecords({ STAT: stat, ROUTINE: routine });
    renderReconcile();
    renderAudit();
    renderDashboard();
    if (!n) alert('No processed records to reconcile. Run "Process" first.');
  });

  // Export Audit
  document.getElementById('btnExportAudit').addEventListener('click', () => {
    const csv = auditToCsv();
    download(`audit_${STCDP.runId}.csv`, csv, 'text/csv');
  });
  document.getElementById('btnCopyAudit').addEventListener('click', async () => {
    const text = safeJsonStringify(STCDP.audit);
    try {
      await navigator.clipboard.writeText(text);
      alert('Audit JSON copied to clipboard.');
    } catch {
      download(`audit_${STCDP.runId}.json`, text, 'application/json');
    }
  });

  // Playbooks
  renderPlaybooks();
  document.getElementById('tblErrors').addEventListener('click', onClickPlaybook);

  /* ===== Editor wiring ===== */
  document.getElementById('btnShowCurrent').addEventListener('click', () => {
    const fmt = document.querySelector('input[name="editorFormat"]:checked').value;
    const text = (fmt === 'xml') ? recordsToXmlText() : recordsToJsonText();
    document.getElementById('editorText').value = text;
    document.getElementById('editorSummary').textContent = `Rendered current dataset as ${fmt.toUpperCase()}.`;
  });

  document.getElementById('btnBeautify').addEventListener('click', () => {
    const fmt = document.querySelector('input[name="editorFormat"]:checked').value;
    const el = document.getElementById('editorText');
    const raw = el.value.trim();
    try {
      if (fmt === 'json') {
        const obj = JSON.parse(raw);
        el.value = JSON.stringify(obj, null, 2);
      } else {
        // Naive pretty for XML: reparse and serialize via DOM
        const parser = new DOMParser();
        const xml = parser.parseFromString(raw, 'application/xml');
        const err = xml.querySelector('parsererror');
        if (err) throw new Error('XML parse error');
        const serializer = new XMLSerializer();
        const pretty = serializer.serializeToString(xml);
        el.value = pretty;
      }
      document.getElementById('editorSummary').textContent = 'Beautified successfully.';
    } catch (e) {
      document.getElementById('editorSummary').textContent = `Beautify failed: ${e.message}`;
    }
  });

  document.getElementById('filePicker').addEventListener('change', ev => {
    const file = ev.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const text = String(reader.result || '');
      document.getElementById('editorText').value = text;
      // Guess format by extension
      const isXml = /\.xml$/i.test(file.name);
      document.querySelector(`input[name="editorFormat"][value="${isXml ? 'xml' : 'json'}"]`).checked = true;
      document.getElementById('editorSummary').textContent = `Loaded ${file.name} (${text.length} bytes)`;
    };
    reader.readAsText(file);
  });

  document.getElementById('btnApplyEditor').addEventListener('click', () => {
    const fmt = document.querySelector('input[name="editorFormat"]:checked').value;
    const raw = document.getElementById('editorText').value.trim();
    if (!raw) {
      document.getElementById('editorSummary').textContent = 'Nothing to apply.';
      return;
    }
    const res = intakeAndValidate(raw, fmt);
    document.getElementById('editorSummary').textContent = res.ok ? `Applied: ${res.message}` : `Apply failed: ${res.message}`;
    renderValidation();
    renderResults();
    renderAudit();
    renderDashboard();
    switchTab('validate');
  });

  document.getElementById('btnDownloadCurrent').addEventListener('click', () => {
    const fmt = document.querySelector('input[name="editorFormat"]:checked').value;
    const text = (fmt === 'xml') ? recordsToXmlText() : recordsToJsonText();
    const ext = (fmt === 'xml') ? 'xml' : 'json';
    download(`clsdw_dataset_${STCDP.runId}.${ext}`, text, (fmt === 'xml') ? 'application/xml' : 'application/json');
  });

  /* ===== New Entry wiring ===== */
  document.getElementById('btnAddNew').addEventListener('click', () => {
    const id = document.getElementById('newId').value.trim();
    const patientId = document.getElementById('newPatientId').value.trim();
    const specimenType = document.getElementById('newSpecimen').value.trim().toUpperCase();
    const status = document.getElementById('newStatus').value.trim().toUpperCase();
    const valueNum = Number(document.getElementById('newValue').value);
    const unit = document.getElementById('newUnit').value.trim();
    const dtLocal = document.getElementById('newCollected').value; // yyyy-MM-ddTHH:mm
    const collectedAt = dtLocal ? new Date(dtLocal).toISOString().replace(/\.\d{3}Z$/, 'Z') : '';
    const accession = document.getElementById('newAccession').value.trim().toUpperCase();
    const toIso = v => v ? new Date(v).toISOString().replace(/\.\d{3}Z$/, 'Z') : '';
    const result = document.getElementById('newResult').value.trim().toUpperCase();
    const test = document.getElementById('newTest').value.trim();
    const age = document.getElementById('newAge').value;
    const ascvdRisk = document.getElementById('newRisk').value;
    const sex = document.getElementById('newSex').value;
    const context = document.getElementById('newContext').value;
    const priority = document.getElementById('newPriority').value.trim().toUpperCase();
    const expectedAt = toIso(document.getElementById('newExpected').value);
    const receivedAt = toIso(document.getElementById('newReceived').value);
    const reportedAt = toIso(document.getElementById('newReported').value);

    const newRec = normalizeRecord({ id, patientId, specimenType, status, value: valueNum, unit, collectedAt, accession, test, sex, age, ascvdRisk, context, result, priority, expectedAt, receivedAt, reportedAt, source: 'json' });
    const current = STCDP.records.slice();
    current.push(newRec);
    const res = intakeAndValidate(safeJsonStringify(current), 'json'); // re-run through intake pipeline
    document.getElementById('newSummary').textContent = res.ok ? `Added & validated. ${res.message}` : `Add failed: ${res.message}`;
    renderValidation();
    renderResults();
    renderAudit();
    renderDashboard();
    switchTab('validate');
  });

  /* ===== Dashboard animation toggle ===== */
  const animToggle = document.getElementById('toggleDashAnim');
  const animHost = document.getElementById('dashboardAnimHost');
  const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  STCDP.ui.dashAnimEnabled = !prefersReduced;
  animToggle.checked = STCDP.ui.dashAnimEnabled;
  if (!STCDP.ui.dashAnimEnabled) animHost.classList.remove('is-animated');

  animToggle.addEventListener('change', () => {
    STCDP.ui.dashAnimEnabled = !!animToggle.checked;
    animHost.classList.toggle('is-animated', STCDP.ui.dashAnimEnabled);
  });

  // Initial tab
  switchTab('intake');
}

document.addEventListener('DOMContentLoaded', init);

/* ===========================
   Notes
   - CSS/JS prefixed classes to avoid collisions
   - No external assets; CSP self-only
   - Editor supports load/apply/download for JSON/XML
   - Dashboard anim honors toggle and prefers-reduced-motion
   =========================== */
``