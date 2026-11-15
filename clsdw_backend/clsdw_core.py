"""
clsdw_core.py: Clinical Lab & Sample Data Workflows: Python Logic Backend
Mirrors app.js (Stacy Toriola / Anthony O.) deterministically.

All business rules replicated:
  - CRC32 (IEEE 802.3, identical polynomial to JS version)
  - Mulberry32 PRNG for deterministic reconciliation
  - Clinical validation gates (E001–E010)
  - Audit trail with per-entry checksums
  - Processing & category assignment
  - Reconciliation with SLA enforcement
"""

from __future__ import annotations

import json
import re
import time
import math
import random
import hashlib
import csv
import io
import copy
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from xml.etree import ElementTree as ET
from xml.etree.ElementTree import ParseError as XmlParseError

from reference_ranges import (
    REFERENCE_CONTEXTS, REF_INDEX, VALID_RESULT, classify_record, is_known_unit, _norm_name,
)
from diagnostic_criteria import ASCVD_RISK_LABEL, diagnose_record


# ---------------------------------------------------------------------------
# CRC32 (IEEE 802.3), matches JS implementation exactly
# ---------------------------------------------------------------------------

def _build_crc32_table() -> list[int]:
    table = []
    for n in range(256):
        c = n
        for _ in range(8):
            if c & 1:
                c = 0xEDB88320 ^ (c >> 1)
            else:
                c = c >> 1
        table.append(c & 0xFFFFFFFF)
    return table


_CRC32_TABLE = _build_crc32_table()


def crc32(s: str) -> int:
    """Compute CRC32 matching the JS IEEE 802.3 implementation."""
    crc = 0xFFFFFFFF
    for ch in s.encode("utf-8"):
        crc = (crc >> 8) ^ _CRC32_TABLE[(crc ^ ch) & 0xFF]
    return (crc ^ 0xFFFFFFFF) & 0xFFFFFFFF


def crc32_hex(s: str) -> str:
    return format(crc32(s), "08x")


# ---------------------------------------------------------------------------
# Mulberry32 PRNG, matches JS implementation exactly
# ---------------------------------------------------------------------------

def mulberry32_factory(seed: int):
    """Returns a callable PRNG matching the JS mulberry32 function."""
    state = [seed & 0xFFFFFFFF]

    def _next() -> float:
        a = state[0]
        a = (a + 0x6D2B79F5) & 0xFFFFFFFF
        state[0] = a
        # JS: let t = Math.imul(a ^ a >>> 15, 1 | a)
        t = (_imul(a ^ (a >> 15), 1 | a)) & 0xFFFFFFFF
        # t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t
        t = (t + _imul(t ^ (t >> 7), 61 | t) ^ t) & 0xFFFFFFFF
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296

    return _next


def _imul(a: int, b: int) -> int:
    """32-bit integer multiplication (matches Math.imul)."""
    a &= 0xFFFFFFFF
    b &= 0xFFFFFFFF
    result = (a * b) & 0xFFFFFFFF
    # Sign-extend to match JS signed 32-bit
    if result >= 0x80000000:
        result -= 0x100000000
    return result


# ---------------------------------------------------------------------------
# Run ID & timestamps
# ---------------------------------------------------------------------------

def create_run_id() -> str:
    ts = format(int(time.time() * 1000), "x")
    rand = format(random.randint(0, 10**9 - 1), "x")
    return f"run_{ts}_{rand}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Clinical JSON serialization (matches JS safeJsonStringify: sorted keys)
# ---------------------------------------------------------------------------

def safe_json_stringify(obj: Any) -> str:
    """Mirrors JS safeJsonStringify, sorted keys, 2-space indent."""
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Clinical record normalization
# ---------------------------------------------------------------------------

VALID_SPECIMEN = {"BLOOD", "URINE", "SWAB", "SALIVA", "PLASMA", "SERUM", "CSF"}
VALID_PRIORITY = {"STAT", "ROUTINE"}
VALID_SEX = {"M", "F", ""}
VALID_STATUS = {"RECEIVED", "IN_PROGRESS", "REPORTED"}


def _safe_upper(s: str) -> str:
    """Uppercase ASCII chars only, matches JS V8 toUpperCase() for non-ASCII like µ."""
    return "".join(c.upper() if c.isascii() else c for c in s)


def _norm_sex(v) -> str:
    t = str(v or "").strip().upper()
    if t in ("M", "MALE"):
        return "M"
    if t in ("F", "FEMALE"):
        return "F"
    return t


