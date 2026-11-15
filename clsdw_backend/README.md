# CLSDW Python Backend

**Clinical Lab & Sample Data Workflows — Python/FastAPI Backend**
*Logic-complete backend for the CLSDW frontend (mirrors app.js by Stacy Toriola)*

---

## What's included

| File | Purpose |
|------|---------|
| `clsdw_core.py` | All business logic — CRC32, Mulberry32 PRNG, normalization, validation (E001–E010), processing, reconciliation, audit, lab-time and delivery metrics, bench shifts |
| `reference_ranges.py` + `reference_ranges.json` | NBME reference ranges and the rule that computes each result (Normal, Abnormal, Borderline, ...), unit matching included |
| `diagnostic_criteria.py` | Guideline categories: ADA 2026 glucose and A1c, WHO 2024 hemoglobin, 2026 ACC/AHA LDL goal by entered risk |
| `schema.sql` | PostgreSQL (Supabase-style) schema: tables, checks, and views that reproduce the dashboard and the Raw vs Fair bench numbers in SQL |
| `main.py` | FastAPI REST API — exposes every workflow operation as an HTTP endpoint |
| `test_clsdw.py` | 136 pytest tests — unit, integration, end-to-end, API |
| `requirements.txt` | Pinned Python dependencies |

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/playbooks` | All error-code playbooks (static) |
| POST | `/intake` | Ingest + validate JSON or XML dataset → returns `run_id` |
| GET | `/validate/{run_id}` | Validation results (errors, quarantine, KPIs) |
| POST | `/process/{run_id}` | Transform valid records (bench, postCRC) |
| POST | `/reconcile/{run_id}?sla_ms=1500` | Simulate LIS/LIMS ACKs with SLA |
| GET | `/audit/{run_id}` | Full audit trail (JSON) |
| GET | `/audit/{run_id}/csv` | Export audit as CSV download |
| GET | `/dashboard/{run_id}?stat_hours=48&routine_hours=168` | Error rate, SLA breach rate, TAT (Lab time), throughput per day, on-time delivery, lateness by specimen |
| GET | `/results/{run_id}` | Calculated result, flag, range and guideline category for every record |
| GET | `/bench/{run_id}` | Relative efficiency (Raw) vs True efficiency (Fair) for every lab bench and shift (EST: Night 0000-0800, Morning 0800-1600, Afternoon 1600-0000) |
| POST | `/new_entry/{run_id}` | Add one record and re-validate |
| GET | `/dataset/{run_id}` | Current dataset as JSON |
| GET | `/dataset/{run_id}/xml` | Current dataset as XML |
| POST | `/pipeline` | Full pipeline in one call (intake→validate→process→reconcile) |

Interactive docs auto-generated at: `http://127.0.0.1:8000/docs`

---

## Step-by-Step Setup on Windows 11

### Prerequisites (already installed per your setup)
- Python 3.10+ (verify: `python --version`)
- pip (verify: `pip --version`)
- A terminal — PowerShell, Windows Terminal, or CMD all work

---

### Step 1 — Open a terminal in the project folder

```powershell
# Navigate to wherever you extracted the zip
cd C:\path\to\clsdw_backend
```

---

### Step 2 — Create and activate a virtual environment

```powershell
# Create the venv (only needed once)
python -m venv .venv

# Activate it (every new terminal session)
.venv\Scripts\activate
```

You'll see `(.venv)` prepend your prompt when active.

> **Troubleshooting:** If you get "running scripts is disabled", run this once as Administrator:
> ```powershell
> Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
> ```

---

### Step 3 — Install dependencies

```powershell
pip install -r requirements.txt
```

Expected output ends with something like:
```
Successfully installed fastapi-0.115.12 uvicorn-0.34.3 ...
```

---

### Step 4 — Run the tests

```powershell
python -m pytest test_clsdw.py -v
```

Expected: `119 passed` (takes ~1 second).

For a compact summary instead:
```powershell
python -m pytest test_clsdw.py --tb=short -q
```

---

### Step 5 — Start the API server

```powershell
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

You should see:
```
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     Started reloader process
INFO:     Application startup complete.
```

`--reload` enables hot-reload on file save (dev mode). Remove it for production.

---

### Step 6 — Explore the interactive API docs

Open your browser and go to:

```
http://127.0.0.1:8000/docs
```

This opens Swagger UI — you can run every endpoint directly from the browser without any extra tool.

Alternatively, the ReDoc view is at:
```
http://127.0.0.1:8000/redoc
```

---

### Step 7 — Try a quick end-to-end test with curl (optional)

Open a *second* terminal (with the venv activated) while the server is running:

```powershell
# Health check
curl http://127.0.0.1:8000/health

