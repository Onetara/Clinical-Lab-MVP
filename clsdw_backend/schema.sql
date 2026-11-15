/* CLSDW: Clinical Lab & Sample Data Workflows */
/* PostgreSQL schema (tested on PostgreSQL 16; also fits Supabase). */

/* Idea: the Python backend validates records, then stores them here. The views at the bottom */
/* calculate the same efficiency numbers as the browser app and the API, straight from SQL: */
/*   v_dashboard        error rate, SLA breach rate, TAT (Lab time), throughput, delivery */
/*   v_delivery_by_specimen   late delivery vs lab time per specimen type */
/*   v_result_counts    Normal / Abnormal / ... counts */
/*   v_bench_shift      Relative efficiency (Raw) vs True efficiency (Fair) per bench and shift */

/* All data is synthetic. Real patient data would need HIPAA controls (encryption, access control, audit). */
/* Times are stored as timestamptz (UTC). Shifts use a fixed UTC-5 clock ("EST"): */
/*   Night 0000-0800, Morning 0800-1600, Afternoon 1600-0000. */
/* Reference ranges (NBME) and guideline categories (ADA, WHO, ACC/AHA) are applied by the app / backend; */
/* this schema stores the computed result next to each record. */

BEGIN;

/* =========================================================================== */
/* Lookup tables */
/* =========================================================================== */

CREATE TABLE error_codes (
    code   text PRIMARY KEY,
    title  text NOT NULL,
    plain  text NOT NULL            /* short plain-language meaning */
);

INSERT INTO error_codes (code, title, plain) VALUES
    ('E001', 'Missing Required Field', 'Blank field'),
    ('E002', 'Invalid Enumeration',    'Bad choice'),
    ('E003', 'Out-of-Range Value',     'Impossible value'),
    ('E004', 'Chronology Violation',   'Wrong time order'),
    ('E005', 'Plausibility Failure',   'Does not make sense'),
    ('E006', 'Duplicate Detected',     'Repeat entry'),
    ('E007', 'Format Check Failed',    'Bad format'),
    ('E009', 'XML Parse Error',        'Unreadable XML'),
    ('E010', 'JSON Parse Error',       'Unreadable JSON');

/* Deadline (SLA) in hours by priority. Project defaults; edit here to change every view. */
CREATE TABLE deadlines (
    priority text PRIMARY KEY CHECK (priority IN ('STAT', 'ROUTINE')),
    hours    integer NOT NULL CHECK (hours > 0)
);
INSERT INTO deadlines (priority, hours) VALUES ('STAT', 48), ('ROUTINE', 168);

/* Which lab bench normally runs a test (used when a record has no bench). */
CREATE TABLE test_bench (
    test_name text PRIMARY KEY CHECK (test_name = lower(test_name)),
    bench     text NOT NULL
);
INSERT INTO test_bench (test_name, bench) VALUES
    ('sodium', 'Chemistry'), ('potassium', 'Chemistry'), ('creatinine', 'Chemistry'), ('glucose', 'Chemistry'),
    ('alanine aminotransferase', 'Chemistry'), ('aspartate aminotransferase', 'Chemistry'), ('calcium', 'Chemistry'),
    ('triglycerides', 'Chemistry'), ('ldl cholesterol', 'Chemistry'), ('cholesterol, total', 'Chemistry'),
    ('tsh', 'Immunoassay'), ('ferritin', 'Immunoassay'), ('troponin i', 'Immunoassay'),
    ('hemoglobin a1c', 'Hematology'), ('hemoglobin', 'Hematology'),
    ('leukocyte count', 'Hematology'), ('platelet count', 'Hematology');

/* =========================================================================== */
/* One row per intake run */
/* =========================================================================== */

CREATE TABLE runs (
    run_id       text PRIMARY KEY,
    input_format text NOT NULL CHECK (input_format IN ('json', 'xml')),
    created_at   timestamptz NOT NULL DEFAULT now(),
    total        integer NOT NULL DEFAULT 0,
    valid        integer NOT NULL DEFAULT 0,
    errors       integer NOT NULL DEFAULT 0,
    quarantine   integer NOT NULL DEFAULT 0
);

/* =========================================================================== */
/* Valid records (records that fail validation go to quarantine instead) */
/* =========================================================================== */

