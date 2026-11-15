"""
main.py — CLSDW FastAPI Backend
Exposes REST endpoints that mirror every operation in app.js.

Session model: each POST /intake creates a new run and returns a run_id.
Subsequent calls pass run_id in the path (or query param) to retrieve state.

Endpoints
─────────
POST   /intake                  intake + validate (JSON or XML body)
GET    /validate/{run_id}       validation results for a run
POST   /process/{run_id}        process valid records
POST   /reconcile/{run_id}      run reconciliation (optional ?sla_ms=)
GET    /audit/{run_id}          full audit trail (JSON)
GET    /audit/{run_id}/csv      export audit as CSV download
GET    /dashboard/{run_id}      dashboard metrics (lab time, deadlines, delivery, throughput)
GET    /results/{run_id}        automatic Normal / Abnormal results and guideline categories
GET    /bench/{run_id}          Relative (Raw) vs True (Fair) efficiency per bench and shift
GET    /playbooks               error-code playbooks
POST   /new_entry/{run_id}      add a single new record and re-validate
GET    /dataset/{run_id}        current dataset as JSON
GET    /dataset/{run_id}/xml    current dataset as XML
GET    /health                  health check
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field

from reference_ranges import RESULT_LABEL

from clsdw_core import (
    CLSDWState,
    ERROR_BOOK,
    intake_and_validate,
    normalize_record,
    process_records,
    reconcile_records,
    audit_to_csv,
    compute_efficiency,
    compute_bench_shifts,
    records_to_xml,
    safe_json_stringify,
    parse_json,
    parse_xml,
    create_run_id,
)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(
    title="CLSDW — Clinical Lab & Sample Data Workflows API",
    description="Python backend for the CLSDW frontend (mirrors app.js logic)",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],     # tighten for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory session store  { run_id: CLSDWState }
_sessions: dict[str, CLSDWState] = {}

MAX_SESSIONS = 500   # prune oldest sessions beyond this


def _get_session(run_id: str) -> CLSDWState:
    if run_id not in _sessions:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found. Call POST /intake first.")
    return _sessions[run_id]


def _prune_sessions():
    if len(_sessions) > MAX_SESSIONS:
        oldest = list(_sessions.keys())[0]
        del _sessions[oldest]


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class IntakeRequest(BaseModel):
    format: str = Field("json", description="'json' or 'xml'")
    data: str = Field(..., description="Raw JSON array or XML string")


class NewEntryRequest(BaseModel):
    id: str = ""
    patientId: str = ""
    specimenType: str = "BLOOD"
    status: str = "RECEIVED"
    value: Optional[float] = None
    unit: str = "MG/DL"
    collectedAt: str = ""
    accession: str = ""
    test: str = ""
    sex: str = ""
    age: Optional[float] = None
    ascvdRisk: str = ""
    context: str = ""
    priority: str = "ROUTINE"
    expectedAt: str = ""
    receivedAt: str = Field("", description="Actual arrival at the lab (required, ISO-8601 UTC)")
    reportedAt: str = ""
    bench: str = Field("", description="Lab bench; blank = worked out from the test")
    benchReceivedAt: str = Field("", description="When the bench got the sample; blank = same as receivedAt")
    testCount: int = Field(1, description="Number of tests on this sample")


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health", tags=["Meta"])
def health():
    return {"status": "ok", "service": "CLSDW Python Backend", "sessions": len(_sessions)}


# ---------------------------------------------------------------------------
# Playbooks (static, no session needed)
# ---------------------------------------------------------------------------

@app.get("/playbooks", tags=["Playbooks"])
def get_playbooks():
    """Return all error-code playbooks."""
    return ERROR_BOOK


# ---------------------------------------------------------------------------
# Intake
# ---------------------------------------------------------------------------

@app.post("/intake", tags=["Workflow"])
def intake(req: IntakeRequest):
    """
    Ingest and validate a clinical dataset (JSON array or XML).
    Returns a run_id for subsequent calls.
    """
    fmt = req.format.strip().lower()
    if fmt not in ("json", "xml"):
        raise HTTPException(status_code=422, detail="format must be 'json' or 'xml'")

    state = CLSDWState()
    result = intake_and_validate(req.data, fmt, state)

    _prune_sessions()
    _sessions[state.run_id] = state

    return {
        "runId": state.run_id,
        "ok": result["ok"],
        "message": result["message"],
        "summary": state.to_summary(),
    }


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------

@app.get("/validate/{run_id}", tags=["Workflow"])
def get_validation(run_id: str):
    """Return validation results for a run."""
    state = _get_session(run_id)
    return {
        "runId": run_id,
        "metrics": {
            "total": state.metrics["total"],
            "valid": state.metrics["valid"],
            "errors": state.metrics["errors"],
            "quarantine": state.metrics["quarantine"],
        },
        "errors": state.errors,
        "quarantined": state.quarantined,
        "errorBook": {
            code: ERROR_BOOK.get(code, {}) for code in state.metrics.get("errorByCode", {})
        },
    }


# ---------------------------------------------------------------------------
# Process
# ---------------------------------------------------------------------------

@app.post("/process/{run_id}", tags=["Workflow"])
def process(run_id: str):
    """Transform/enrich valid records. Assigns category and postCRC checksum."""
    state = _get_session(run_id)
    if not state.valid_records:
        raise HTTPException(status_code=400, detail="No valid records to process. Run intake first.")
    processed = process_records(state)
    return {
        "runId": run_id,
        "processedCount": len(processed),
        "records": processed,
    }


# ---------------------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------------------

@app.post("/reconcile/{run_id}", tags=["Workflow"])
def reconcile(
    run_id: str,
    sla_ms: int = Query(1500, ge=100, description="SLA threshold in milliseconds"),
):
    """
    Simulate downstream ACKs (LIS/LIMS) with SLA enforcement.
    Deterministic: same run_id + sla_ms always yields same results.
    """
    state = _get_session(run_id)
    if not state.processed:
        raise HTTPException(
            status_code=400,
            detail="No processed records. Call POST /process/{run_id} first.",
        )
    acks = reconcile_records(state, sla_ms)
    return {
        "runId": run_id,
        "slaMs": sla_ms,
        "acked": state.metrics["acked"],
        "slaBreaches": state.metrics["slaBreaches"],
        "acks": acks,
    }


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

@app.get("/audit/{run_id}", tags=["Audit"])
def get_audit(run_id: str):
    """Return full audit trail for a run."""
    state = _get_session(run_id)
    return {"runId": run_id, "audit": state.audit}


@app.get("/audit/{run_id}/csv", tags=["Audit"])
def export_audit_csv(run_id: str):
    """Download audit trail as CSV."""
    state = _get_session(run_id)
    csv_text = audit_to_csv(state)
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="audit_{run_id}.csv"'},
    )


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.get("/dashboard/{run_id}", tags=["Dashboard"])
def get_dashboard(
    run_id: str,
    stat_hours: int = Query(48, ge=1, description="Deadline for STAT samples, in hours"),
    routine_hours: int = Query(168, ge=1, description="Deadline for ROUTINE samples, in hours"),
):
    """Return dashboard metrics for a run.

    SLA breach rate = reported results whose lab time (arrival to reported) passed the deadline.
    Error rate = quarantined records / total records.
    """
    state = _get_session(run_id)
    total = state.metrics["total"]
    quarantine = state.metrics["quarantine"]
    eff = compute_efficiency(state.valid_records, {"STAT": stat_hours, "ROUTINE": routine_hours})
    error_rate = round((quarantine / total) * 100) if total else 0

    return {
        "runId": run_id,
        "errorRate": f"{error_rate}%",
        "slaBreachRate": f"{eff['slaBreachRate']}%",
        "resultCounts": eff["resultCounts"],
        "labTimeHoursAvg": eff["labTimeHoursAvg"],
        "quarantineSize": quarantine,
        "throughputPerDay": eff["throughputPerDay"],
        "onTimeDeliveryRate": eff["onTimeDeliveryRate"],
        "avgLatenessOfLateSamplesHours": eff["avgLatenessOfLateSamplesHours"],
        "deadlinesHours": eff["deadlinesHours"],
        "bySpecimen": eff["bySpecimen"],
        "errorByCode": state.metrics["errorByCode"],
        "ackDelays": state.metrics["ackDelays"],
    }


# ---------------------------------------------------------------------------
# Automatic results and guideline categories
# ---------------------------------------------------------------------------

@app.get("/results/{run_id}", tags=["Dashboard"])
def get_results(run_id: str):
    """Result for every REPORTED record, calculated from the NBME reference ranges (typed results are replaced).

    Each row has the reference range used, a High / Low flag, and, where a national guideline applies
    (ADA glucose and A1c, WHO hemoglobin, ACC/AHA LDL), the guideline category.
    """
    state = _get_session(run_id)
    rows = []
    for rec in state.valid_records:
        info = state.result_info.get(rec["id"])
        if not info:
            continue
        rows.append({
            "id": rec["id"], "test": rec["test"], "value": rec["value"], "unit": rec["unit"],
            "result": info["result"], "resultLabel": RESULT_LABEL[info["result"]],
            "flag": info["flag"], "range": info["rangeText"],
            "typedResultReplaced": bool(info["typed"]) and info["typed"] != info["result"],
            "diagnosis": info["dx"],
        })
    return {"runId": run_id, "count": len(rows), "results": rows}


# ---------------------------------------------------------------------------
# Bench shifts: Relative (Raw) vs True (Fair) efficiency
# ---------------------------------------------------------------------------

@app.get("/bench/{run_id}", tags=["Dashboard"])
def get_bench_shifts(run_id: str):
    """Efficiency per lab bench and shift (EST: Night 0000-0800, Morning 0800-1600, Afternoon 1600-0000).

    Relative efficiency (Raw): finished in shift / received in shift.
    True efficiency (Fair): same, leaving out samples that reached the bench with less shift time
    left than the bench normally needs (time needed grows with the number of tests).
    """
    state = _get_session(run_id)
    rows = compute_bench_shifts(state.valid_records)
    return {"runId": run_id, "shiftsEST": "Night 0000-0800, Morning 0800-1600, Afternoon 1600-0000", "benchShifts": rows}


# ---------------------------------------------------------------------------
# New Entry
# ---------------------------------------------------------------------------

@app.post("/new_entry/{run_id}", tags=["Workflow"])
def add_new_entry(run_id: str, entry: NewEntryRequest):
    """
    Add a single clinical sample record to the dataset, then re-run
    full intake+validation pipeline.
    """
    state = _get_session(run_id)

    # Build normalized record
    new_rec = normalize_record({
        "id": entry.id,
        "patientId": entry.patientId,
        "specimenType": entry.specimenType,
        "status": entry.status,
        "value": entry.value,
        "unit": entry.unit,
        "collectedAt": entry.collectedAt,
        "accession": entry.accession,
        "test": entry.test,
        "sex": entry.sex,
        "age": entry.age,
        "ascvdRisk": entry.ascvdRisk,
        "context": entry.context,
        "priority": entry.priority,
        "expectedAt": entry.expectedAt,
        "receivedAt": entry.receivedAt,
        "reportedAt": entry.reportedAt,
        "bench": entry.bench,
        "benchReceivedAt": entry.benchReceivedAt,
        "testCount": entry.testCount,
        "source": "json",
    })

    # Append to existing dataset
    current = state.records[:]
    current.append(new_rec)

    # Re-run intake pipeline (mirrors JS btnAddNew logic)
    result = intake_and_validate(safe_json_stringify(current), "json", state)

    return {
        "runId": state.run_id,
        "ok": result["ok"],
        "message": result["message"],
        "summary": state.to_summary(),
    }


# ---------------------------------------------------------------------------
# Dataset access
# ---------------------------------------------------------------------------

@app.get("/dataset/{run_id}", tags=["Dataset"])
def get_dataset_json(run_id: str):
    """Return current dataset as JSON."""
    state = _get_session(run_id)
    return {"runId": run_id, "format": "json", "records": state.records}


@app.get("/dataset/{run_id}/xml", tags=["Dataset"])
def get_dataset_xml(run_id: str):
    """Return current dataset as XML."""
    state = _get_session(run_id)
    xml_text = records_to_xml(state.records)
    return Response(content=xml_text, media_type="application/xml")


# ---------------------------------------------------------------------------
# Full pipeline convenience endpoint
# ---------------------------------------------------------------------------

@app.post("/pipeline", tags=["Workflow"])
def run_full_pipeline(
    req: IntakeRequest,
    sla_ms: int = Query(1500, ge=100, description="SLA threshold in milliseconds"),
):
    """
    Run the complete pipeline in one call:
      intake → validate → process → reconcile
    Returns all results plus run_id.
    """
    fmt = req.format.strip().lower()
    if fmt not in ("json", "xml"):
        raise HTTPException(status_code=422, detail="format must be 'json' or 'xml'")

    state = CLSDWState()
    iv = intake_and_validate(req.data, fmt, state)
    if not iv["ok"]:
        _prune_sessions()
        _sessions[state.run_id] = state
        return {
            "runId": state.run_id,
            "ok": False,
            "message": iv["message"],
            "stage": "intake",
        }

    processed = process_records(state)
    acks = reconcile_records(state, sla_ms)

    _prune_sessions()
    _sessions[state.run_id] = state

    total = state.metrics["total"]
    error_rate = round((state.metrics["quarantine"] / total) * 100) if total else 0
    eff = compute_efficiency(state.valid_records)

    return {
        "runId": state.run_id,
        "ok": True,
        "message": iv["message"],
        "validation": {
            "total": state.metrics["total"],
            "valid": state.metrics["valid"],
            "errors": state.metrics["errors"],
            "quarantine": state.metrics["quarantine"],
            "errorList": state.errors,
            "quarantined": state.quarantined,
        },
        "processing": {
            "processedCount": len(processed),
        },
        "reconciliation": {
            "slaMs": sla_ms,
            "acked": state.metrics["acked"],
            "slaBreaches": state.metrics["slaBreaches"],
            "acks": acks,
        },
        "dashboard": {
            "errorRate": f"{error_rate}%",
            "slaBreachRate": f"{eff['slaBreachRate']}%",
            "labTimeHoursAvg": eff["labTimeHoursAvg"],
            "quarantineSize": state.metrics["quarantine"],
            "throughputPerDay": eff["throughputPerDay"],
            "onTimeDeliveryRate": eff["onTimeDeliveryRate"],
            "avgLatenessOfLateSamplesHours": eff["avgLatenessOfLateSamplesHours"],
            "errorByCode": state.metrics["errorByCode"],
            "ackDelays": state.metrics["ackDelays"],
        },
        "benchShifts": compute_bench_shifts(state.valid_records),
    }