def _norm_num_or_blank(v):
    if v is None or v == "":
        return ""
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def _norm_test_count(v):
    if v is None or v == "":
        return 1
    try:
        f = float(v)
    except (TypeError, ValueError):
        return float("nan")
    return int(f) if f == int(f) else f


def normalize_record(r: dict, source: str = "json") -> dict:
    """Normalize a raw dict into the clinical schema."""
    raw_value = r.get("value", None)
    if isinstance(raw_value, (int, float)):
        value = float(raw_value)
    else:
        try:
            value = float(raw_value) if raw_value not in (None, "") else float("nan")
        except (TypeError, ValueError):
            value = float("nan")

    return {
        "id": str(r.get("id", "") or "").strip(),
        "patientId": str(r.get("patientId", "") or "").strip(),
        "specimenType": _safe_upper(str(r.get("specimenType", "") or "").strip()),
        "status": _safe_upper(str(r.get("status", "") or "").strip()),
        "value": value,
        "unit": str(r.get("unit", "") or "").strip(),   # kept as typed; matching ignores case (see known_units.py)
        "collectedAt": str(r.get("collectedAt", "") or "").strip(),
        "accession": _safe_upper(str(r.get("accession", "") or "").strip()) if r.get("accession") is not None else "",
        "source": str(r.get("source", source)).lower(),
        # Efficiency fields
        "test": str(r.get("test", "") or "").strip(),
        "sex": _norm_sex(r.get("sex")),
        "age": _norm_num_or_blank(r.get("age")),
        "ascvdRisk": _safe_upper(str(r.get("ascvdRisk", "") or "").strip()),
        "context": re.sub(r"[\s-]+", "_", _safe_upper(str(r.get("context", "") or "").strip())),
        "result": re.sub(r"[\s-]+", "_", _safe_upper(str(r.get("result", "") or "").strip())),
        "priority": _safe_upper(str(r.get("priority", "") or "").strip()) or "ROUTINE",
        "expectedAt": str(r.get("expectedAt", "") or "").strip(),   # when the sample was expected to arrive
        "receivedAt": str(r.get("receivedAt", "") or "").strip(),   # when the sample actually arrived (required)
        "reportedAt": str(r.get("reportedAt", "") or "").strip(),   # when the result was reported
        "bench": str(r.get("bench", "") or "").strip(),             # lab bench; derived from the test when blank
        "benchReceivedAt": str(r.get("benchReceivedAt", "") or "").strip(),  # blank = same as receivedAt
        "testCount": _norm_test_count(r.get("testCount")),
    }


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_json(text: str) -> dict:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return {"error": f"E010 JSON parse error: {e}"}
    if not isinstance(data, list):
        return {"error": "JSON must be an array of clinical sample records."}
    records = [normalize_record(d, source="json") for d in data]
    return {"records": records}


def parse_xml(text: str) -> dict:
    try:
        root = ET.fromstring(text)
    except XmlParseError as e:
        return {"error": f"E009 XML parse error: {e}"}

    # Support both <samples><sample>... and root IS <samples>
    if root.tag == "samples":
        nodes = list(root.findall("sample"))
    else:
        nodes = list(root.findall(".//sample"))

    if not nodes:
        return {"error": "XML must contain <samples><sample>..</sample></samples>."}

    def g(node, tag):
        el = node.find(tag)
        return el.text if el is not None and el.text else ""

    records = []
    for x in nodes:
        records.append(normalize_record({
            "id": g(x, "id"),
            "patientId": g(x, "patientId"),
            "specimenType": g(x, "specimenType"),
            "status": g(x, "status"),
            "value": g(x, "value"),
            "unit": g(x, "unit"),
            "collectedAt": g(x, "collectedAt"),
            "accession": g(x, "accession"),
            "test": g(x, "test"),
            "sex": g(x, "sex"),
            "age": g(x, "age"),
            "ascvdRisk": g(x, "ascvdRisk"),
            "context": g(x, "context"),
            "result": g(x, "result"),
            "priority": g(x, "priority"),
            "expectedAt": g(x, "expectedAt"),
            "receivedAt": g(x, "receivedAt"),
            "reportedAt": g(x, "reportedAt"),
            "bench": g(x, "bench"),
            "benchReceivedAt": g(x, "benchReceivedAt"),
            "testCount": g(x, "testCount"),
            "source": "xml",
        }))
    return {"records": records}


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_ISO_DATE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?Z$"
)
_ID_RE = re.compile(r"^[A-Za-z0-9\-_]{4,40}$")
_ACC_RE = re.compile(r"^[A-Z0-9\-]{8,30}$")


def is_iso_date(s: str) -> bool:
    return bool(_ISO_DATE_RE.match(s))