CREATE TABLE specimens (
    specimen_pk       bigserial PRIMARY KEY,
    run_id            text NOT NULL REFERENCES runs (run_id) ON DELETE CASCADE,
    specimen_id       text NOT NULL CHECK (specimen_id ~ '^[A-Za-z0-9_-]{4,40}$'),
    accession         text NOT NULL DEFAULT '' CHECK (accession = '' OR accession ~ '^[A-Z0-9-]{8,30}$'),
    patient_id        text NOT NULL,
    specimen_type     text NOT NULL CHECK (specimen_type IN ('BLOOD', 'URINE', 'SWAB', 'SALIVA', 'PLASMA', 'SERUM', 'CSF')),
    status            text NOT NULL CHECK (status IN ('RECEIVED', 'IN_PROGRESS', 'REPORTED')),
    priority          text NOT NULL DEFAULT 'ROUTINE' REFERENCES deadlines (priority),
    test              text,
    value             numeric NOT NULL CHECK (value >= 0 AND value <= 1000000),
    unit              text NOT NULL,
    sex               text CHECK (sex IN ('M', 'F')),
    age               numeric CHECK (age >= 0 AND age <= 120),
    ascvd_risk        text CHECK (ascvd_risk IN ('LOW', 'BORDERLINE', 'INTERMEDIATE', 'HIGH')),   /* 10-year heart risk, entered */
    context           text CHECK (context IN ('FASTING', 'RANDOM', 'PREMENOPAUSE', 'FOLLICULAR', 'MIDCYCLE', 'POSTMENOPAUSE',
                                              'PROVOCATIVE', 'MORNING_0800', 'AFTERNOON_1600', 'PREGNANT_T1', 'PREGNANT_T2', 'PREGNANT_T3')),
/* Timeline */
    collected_at      timestamptz NOT NULL,
    expected_at       timestamptz,                 /* when the sample was due at the lab */
    received_at       timestamptz NOT NULL,        /* actual arrival at the lab (required) */
    bench_received_at timestamptz,                 /* when the bench got it (blank = same as received_at) */
    reported_at       timestamptz,
/* Bench workload */
    bench             text,                        /* blank = worked out from the test (see v_specimens) */
    test_count        integer NOT NULL DEFAULT 1 CHECK (test_count BETWEEN 1 AND 20),
/* Calculated by the app from the reference ranges (a typed result is replaced; INCONCLUSIVE is kept) */
    result            text CHECK (result IN ('NORMAL', 'ABNORMAL', 'INCONCLUSIVE', 'BORDERLINE', 'NO_RANGE',
                                             'UNIT_MISMATCH', 'SEX_NEEDED', 'CONTEXT_NEEDED')),

    UNIQUE (run_id, specimen_id, accession),
    CHECK (received_at >= collected_at),
    CHECK (reported_at IS NULL OR reported_at >= received_at),
    CHECK (bench_received_at IS NULL OR bench_received_at >= received_at),
    CHECK (reported_at IS NULL OR bench_received_at IS NULL OR reported_at >= bench_received_at),
    CHECK (status <> 'REPORTED' OR (coalesce(test, '') <> '' AND reported_at IS NOT NULL))
);
CREATE INDEX specimens_run_idx ON specimens (run_id);
CREATE INDEX specimens_received_idx ON specimens (received_at);

/* Reference-range flag and guideline category for each REPORTED record */
CREATE TABLE result_details (
    specimen_pk  bigint PRIMARY KEY REFERENCES specimens (specimen_pk) ON DELETE CASCADE,
    typed_result text,              /* what the file said (replaced by the calculated result) */
    flag         text CHECK (flag IN ('', 'HIGH', 'LOW')),
    range_text   text,
    dx_code      text,              /* e.g. PREDIABETES, ANEMIA_MILD, LDL_ABOVE_GOAL */
    dx_label     text,
    dx_source    text,              /* ADA, WHO or ACC/AHA guideline */
    dx_note      text
);

/* =========================================================================== */
/* Validation results and audit trail */
/* =========================================================================== */

CREATE TABLE validation_errors (
    error_pk  bigserial PRIMARY KEY,
    run_id    text NOT NULL REFERENCES runs (run_id) ON DELETE CASCADE,
    row_idx   integer NOT NULL,
    record_id text NOT NULL,
    code      text NOT NULL REFERENCES error_codes (code),
    message   text NOT NULL,
    field     text
);

CREATE TABLE quarantine (         /* On hold: failed validation, kept out of processing */
    quarantine_pk bigserial PRIMARY KEY,
    run_id        text NOT NULL REFERENCES runs (run_id) ON DELETE CASCADE,
    row_idx       integer NOT NULL,
    record_id     text NOT NULL,
    record        jsonb NOT NULL,
    reason        text NOT NULL DEFAULT 'Validation failure'
);

