# Clinical Lab Data Workflow

Created By: Dr. Stacy Toriola, MD, MSc.

Production-style monitoring dashboard simulating a high-throughput clinical genomics pipeline (genomics = DNA/RNA testing).
Goal: monitor lab efficiency. Terms are written as Lab/tech term (Short word) so non-lab readers can follow.

## What it tracks
- **Results:** Normal (In range), Abnormal (Out of range), Inconclusive (Unclear).
- **SLA breach (Missed deadline) rate:** share of reported results whose TAT (Wait time) exceeded the SLA (Deadline). TAT (Wait time) = result reported minus sample arrival. STAT (Urgent) = 48 hours, Routine = 7 days (editable on the Reconcile tab).
- **Quarantine (On hold):** records that failed validation and are held out of processing.
- **Throughput (Daily output):** reported results per day.
- **Specimen (Sample) delivery, expected vs actual:** on-time delivery rate and average hours late by specimen type, used for Bottleneck (Slowdown) analysis.
- **Error categories:** every error code is defined on the dashboard and in the Playbooks tab.

Designed around a PostgreSQL (Supabase) relational schema using synthetic data to model sample flow, QC (Quality check) failures, and operational bottlenecks.

## Automatic results
Normal / Abnormal is calculated from the value, not typed. For each REPORTED sample the app looks up the test in `reference_ranges.js`, picks the range by patient sex and context (for example Fasting or Random glucose), and checks the unit (conventional or SI).
- Normal (In range), Abnormal (Out of range) with a High / Low flag, Borderline (Near limit)
- Inconclusive (Unclear): kept only when the lab marks it
- No range (Not listed), Unit mismatch (Wrong unit), Sex needed (Missing sex), Context needed (Missing detail)

The computed result replaces any typed result.

Reference ranges: NBME Laboratory Values reference sheet.

## Diagnostic categories (Guideline)
A separate column applies national and international guidelines (`diagnostic_criteria.js`). Normal / Abnormal says whether a value is in the lab's range; a guideline category says what it means.
- **Glucose and hemoglobin A1c:** ADA Standards of Care in Diabetes 2026 (below diabetes range, Prediabetes (Higher risk), Diabetes range (Repeat to confirm)).
- **Hemoglobin:** WHO 2024 anemia cutoffs by sex, age (15 and older) and pregnancy. Not adjusted for altitude or smoking.
- **LDL cholesterol:** 2026 ACC/AHA dyslipidemia guideline goals by 10-year heart risk. The risk (PREVENT calculator) is entered, not calculated.

## Data fields
`test`, `sex` (M or F), `age`, `ascvdRisk` (LOW, BORDERLINE, INTERMEDIATE or HIGH), `context`, `priority` (STAT or ROUTINE), `expectedAt`, `receivedAt`, `reportedAt` (ISO-8601 timestamps). `test` and `reportedAt` are required when `status` is REPORTED.

## Dashboard Overview
![CLSDW Clinical Lab Overview](dashboar.png)

## Reconciliation / SLA Breach Status
![SLA Breach](reconcile.png)

## Validation Result
![Result Validation](validation.png)

## Troubleshoot Index
![Troubleshoot Index](troubleshoot.png)