def _parse_ts(s: str):
    """ISO string -> milliseconds since epoch, or None if blank/invalid."""
    if not s or not is_iso_date(s):
        return None
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)
    except (ValueError, OverflowError):
        return None


def id_pattern(s: str) -> bool:
    return bool(_ID_RE.match(s))


def accession_pattern(s: str) -> bool:
    return s == "" or bool(_ACC_RE.match(s))


ERROR_BOOK: dict[str, dict] = {
    "E001": {
        "title": "Missing Required Field",
        "fix": [
            "Identify the missing field.",
            "Ensure source systems populate it (LIS/LIMS).",
            "If unavailable, quarantine or enrich upstream.",
            "Re-run intake after correction.",
        ],
    },
    "E002": {
        "title": "Invalid Enumeration",
        "fix": [
            "Compare value to allowed list (specimenType/status/unit).",
            "Correct mapping in source or extend enum after review.",
            "Re-run validation.",
        ],
    },
    "E003": {
        "title": "Out-of-Range Value",
        "fix": [
            "Verify biological thresholds and units.",
            "Check unit conversions.",
            "Correct anomalous values or quarantine.",
        ],
    },
    "E004": {
        "title": "Chronology Violation",
        "fix": [
            "Sort samples by collectedAt.",
            "Ensure timestamps are not in the future.",
            "Re-intake after ordering fixes.",
        ],
    },
    "E005": {
        "title": "Plausibility Failure",
        "fix": [
            "Check clinical rules (e.g., REPORTED must have valid value).",
            "Review specimen/unit pairing.",
            "Fix data or quarantine.",
        ],
    },
    "E006": {
        "title": "Duplicate Detected",
        "fix": [
            "Use id+accession as a natural key.",
            "Deduplicate upstream.",
            "Keep first occurrence, quarantine duplicates.",
        ],
    },
    "E007": {
        "title": "Format Check Failed",
        "fix": [
            "Use ISO-8601 timestamp in collectedAt.",
            "Ensure id/accession match regex.",
            "Correct formatting upstream.",
        ],
    },
    "E009": {
        "title": "XML Parse Error",
        "fix": [
            "Validate XML structure.",
            "Fix closing tags & nesting.",
            "Re-submit.",
        ],
    },
    "E010": {
        "title": "JSON Parse Error",
        "fix": [
            "Validate JSON syntax.",
            "Remove trailing commas.",
            "Re-submit.",
        ],
    },
}


# ---------------------------------------------------------------------------
# Validation engine
# ---------------------------------------------------------------------------