CREATE TABLE audit_log (
    audit_pk  bigserial PRIMARY KEY,
    run_id    text NOT NULL REFERENCES runs (run_id) ON DELETE CASCADE,
    logged_at timestamptz NOT NULL,
    stage     text NOT NULL,
    status    text,
    record_id text,
    checksum  text NOT NULL,       /* CRC32 of run | stage | record | notes */
    notes     text
);

/* =========================================================================== */
/* Views: the same numbers as the browser app and the API */
/* =========================================================================== */

/* One row per valid record with the derived values every other view needs. */
CREATE VIEW v_specimens WITH (security_invoker = true) AS
SELECT
    s.*,
    coalesce(nullif(s.bench, ''), tb.bench,
             CASE s.specimen_type
                 WHEN 'URINE'  THEN 'Urinalysis'
                 WHEN 'BLOOD'  THEN 'Hematology'
                 WHEN 'SERUM'  THEN 'Chemistry'
                 WHEN 'PLASMA' THEN 'Chemistry'
                 ELSE 'Unassigned'
             END)                                                              AS bench_name,
    coalesce(s.bench_received_at, s.received_at)                               AS bench_start,
/* Lab time (TAT): arrival to reported. Late delivery is not counted. */
    CASE WHEN s.status = 'REPORTED' AND s.reported_at IS NOT NULL
         THEN extract(epoch FROM (s.reported_at - s.received_at)) / 3600 END   AS lab_hours,
/* Delivery lateness: hours after the expected arrival (0 when on time or early) */
    CASE WHEN s.expected_at IS NOT NULL
         THEN greatest(0, extract(epoch FROM (s.received_at - s.expected_at)) / 3600) END AS late_hours,
    CASE WHEN s.expected_at IS NOT NULL THEN s.received_at > s.expected_at END AS arrived_late,
    d.hours                                                                    AS deadline_hours
FROM specimens s
LEFT JOIN test_bench tb ON tb.test_name = lower(regexp_replace(trim(coalesce(s.test, '')), '\s+', ' ', 'g'))
JOIN deadlines d ON d.priority = s.priority;

/* Run-level dashboard */
CREATE VIEW v_dashboard WITH (security_invoker = true) AS
WITH agg AS (
    SELECT run_id,
           count(*) FILTER (WHERE status = 'REPORTED' AND reported_at IS NOT NULL)            AS reported,
           count(lab_hours)                                                                   AS timed,
           count(*) FILTER (WHERE lab_hours > deadline_hours)                                 AS breaches,
           avg(lab_hours)                                                                     AS avg_lab_hours,
           count(arrived_late)                                                                AS delivery_known,
           count(*) FILTER (WHERE arrived_late)                                               AS late_deliveries,
           avg(late_hours) FILTER (WHERE late_hours > 0)                                      AS avg_late_hours,
           min(received_at) FILTER (WHERE status = 'REPORTED' AND reported_at IS NOT NULL)    AS first_in,
           max(reported_at) FILTER (WHERE status = 'REPORTED' AND reported_at IS NOT NULL)    AS last_out
    FROM v_specimens
    GROUP BY run_id
)
SELECT r.run_id,
       round(100.0 * r.quarantine / nullif(r.total, 0))                                       AS error_rate_pct,   /* quarantined / total */
       coalesce(round(100.0 * a.breaches / nullif(a.timed, 0)), 0)                            AS sla_breach_rate_pct,
       round(a.avg_lab_hours::numeric, 1)                                                     AS lab_time_hours_avg,
       r.quarantine                                                                           AS quarantine_size,
       round((CASE WHEN a.last_out > a.first_in
                   THEN a.reported / greatest(1, extract(epoch FROM (a.last_out - a.first_in)) / 86400)
                   ELSE a.reported END)::numeric, 1)                                          AS throughput_per_day,
       round(100.0 * (a.delivery_known - a.late_deliveries) / nullif(a.delivery_known, 0))    AS on_time_delivery_pct,
       round(a.avg_late_hours::numeric, 1)                                                    AS avg_lateness_of_late_hours
FROM runs r
LEFT JOIN agg a USING (run_id);

/* Bottleneck (Slowdown): late delivery vs lab time, by specimen type */
CREATE VIEW v_delivery_by_specimen WITH (security_invoker = true) AS
SELECT run_id, specimen_type,
       round(avg(late_hours)::numeric, 1) AS avg_late_delivery_hours,
       round(avg(lab_hours)::numeric, 1)  AS avg_lab_hours
FROM v_specimens
GROUP BY run_id, specimen_type;

CREATE VIEW v_result_counts WITH (security_invoker = true) AS
SELECT run_id, result, count(*) AS records
FROM specimens
WHERE status = 'REPORTED' AND result IS NOT NULL
GROUP BY run_id, result;