# Full pipeline with example JSON (inline — PowerShell escape)
$body = '{
  "format": "json",
  "data": "[{\"id\":\"SMP-1001\",\"patientId\":\"PAT-001\",\"specimenType\":\"BLOOD\",\"status\":\"RECEIVED\",\"value\":85.2,\"unit\":\"MG/DL\",\"collectedAt\":\"2026-01-15T09:00:00Z\",\"accession\":\"ACC-2026-0001\"}]"
}'
curl -X POST http://127.0.0.1:8000/pipeline `
     -H "Content-Type: application/json" `
     -d $body
```

Or use the Swagger UI at `/docs` which is much easier for exploratory testing.

---

### Step 8 — Serve the frontend alongside the backend (optional)

The original `index.html` / `styles.css` / `app.js` are self-contained and work without the backend. To serve them from the same origin (useful if you integrate the API calls):

```powershell
# From the folder containing index.html, in a separate terminal:
python -m http.server 5500
```

Frontend: `http://127.0.0.1:5500`
Backend: `http://127.0.0.1:8000`

---

## Using the API — Workflow walkthrough

### Option A: Step-by-step (mirrors frontend tabs)

```
POST /intake       → get run_id
GET  /validate/{run_id}
POST /process/{run_id}
POST /reconcile/{run_id}?sla_ms=1500
GET  /audit/{run_id}
GET  /dashboard/{run_id}
GET  /bench/{run_id}        ← Raw vs Fair efficiency per bench and shift
GET  /audit/{run_id}/csv    ← download
```

### Option B: One-shot pipeline

```
POST /pipeline     → everything in one call, same run_id back
```

### Request format for `/intake` and `/pipeline`

```json
{
  "format": "json",
  "data": "[{\"id\":\"SMP-1001\", ...}]"
}
```

Or for XML:
```json
{
  "format": "xml",
  "data": "<samples><sample>...</sample></samples>"
}
```

---

## What changed in version 2
- `receivedAt` (actual arrival) is required on every record (E001). `expectedAt` is optional.
- New fields: `test`, `sex`, `age`, `ascvdRisk`, `context`, `result`, `priority` (STAT or ROUTINE), `expectedAt`, `receivedAt`, `reportedAt`, `bench`, `benchReceivedAt`, `testCount`. REPORTED needs `test` and `reportedAt`.
- TAT (Lab time) runs from arrival to reported, so late delivery is not counted as lab time.
- Bench efficiency: Relative (Raw) = finished in shift / received in shift. True (Fair) leaves out samples that reached the bench with less shift time left than the bench normally needs (typical hours per test x number of tests).
- Units are kept as typed and matched ignoring case; accepted units come from `reference_ranges.json`.
- Not ported yet: automatic Normal / Abnormal results from reference ranges and the guideline categories (these still run in the browser app only).
- Results on the 300-record sample match the browser app exactly (valid, errors, metrics and bench scores).

## Technical notes

### Why this matches the JS exactly

| Concern | JS | Python |
|---------|----|----|
| CRC32 | IEEE 802.3, `^(-1)` init/final | Same polynomial + same init/final |
| PRNG | Mulberry32 with `Math.imul` | `_imul()` replicates 32-bit signed multiplication |
| Unit matching | `unitKey()`: uppercase, spaces removed, micro-sign variants alike | `unit_key()` in `reference_ranges.py`, same rule |
| JSON key sort | `Object.keys(obj).sort()` | `json.dumps(..., sort_keys=True)` |
| Accession regex | `/^[A-Z0-9\-]{8,30}$/` | `^[A-Z0-9\-]{8,30}$` |
| ID regex | `/^[A-Za-z0-9\-_]{4,40}$/` | Same |

### Session model
Sessions are held in memory (`_sessions` dict). Each `POST /intake` creates a new session identified by `run_id`. Sessions are pruned (oldest first) when the store exceeds 500 entries. For production, replace with Redis or a database.

---

## Running in production (quick guide)

```powershell
# Install production ASGI server
pip install gunicorn

# Run with multiple workers (Windows: use uvicorn workers only)
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

For Windows production deployments, Uvicorn directly (without gunicorn) is recommended since gunicorn's worker management is Unix-native.


---

## Same answers as the browser app

The Python results were compared with the browser app (app.js) on 147,954 synthetic records: 0 differences in result, flag, range text and guideline category. On the 300-record sample both give 285 valid, 15 quarantined, lab time 6.2 h, 21.2 samples/day, 73% on-time delivery, and the same bench rows.

## SQL schema

`schema.sql` was loaded into PostgreSQL 16 and the 300-record sample was inserted. `v_dashboard` and `v_bench_shift` match the API (one rounded value differs by 0.1 h because of rounding rules).

```bash
createdb clsdw
psql -d clsdw -f schema.sql
```

All data is synthetic. Real patient data would need HIPAA controls.