def validate_records(records: list[dict], state: "CLSDWState") -> dict:
    valid = []
    quarantine_bin = []
    seen_key: set[str] = set()

    now_ms = int(time.time() * 1000)
    three_years_ago = now_ms - (1000 * 60 * 60 * 24 * 365 * 3)
    last_ts = -float("inf")

    required_fields = ["id", "patientId", "specimenType", "status", "value", "unit", "collectedAt", "receivedAt"]  # receivedAt = actual arrival

    for idx, r in enumerate(records):
        record_id = r["id"] if r["id"] else f"row_{idx+1}"

        def add_err(code, message, field):
            state.errors.append({"idx": idx, "id": record_id, "code": code, "message": message, "field": field})
            state.metrics["errorByCode"][code] = state.metrics["errorByCode"].get(code, 0) + 1

        # E001: Required fields
        for f in required_fields:
            val = r.get(f)
            missing = (
                val is None
                or val == ""
                or (f == "value" and (isinstance(val, float) and math.isnan(val)))
            )
            if missing:
                add_err("E001", f"Required field '{f}' is missing", f)

        # E002: Enumerations
        if r["specimenType"] and r["specimenType"] not in VALID_SPECIMEN:
            add_err("E002", f"Invalid specimenType '{r['specimenType']}'", "specimenType")
        if r["status"] and r["status"] not in VALID_STATUS:
            add_err("E002", f"Invalid status '{r['status']}'", "status")
        if r["unit"] and not is_known_unit(r["unit"]):
            add_err("E002", f"Invalid unit '{r['unit']}'", "unit")
        if r["priority"] and r["priority"] not in VALID_PRIORITY:
            add_err("E002", f"Invalid priority '{r['priority']}'", "priority")
        if r["sex"] not in VALID_SEX:
            add_err("E002", f"Invalid sex '{r['sex']}'", "sex")
        if r["result"] and r["result"] not in VALID_RESULT:
            add_err("E002", f"Invalid result '{r['result']}'", "result")
        if r["ascvdRisk"] and r["ascvdRisk"] not in ASCVD_RISK_LABEL:
            add_err("E002", f"Invalid ascvdRisk '{r['ascvdRisk']}'", "ascvdRisk")
        if r["context"] and r["context"] not in REFERENCE_CONTEXTS:
            add_err("E002", f"Invalid context '{r['context']}'", "context")
        age = r["age"]
        if age != "":
            if isinstance(age, float) and math.isnan(age):
                add_err("E007", "Invalid age (must be a number)", "age")
            elif age < 0 or age > 120:
                add_err("E003", f"Age out of range: {age}", "age")

        # E003: Value range
        v = r["value"]
        if isinstance(v, float) and not math.isnan(v):
            if v < 0:
                add_err("E003", f"Value cannot be negative: {v}", "value")
            if v > 1_000_000:
                add_err("E003", f"Value out of range: {v}", "value")

        # E005: REPORTED plausibility (mirrors JS condition exactly)
        # JS: if (r.status === 'REPORTED' && !(typeof r.value === 'number') || Number.isNaN(r.value))
        # Due to JS operator precedence this is: (status=REPORTED AND NOT number) OR isNaN
        is_nan_val = isinstance(r["value"], float) and math.isnan(r["value"])
        is_number = isinstance(r["value"], (int, float))
        if (r["status"] == "REPORTED" and not is_number) or is_nan_val:
            add_err("E005", "REPORTED requires a numeric value", "value")

        # REPORTED needs a test name and a reported time (needed for lab time)
        if r["status"] == "REPORTED" and (not r["test"] or not r["reportedAt"]):
            add_err("E005", "REPORTED requires test and reportedAt", "test/reportedAt")

        # testCount: whole number from 1 to 20
        tc = r["testCount"]
        if not (isinstance(tc, int) and 1 <= tc <= 20):
            add_err("E003", "testCount must be a whole number from 1 to 20", "testCount")

        # Efficiency timestamps: must be ISO and in order
        for f in ("expectedAt", "receivedAt", "reportedAt", "benchReceivedAt"):
            if r[f] and not is_iso_date(r[f]):
                add_err("E007", f"Invalid ISO timestamp ({f})", f)
        t_c, t_r, t_p, t_b = (_parse_ts(r[f]) for f in ("collectedAt", "receivedAt", "reportedAt", "benchReceivedAt"))
        if t_r and t_c and t_r < t_c:
            add_err("E004", "receivedAt is before collectedAt", "receivedAt")
        if t_p and t_r and t_p < t_r:
            add_err("E004", "reportedAt is before receivedAt", "reportedAt")
        if t_p and t_p > now_ms:
            add_err("E004", "reportedAt cannot be in the future", "reportedAt")
        if t_b:
            if t_r and t_b < t_r:
                add_err("E004", "benchReceivedAt is before receivedAt", "benchReceivedAt")
            if t_p and t_p < t_b:
                add_err("E004", "reportedAt is before benchReceivedAt", "reportedAt")

        # E007: Format checks
        if r["id"] and not id_pattern(r["id"]):
            add_err("E007", "Invalid id format", "id")
        if r["accession"] and not accession_pattern(r["accession"]):
            add_err("E007", "Invalid accession format", "accession")
        if r["collectedAt"] and not is_iso_date(r["collectedAt"]):
            add_err("E007", "Invalid ISO timestamp (collectedAt)", "collectedAt")

        # E004 + E003: Timestamp checks
        if is_iso_date(r["collectedAt"]):
            try:
                dt = datetime.fromisoformat(r["collectedAt"].replace("Z", "+00:00"))
                t_ms = int(dt.timestamp() * 1000)
                if t_ms > now_ms:
                    add_err("E004", "collectedAt cannot be in the future", "collectedAt")
                if t_ms < three_years_ago:
                    add_err("E003", "collectedAt older than 3 years", "collectedAt")
                if t_ms < last_ts:
                    add_err("E004", "Dataset chronology must be non-decreasing", "collectedAt")
                if t_ms <= now_ms:          # a future-dated row must not break the order check for later rows
                    last_ts = max(last_ts, float(t_ms))
            except (ValueError, OverflowError):
                add_err("E007", "Unparseable collectedAt", "collectedAt")

        # E006: Duplicate check
        dedup_key = f"{r['id']}::{r['accession'] or ''}"
        if dedup_key in seen_key:
            add_err("E006", "Duplicate id+accession", "id/accession")
        else:
            seen_key.add(dedup_key)

        # Per-record audit checksum
        checksum = crc32_hex(safe_json_stringify(r))
        _log_audit(state, "validate:item", "ok", r["id"], f"preCRC={checksum}")

        # Categorise: valid vs quarantine
        had_errors = any(e["id"] == record_id and e["idx"] == idx for e in state.errors)
        if had_errors:
            quarantine_bin.append({"record": r, "reason": "Validation failure", "code": "E005"})
        else:
            valid.append(r)

    return {"valid": valid, "errors": state.errors, "quarantine": quarantine_bin}