/* Relative efficiency (Raw) vs True efficiency (Fair) for every lab bench and shift. */
/* A sample belongs to the shift in which its bench received it (fixed UTC-5 clock). */
/*   Raw  = finished inside the shift / received in the shift (how benches are graded today). */
/*   Fair = same, leaving out samples that reached the bench with less shift time left than the bench */
/*          normally needs (typical hours per test x number of tests on the sample). */
CREATE VIEW v_bench_shift WITH (security_invoker = true) AS
WITH base AS (
    SELECT v.*,
           v.bench_start AT TIME ZONE 'Etc/GMT+5' AS local_start         /* Etc/GMT+5 is UTC-5 */
    FROM v_specimens v
), shifted AS (
    SELECT b.*,
           floor(extract(hour FROM b.local_start) / 8)::int AS shift_no,
           ((date_trunc('day', b.local_start)
             + (floor(extract(hour FROM b.local_start) / 8)::int + 1) * interval '8 hours')
            AT TIME ZONE 'Etc/GMT+5') AS shift_end
    FROM base b
), typical AS (                                                          /* typical hours per test, per bench */
    SELECT run_id, bench_name,
           percentile_cont(0.5) WITHIN GROUP (
               ORDER BY extract(epoch FROM (reported_at - bench_start)) / 3600 / test_count) AS hours_per_test
    FROM shifted
    WHERE status = 'REPORTED' AND reported_at IS NOT NULL
    GROUP BY run_id, bench_name
), flagged AS (
    SELECT s.*,
           (s.status = 'REPORTED' AND s.reported_at IS NOT NULL AND s.reported_at <= s.shift_end) AS finished,
           extract(epoch FROM (s.shift_end - s.bench_start)) / 3600                              AS hours_left,
           coalesce(t.hours_per_test, 0) * s.test_count                                           AS hours_needed
    FROM shifted s
    LEFT JOIN typical t USING (run_id, bench_name)
)
SELECT run_id,
       bench_name                                                                                     AS bench,
       (ARRAY['Night', 'Morning', 'Afternoon'])[shift_no + 1]                                         AS shift,
       count(*)                                                                                       AS received,
       round(100.0 * count(*) FILTER (WHERE finished) / count(*))                                     AS relative_efficiency_raw_pct,
       count(*) FILTER (WHERE hours_left < hours_needed)                                              AS late_arrivals,
       round(100.0 * count(*) FILTER (WHERE finished AND hours_left >= hours_needed)
             / nullif(count(*) FILTER (WHERE hours_left >= hours_needed), 0))                         AS true_efficiency_fair_pct,
       round(100.0 * count(*) FILTER (WHERE finished AND hours_left >= hours_needed)
             / nullif(count(*) FILTER (WHERE hours_left >= hours_needed), 0))
         - round(100.0 * count(*) FILTER (WHERE finished) / count(*))                                 AS gap_points,
       count(*) FILTER (WHERE test_count > 1)                                                         AS multi_test_samples,
       round((avg(extract(epoch FROM (reported_at - bench_start)) / 3600)
              FILTER (WHERE status = 'REPORTED' AND reported_at IS NOT NULL))::numeric, 1)            AS avg_bench_hours,
       round(avg(extract(epoch FROM (bench_start - received_at)) / 3600)::numeric, 1)                 AS avg_handoff_wait_hours,
       round(avg(late_hours)::numeric, 1)                                                             AS avg_late_delivery_hours
FROM flagged
GROUP BY run_id, bench_name, shift_no;

/* =========================================================================== */
/* Row Level Security (Supabase) */
/* With RLS on and no policies, the public API (anon key) can read or write nothing. */
/* Your backend uses the service-role key, which bypasses RLS. Views use security_invoker */
/* so they follow the same rules. Add policies later only for the access you want to allow. */
/* =========================================================================== */

ALTER TABLE error_codes       ENABLE ROW LEVEL SECURITY;
ALTER TABLE deadlines         ENABLE ROW LEVEL SECURITY;
ALTER TABLE test_bench        ENABLE ROW LEVEL SECURITY;
ALTER TABLE runs              ENABLE ROW LEVEL SECURITY;
ALTER TABLE specimens         ENABLE ROW LEVEL SECURITY;
ALTER TABLE result_details    ENABLE ROW LEVEL SECURITY;
ALTER TABLE validation_errors ENABLE ROW LEVEL SECURITY;
ALTER TABLE quarantine        ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log         ENABLE ROW LEVEL SECURITY;

COMMIT;