# ---------------------------------------------------------------------------
# Automatic results (Normal / Abnormal) and guideline categories
# ---------------------------------------------------------------------------

def classify_records(state: "CLSDWState") -> None:
    """Computed result replaces any typed result for REPORTED records.

    Exception: a typed INCONCLUSIVE is kept (the lab could not determine a result).
    """
    state.result_info = {}
    for r in state.valid_records:
        if r["status"] != "REPORTED":
            continue
        typed = r["result"]
        info = classify_record(r)
        final = "INCONCLUSIVE" if typed == "INCONCLUSIVE" else info["result"]
        r["result"] = final
        entry = REF_INDEX.get(_norm_name(r["test"]))
        dx = diagnose_record(r, entry["name"]) if entry else None
        state.result_info[r["id"]] = {**info, "result": final, "typed": typed, "dx": dx}
        if dx:
            _log_audit(state, "diagnosis", "ok", r["id"], f"{dx['code']} ({dx['source']})")
        changed = bool(typed) and typed != final
        flag = "/" + info["flag"] if info["flag"] else ""
        _log_audit(state, "result", "ok", r["id"],
                   f"computed={info['result']}{flag} range={info['rangeText'] or 'n/a'}"
                   f"{f' (typed {typed} replaced)' if changed else ''}")


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------

def process_records(state: "CLSDWState") -> list[dict]:
    out = []
    for r in state.valid_records:
        processed = {**r, "processedAt": now_iso(), "normalizedValue": float(r["value"]), "category": bench_for(r).upper()}
        checksum = crc32_hex(safe_json_stringify(processed))
        _log_audit(state, "process:item", "ok", r["id"], f"postCRC={checksum}")
        out.append(processed)

    state.processed = out
    _log_audit(state, "process", "-", "-", f"Processed {len(out)} records")
    return out


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

def reconcile_records(state: "CLSDWState", sla_ms: int = 1500) -> list[dict]:
    acks = []
    state.metrics["acked"] = 0
    state.metrics["slaBreaches"] = 0
    state.metrics["ackDelays"] = []

    for r in state.processed:
        seed = crc32(r["id"]) & 0xFFFFFFFF
        rand = mulberry32_factory(seed)
        delay = 100 + int(rand() * 2400)   # 100..2500ms
        ok = rand() > 0.12                  # ~88% success
        ack_code = 200 if ok else 500
        sla = delay <= sla_ms

        state.metrics["ackDelays"].append(delay)
        if ok:
            state.metrics["acked"] += 1
        if not sla:
            state.metrics["slaBreaches"] += 1

        acks.append({"id": r["id"], "ok": ok, "ackCode": ack_code, "delay": delay, "sla": sla})
        _log_audit(
            state, "reconcile:item",
            "ok" if ok else "error",
            r["id"],
            f"ack={ack_code} delay={delay}ms sla={'met' if sla else 'breach'}"
        )

    state.acks = acks
    _log_audit(
        state, "reconcile", "-", "-",
        f"ACKed={state.metrics['acked']} SLA breaches={state.metrics['slaBreaches']}"
    )
    return acks


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

def _log_audit(state: "CLSDWState", stage: str, status: str, record_id: str, notes: str = "") -> None:
    entry = {
        "time": now_iso(),
        "runId": state.run_id,
        "stage": stage,
        "status": status,
        "recordId": record_id,
        "checksum": crc32_hex(f"{state.run_id}|{stage}|{record_id}|{notes}"),
        "notes": notes,
    }
    state.audit.append(entry)


def audit_to_csv(state: "CLSDWState") -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_ALL)
    writer.writerow(["time", "runId", "stage", "status", "recordId", "checksum", "notes"])
    for a in state.audit:
        writer.writerow([
            a["time"], a["runId"], a["stage"], a["status"],
            a["recordId"], a["checksum"], a["notes"].replace("\n", " "),
        ])
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Dataset serialization helpers
# ---------------------------------------------------------------------------

def records_to_xml(records: list[dict]) -> str:
    def esc(s):
        return (str(s or "")
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;"))

    rows = []
    for r in records:
        rows.append(
            f"  <sample>\n"
            f"    <id>{esc(r['id'])}</id>\n"
            f"    <patientId>{esc(r['patientId'])}</patientId>\n"
            f"    <specimenType>{esc(r['specimenType'])}</specimenType>\n"
            f"    <status>{esc(r['status'])}</status>\n"
            f"    <value>{float(r['value'])}</value>\n"
            f"    <unit>{esc(r['unit'])}</unit>\n"
            f"    <collectedAt>{esc(r['collectedAt'])}</collectedAt>\n"
            f"    <accession>{esc(r['accession'])}</accession>\n"
            f"    <test>{esc(r.get('test'))}</test>\n"
            f"    <sex>{esc(r.get('sex'))}</sex>\n"
            f"    <age>{esc(r.get('age'))}</age>\n"
            f"    <ascvdRisk>{esc(r.get('ascvdRisk'))}</ascvdRisk>\n"
            f"    <context>{esc(r.get('context'))}</context>\n"
            f"    <result>{esc(r.get('result'))}</result>\n"
            f"    <priority>{esc(r.get('priority'))}</priority>\n"
            f"    <expectedAt>{esc(r.get('expectedAt'))}</expectedAt>\n"
            f"    <receivedAt>{esc(r.get('receivedAt'))}</receivedAt>\n"
            f"    <reportedAt>{esc(r.get('reportedAt'))}</reportedAt>\n"
            f"    <bench>{esc(r.get('bench'))}</bench>\n"
            f"    <benchReceivedAt>{esc(r.get('benchReceivedAt'))}</benchReceivedAt>\n"
            f"    <testCount>{esc(r.get('testCount'))}</testCount>\n"
            f"  </sample>"
        )
    return "<samples>\n" + "\n".join(rows) + "\n</samples>\n"


# ---------------------------------------------------------------------------
# Efficiency metrics and bench shifts (mirrors app.js)
# ---------------------------------------------------------------------------

HOUR_MS = 3_600_000
EST = timezone(timedelta(hours=-5))          # fixed UTC-5, shown as "EST"
DEFAULT_DEADLINES = {"STAT": 48, "ROUTINE": 168}   # hours; project defaults, editable
SHIFTS = [("Night", 0, 8), ("Morning", 8, 16), ("Afternoon", 16, 24)]   # EST, 8 hours each

BENCH_BY_TEST = {
    "sodium": "Chemistry", "potassium": "Chemistry", "creatinine": "Chemistry", "glucose": "Chemistry",
    "alanine aminotransferase": "Chemistry", "aspartate aminotransferase": "Chemistry", "calcium": "Chemistry",
    "triglycerides": "Chemistry", "ldl cholesterol": "Chemistry", "cholesterol, total": "Chemistry",
    "tsh": "Immunoassay", "ferritin": "Immunoassay", "troponin i": "Immunoassay",
    "hemoglobin a1c": "Hematology", "hemoglobin": "Hematology", "leukocyte count": "Hematology", "platelet count": "Hematology",
}


def bench_for(r: dict) -> str:
    """The record's bench, or one worked out from the test name / specimen type."""
    if r.get("bench"):
        return r["bench"]
    b = BENCH_BY_TEST.get(str(r.get("test", "")).lower())
    if b:
        return b
    sp = r.get("specimenType")
    if sp == "URINE":
        return "Urinalysis"
    if sp == "BLOOD":
        return "Hematology"
    if sp in ("SERUM", "PLASMA"):
        return "Chemistry"
    return "Unassigned"


def _has_ts(s) -> bool:
    return bool(s) and is_iso_date(s)


def hours_between(a: str, b: str) -> float:
    return (_parse_ts(b) - _parse_ts(a)) / HOUR_MS


def wait_hours(r: dict):
    """Lab time: hours from actual arrival to result reported (REPORTED records only)."""
    if r.get("status") == "REPORTED" and _has_ts(r.get("receivedAt")) and _has_ts(r.get("reportedAt")):
        return hours_between(r["receivedAt"], r["reportedAt"])
    return None


def compute_efficiency(records: list[dict], deadlines: Optional[dict] = None) -> dict:
    dl = {**DEFAULT_DEADLINES, **(deadlines or {})}
    waits, delays, breaches, late = [], [], 0, 0
    delay_by_specimen: dict[str, list[float]] = {}
    lab_by_specimen: dict[str, list[float]] = {}
    reported = 0
    first = last = None
    result_counts = {k: 0 for k in VALID_RESULT}
    for r in records:
        if r.get("status") == "REPORTED" and r.get("result") in result_counts:
            result_counts[r["result"]] += 1
        w = wait_hours(r)
        if w is not None:
            waits.append(w)
            lab_by_specimen.setdefault(r["specimenType"], []).append(w)
            if w > dl.get(r["priority"], dl["ROUTINE"]):
                breaches += 1
        if r.get("status") == "REPORTED" and _has_ts(r.get("reportedAt")):
            reported += 1
            start = _parse_ts(r["receivedAt"] if _has_ts(r.get("receivedAt")) else r["collectedAt"])
            end = _parse_ts(r["reportedAt"])
            first = start if first is None else min(first, start)
            last = end if last is None else max(last, end)
        if _has_ts(r.get("expectedAt")) and _has_ts(r.get("receivedAt")):
            d = hours_between(r["expectedAt"], r["receivedAt"])
            delays.append(d)
            if d > 0:
                late += 1
            delay_by_specimen.setdefault(r["specimenType"], []).append(max(0.0, d))
    if reported and first is not None and last > first:
        daily = reported / max(1.0, (last - first) / (24 * HOUR_MS))
    else:
        daily = float(reported)
    avg = lambda a: round(sum(a) / len(a), 1) if a else None
    late_only = [d for d in delays if d > 0]
    return {
        "reported": reported,
        "resultCounts": result_counts,
        "labTimeHoursAvg": avg(waits),
        "slaBreaches": breaches,
        "slaBreachRate": round(100 * breaches / len(waits)) if waits else 0,
        "throughputPerDay": round(daily, 1),
        "onTimeDeliveryRate": round(100 * (len(delays) - late) / len(delays)) if delays else None,
        "avgLatenessOfLateSamplesHours": avg(late_only),
        "deadlinesHours": dl,
        "bySpecimen": {
            k: {"avgLateDeliveryHours": avg(delay_by_specimen.get(k, [])), "avgLabTimeHours": avg(lab_by_specimen.get(k, []))}
            for k in sorted(set(delay_by_specimen) | set(lab_by_specimen))
        },
    }


def _shift_of(iso: str) -> tuple[str, int]:
    """Shift name and end time (ms) for a UTC ISO time, on the EST clock."""
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(EST)
    name, _, end_h = next(s for s in SHIFTS if s[1] <= dt.hour < s[2])
    end = dt.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(hours=end_h)
    return name, int(end.timestamp() * 1000)


def _median(a: list[float]) -> float:
    s = sorted(a)
    n = len(s)
    if not n:
        return 0.0
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def compute_bench_shifts(records: list[dict]) -> list[dict]:
    """Relative efficiency (Raw) vs True efficiency (Fair) for every bench and shift.

    A sample belongs to the shift in which its bench received it.
    Raw  = finished inside the shift / received in the shift (how benches are graded today).
    Fair = same, but leaves out samples that reached the bench with less shift time left than the
           bench normally needs (typical hours per test x number of tests on the sample).
    """
    rows = []
    for r in records:
        start = r["benchReceivedAt"] if _has_ts(r.get("benchReceivedAt")) else r.get("receivedAt")
        if not _has_ts(start):
            continue
        name, end_ms = _shift_of(start)
        rows.append({"r": r, "bench": bench_for(r), "start": start, "shift": name, "end": end_ms})

    per_test: dict[str, list[float]] = {}
    for x in rows:
        r = x["r"]
        if r["status"] == "REPORTED" and _has_ts(r.get("reportedAt")):
            per_test.setdefault(x["bench"], []).append(hours_between(x["start"], r["reportedAt"]) / max(1, r["testCount"]))
    typical = {b: _median(v) for b, v in per_test.items()}

    groups: dict[tuple[str, str], dict] = {}
    for x in rows:
        r = x["r"]
        g = groups.setdefault((x["bench"], x["shift"]), {
            "bench": x["bench"], "shift": x["shift"], "received": 0, "finished": 0, "lateArrivals": 0,
            "eligible": 0, "eligibleDone": 0, "multiTest": 0, "bench_h": [], "handoff_h": [], "late_h": []})
        done = r["status"] == "REPORTED" and _has_ts(r.get("reportedAt")) and _parse_ts(r["reportedAt"]) <= x["end"]
        left = (x["end"] - _parse_ts(x["start"])) / HOUR_MS
        needed = typical.get(x["bench"], 0.0) * max(1, r["testCount"])
        g["received"] += 1
        g["finished"] += int(done)
        if left < needed:
            g["lateArrivals"] += 1
        else:
            g["eligible"] += 1
            g["eligibleDone"] += int(done)
        if r["testCount"] > 1:
            g["multiTest"] += 1
        if r["status"] == "REPORTED" and _has_ts(r.get("reportedAt")):
            g["bench_h"].append(hours_between(x["start"], r["reportedAt"]))
        if _has_ts(r.get("receivedAt")):
            g["handoff_h"].append(hours_between(r["receivedAt"], x["start"]))
        if _has_ts(r.get("expectedAt")) and _has_ts(r.get("receivedAt")):
            g["late_h"].append(max(0.0, hours_between(r["expectedAt"], r["receivedAt"])))

    avg = lambda a: round(sum(a) / len(a), 1) if a else None
    order = {s[0]: i for i, s in enumerate(SHIFTS)}
    out = []
    for g in sorted(groups.values(), key=lambda g: (g["bench"], order[g["shift"]])):
        raw = round(100 * g["finished"] / g["received"]) if g["received"] else 0
        fair = round(100 * g["eligibleDone"] / g["eligible"]) if g["eligible"] else None
        out.append({
            "bench": g["bench"], "shift": g["shift"], "received": g["received"],
            "relativeEfficiencyRaw": raw,
            "lateArrivals": g["lateArrivals"],
            "trueEfficiencyFair": fair,
            "gapPoints": (fair - raw) if fair is not None else None,
            "multiTestSamples": g["multiTest"],
            "avgBenchTimeHours": avg(g["bench_h"]),
            "avgHandoffWaitHours": avg(g["handoff_h"]),
            "avgLateDeliveryHours": avg(g["late_h"]),
        })
    return out


# ---------------------------------------------------------------------------
# Session state container
# ---------------------------------------------------------------------------

class CLSDWState:
    """Holds all mutable state for one CLSDW session / run."""

    def __init__(self):
        self.run_id: str = create_run_id()
        self.raw_input: str = ""
        self.input_format: str = "json"
        self.records: list[dict] = []
        self.valid_records: list[dict] = []
        self.quarantined: list[dict] = []
        self.errors: list[dict] = []
        self.processed: list[dict] = []
        self.acks: list[dict] = []
        self.audit: list[dict] = []
        self.result_info: dict[str, dict] = {}     # record id -> computed result, flag, range, guideline category
        self.metrics: dict = {
            "total": 0,
            "valid": 0,
            "errors": 0,
            "quarantine": 0,
            "acked": 0,
            "slaBreaches": 0,
            "errorByCode": {},
            "ackDelays": [],
        }

    def reset(self):
        self.__init__()

    def to_summary(self) -> dict:
        return {
            "runId": self.run_id,
            "total": self.metrics["total"],
            "valid": self.metrics["valid"],
            "errors": self.metrics["errors"],
            "quarantine": self.metrics["quarantine"],
            "acked": self.metrics["acked"],
            "slaBreaches": self.metrics["slaBreaches"],
            "errorByCode": self.metrics["errorByCode"],
            "ackDelays": self.metrics["ackDelays"],
        }


# ---------------------------------------------------------------------------
# High-level orchestration (mirrors JS intakeAndValidate)
# ---------------------------------------------------------------------------

def intake_and_validate(raw: str, fmt: str, state: CLSDWState) -> dict:
    """Full intake + validation pipeline. Resets state then populates it."""
    state.run_id = create_run_id()
    state.raw_input = raw
    state.input_format = fmt
    state.records = []
    state.valid_records = []
    state.quarantined = []
    state.errors = []
    state.processed = []
    state.acks = []
    state.audit = []
    state.result_info = {}
    state.metrics = {
        "total": 0, "valid": 0, "errors": 0, "quarantine": 0,
        "acked": 0, "slaBreaches": 0, "errorByCode": {}, "ackDelays": [],
    }

    parsed = parse_xml(raw) if fmt == "xml" else parse_json(raw)
    if "error" in parsed:
        _log_audit(state, "intake", "-", "error", parsed["error"])
        state.metrics["errors"] = 1
        return {"ok": False, "message": parsed["error"]}

    state.records = parsed["records"]
    state.metrics["total"] = len(state.records)
    _log_audit(state, "intake", "-", "ok", f"Ingested {len(state.records)} records via {fmt}")

    vres = validate_records(state.records, state)
    state.valid_records = vres["valid"]
    state.errors = vres["errors"]
    state.quarantined = vres["quarantine"]
    state.metrics["valid"] = len(state.valid_records)
    state.metrics["errors"] = len(state.errors)
    state.metrics["quarantine"] = len(state.quarantined)
    classify_records(state)

    _log_audit(
        state, "validate", "-", "ok",
        f"Valid={state.metrics['valid']} Errors={state.metrics['errors']} Quarantine={state.metrics['quarantine']}"
    )

    return {
        "ok": True,
        "message": (
            f"Ingested {len(state.records)} records. "
            f"Valid: {len(state.valid_records)}. "
            f"Errors: {len(state.errors)}. "
            f"Quarantine: {len(state.quarantined)}."
        ),
    }
