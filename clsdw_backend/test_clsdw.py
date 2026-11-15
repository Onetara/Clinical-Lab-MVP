"""
test_clsdw.py — CLSDW Backend Test Suite
Tests core logic, API endpoints, edge cases, and parity with JS behavior.

Run with:  python -m pytest test_clsdw.py -v
"""

import json
import math
import pytest
from fastapi.testclient import TestClient

from clsdw_core import (
    crc32,
    crc32_hex,
    mulberry32_factory,
    normalize_record,
    parse_json,
    parse_xml,
    intake_and_validate,
    validate_records,
    process_records,
    reconcile_records,
    audit_to_csv,
    records_to_xml,
    safe_json_stringify,
    CLSDWState,
    id_pattern,
    accession_pattern,
    is_iso_date,
    VALID_SPECIMEN,
    VALID_STATUS,
    ERROR_BOOK,
)
from main import app

client = TestClient(app)

# ─────────────────────────────────────────────
# Example data (matches JS constants)
# ─────────────────────────────────────────────

VALID_JSON = json.dumps([
    {"id": "SMP-1001", "patientId": "PAT-001", "specimenType": "BLOOD",
     "status": "RECEIVED", "value": 85.2, "unit": "MG/DL",
     "collectedAt": "2026-01-15T09:00:00Z", "receivedAt": "2026-01-15T09:30:00Z", "accession": "ACC-2026-0001"},
    {"id": "SMP-1002", "patientId": "PAT-002", "specimenType": "URINE",
     "status": "IN_PROGRESS", "value": 6.5, "unit": "MMOL/L",
     "collectedAt": "2026-01-15T09:05:00Z", "receivedAt": "2026-01-15T09:35:00Z", "accession": "ACC-2026-0002"},
    {"id": "SMP-1003", "patientId": "PAT-003", "specimenType": "SWAB",
     "status": "REPORTED", "value": 1, "unit": "IU/L",
     "test": "Alanine aminotransferase", "reportedAt": "2026-01-15T14:10:00Z",
     "collectedAt": "2026-01-15T09:10:00Z", "receivedAt": "2026-01-15T09:40:00Z", "accession": "ACC-2026-0003"},
])

INVALID_JSON = json.dumps([
    {"id": "bad id!", "patientId": "PAT-011", "specimenType": "PLASMA",
     "status": "REPORTED", "value": -3, "unit": "MG/DL",
     "collectedAt": "2026-01-15T09:42:00Z", "receivedAt": "2026-01-15T10:12:00Z", "accession": "TOO-LONG-REF-123456-OVER"},
    {"id": "SMP-1002", "patientId": "PAT-002", "specimenType": "URINE",
     "status": "RECEIVED", "value": 1.1, "unit": "XYZ",
     "collectedAt": "2026-01-15T09:45:00Z", "receivedAt": "2026-01-15T10:15:00Z", "accession": "ACC-2026-0002"},
])

VALID_XML = """<samples>
  <sample>
    <id>SMP-2001</id>
    <patientId>PAT-021</patientId>
    <specimenType>BLOOD</specimenType>
    <status>RECEIVED</status>
    <value>92.4</value>
    <unit>MG/DL</unit>
    <collectedAt>2026-01-16T10:00:00Z</collectedAt>
    <receivedAt>2026-01-16T10:30:00Z</receivedAt>
    <accession>ACC-2026-0101</accession>
  </sample>
  <sample>
    <id>SMP-2002</id>
    <patientId>PAT-022</patientId>
    <specimenType>URINE</specimenType>
    <status>IN_PROGRESS</status>
    <value>4.2</value>
    <unit>MMOL/L</unit>
    <collectedAt>2026-01-16T10:05:00Z</collectedAt>
    <receivedAt>2026-01-16T10:35:00Z</receivedAt>
    <accession>ACC-2026-0102</accession>
  </sample>
</samples>"""

MIXED_JSON = json.dumps([
    {"id": "SMP-1001", "patientId": "PAT-001", "specimenType": "BLOOD",
     "status": "RECEIVED", "value": 85.2, "unit": "MG/DL",
     "collectedAt": "2026-01-15T09:00:00Z", "receivedAt": "2026-01-15T09:30:00Z", "accession": "ACC-2026-0001"},
    {"id": "bad id!", "patientId": "PAT-011", "specimenType": "PLASMA",
     "status": "REPORTED", "value": -3, "unit": "MG/DL",
     "collectedAt": "2026-01-15T09:42:00Z", "receivedAt": "2026-01-15T10:12:00Z", "accession": "TOO-LONG-REF-123456-OVER"},
    # duplicate id+accession
    {"id": "SMP-1001", "patientId": "PAT-001", "specimenType": "BLOOD",
     "status": "RECEIVED", "value": 85.2, "unit": "MG/DL",
     "collectedAt": "2026-01-15T09:50:00Z", "receivedAt": "2026-01-15T10:20:00Z", "accession": "ACC-2026-0001"},
])

# ═════════════════════════════════════════════
# 1. CRC32 Tests
# ═════════════════════════════════════════════

class TestCRC32:
    def test_empty_string(self):
        assert crc32("") == 0x00000000

    def test_known_values(self):
        # Standard CRC32 test vectors
        assert crc32("123456789") == 0xCBF43926

    def test_hex_format(self):
        h = crc32_hex("test")
        assert len(h) == 8
        assert all(c in "0123456789abcdef" for c in h)

    def test_deterministic(self):
        assert crc32("hello") == crc32("hello")

    def test_different_inputs_differ(self):
        assert crc32("abc") != crc32("xyz")

    def test_unicode_handling(self):
        # Should not raise
        result = crc32("CELLS/µL")
        assert isinstance(result, int)
        assert result >= 0

    def test_padding_to_8_chars(self):
        # Even small checksums pad to 8
        h = crc32_hex("")
        assert len(h) == 8


# ═════════════════════════════════════════════
# 2. Mulberry32 PRNG Tests
# ═════════════════════════════════════════════

class TestMultberry32:
    def test_deterministic_same_seed(self):
        r1 = mulberry32_factory(42)
        r2 = mulberry32_factory(42)
        assert r1() == r2()
        assert r1() == r2()

    def test_different_seeds_differ(self):
        r1 = mulberry32_factory(1)
        r2 = mulberry32_factory(2)
        assert r1() != r2()

    def test_output_range(self):
        r = mulberry32_factory(999)
        for _ in range(100):
            v = r()
            assert 0.0 <= v < 1.0

    def test_reconciliation_determinism(self):
        """Same record id must always produce the same delay/ok."""
        from clsdw_core import crc32
        seed = crc32("SMP-1001") & 0xFFFFFFFF
        r1 = mulberry32_factory(seed)
        r2 = mulberry32_factory(seed)
        delay1 = 100 + int(r1() * 2400)
        ok1 = r1() > 0.12
        delay2 = 100 + int(r2() * 2400)
        ok2 = r2() > 0.12
        assert delay1 == delay2
        assert ok1 == ok2


# ═════════════════════════════════════════════
# 3. Normalize Record Tests
# ═════════════════════════════════════════════

class TestNormalizeRecord:
    def test_basic_normalization(self):
        r = normalize_record({"id": " SMP-1 ", "specimenType": "blood", "value": 10})
        assert r["id"] == "SMP-1"
        assert r["specimenType"] == "BLOOD"
        assert r["value"] == 10.0

    def test_missing_fields_default_empty(self):
        r = normalize_record({})
        assert r["id"] == ""
        assert r["patientId"] == ""
        assert r["accession"] == ""

    def test_nan_value_for_invalid_string(self):
        r = normalize_record({"value": "not_a_number"})
        assert math.isnan(r["value"])

    def test_source_preserved(self):
        r = normalize_record({"source": "xml"}, source="json")
        assert r["source"] == "xml"

    def test_accession_none_becomes_empty(self):
        r = normalize_record({"accession": None})
        assert r["accession"] == ""

    def test_status_uppercased(self):
        r = normalize_record({"status": "in_progress"})
        assert r["status"] == "IN_PROGRESS"


# ═════════════════════════════════════════════
# 4. Parsing Tests
# ═════════════════════════════════════════════

class TestParsing:
    def test_parse_valid_json(self):
        result = parse_json(VALID_JSON)
        assert "records" in result
        assert len(result["records"]) == 3
        assert result["records"][0]["id"] == "SMP-1001"

    def test_parse_json_not_array(self):
        result = parse_json('{"id": "SMP-1"}')
        assert "error" in result
        assert "array" in result["error"].lower()

    def test_parse_json_malformed(self):
        result = parse_json("{broken json")
        assert "error" in result
        assert "E010" in result["error"]

    def test_parse_valid_xml(self):
        result = parse_xml(VALID_XML)
        assert "records" in result
        assert len(result["records"]) == 2
        assert result["records"][0]["id"] == "SMP-2001"

    def test_parse_xml_no_samples(self):
        result = parse_xml("<root><item/></root>")
        assert "error" in result

    def test_parse_xml_malformed(self):
        result = parse_xml("<broken>")
        assert "error" in result
        assert "E009" in result["error"]

    def test_xml_source_tagged(self):
        result = parse_xml(VALID_XML)
        assert result["records"][0]["source"] == "xml"

    def test_json_source_tagged(self):
        result = parse_json(VALID_JSON)
        assert result["records"][0]["source"] == "json"


# ═════════════════════════════════════════════
# 5. Validation Tests
# ═════════════════════════════════════════════

class TestValidation:
    def _run(self, raw, fmt="json"):
        state = CLSDWState()
        result = intake_and_validate(raw, fmt, state)
        return state, result

    def test_all_valid_records(self):
        state, res = self._run(VALID_JSON)
        assert res["ok"]
        assert state.metrics["valid"] == 3
        assert state.metrics["errors"] == 0

    def test_invalid_records_quarantined(self):
        state, res = self._run(INVALID_JSON)
        assert res["ok"]   # intake OK, but records have errors
        assert state.metrics["quarantine"] > 0

    def test_e001_missing_field(self):
        data = json.dumps([{"id": "SMP-9001", "patientId": "PAT-1",
                             "specimenType": "BLOOD", "status": "RECEIVED",
                             "value": 10, "unit": "MG/DL",
                             "collectedAt": ""}])   # missing collectedAt
        state, _ = self._run(data)
        codes = [e["code"] for e in state.errors]
        assert "E001" in codes

    def test_e002_invalid_specimen(self):
        data = json.dumps([{"id": "SMP-9002", "patientId": "PAT-2",
                             "specimenType": "TISSUE", "status": "RECEIVED",
                             "value": 5, "unit": "MG/DL",
                             "collectedAt": "2026-01-15T09:00:00Z", "receivedAt": "2026-01-15T09:30:00Z",
                             "accession": "ACC-2026-9002"}])
        state, _ = self._run(data)
        codes = [e["code"] for e in state.errors]
        assert "E002" in codes

    def test_e003_negative_value(self):
        data = json.dumps([{"id": "SMP-9003", "patientId": "PAT-3",
                             "specimenType": "BLOOD", "status": "RECEIVED",
                             "value": -5, "unit": "MG/DL",
                             "collectedAt": "2026-01-15T09:00:00Z", "receivedAt": "2026-01-15T09:30:00Z",
                             "accession": "ACC-2026-9003"}])
        state, _ = self._run(data)
        codes = [e["code"] for e in state.errors]
        assert "E003" in codes

    def test_e004_future_timestamp(self):
        data = json.dumps([{"id": "SMP-9004", "patientId": "PAT-4",
                             "specimenType": "BLOOD", "status": "RECEIVED",
                             "value": 10, "unit": "MG/DL",
                             "collectedAt": "2099-01-01T00:00:00Z", "receivedAt": "2099-01-01T00:30:00Z",
                             "accession": "ACC-2026-9004"}])
        state, _ = self._run(data)
        codes = [e["code"] for e in state.errors]
        assert "E004" in codes

    def test_e006_duplicate(self):
        data = json.dumps([
            {"id": "SMP-DUP", "patientId": "PAT-5", "specimenType": "BLOOD",
             "status": "RECEIVED", "value": 10, "unit": "MG/DL",
             "collectedAt": "2026-01-15T09:00:00Z", "receivedAt": "2026-01-15T09:30:00Z", "accession": "ACC-2026-DUP0"},
            {"id": "SMP-DUP", "patientId": "PAT-5", "specimenType": "BLOOD",
             "status": "RECEIVED", "value": 10, "unit": "MG/DL",
             "collectedAt": "2026-01-15T09:05:00Z", "receivedAt": "2026-01-15T09:35:00Z", "accession": "ACC-2026-DUP0"},
        ])
        state, _ = self._run(data)
        codes = [e["code"] for e in state.errors]
        assert "E006" in codes

    def test_e007_bad_id_format(self):
        data = json.dumps([{"id": "bad id!", "patientId": "PAT-6",
                             "specimenType": "BLOOD", "status": "RECEIVED",
                             "value": 10, "unit": "MG/DL",
                             "collectedAt": "2026-01-15T09:00:00Z", "receivedAt": "2026-01-15T09:30:00Z",
                             "accession": "ACC-2026-9006"}])
        state, _ = self._run(data)
        codes = [e["code"] for e in state.errors]
        assert "E007" in codes

    def test_xml_intake(self):
        state, res = self._run(VALID_XML, fmt="xml")
        assert res["ok"]
        assert state.metrics["total"] == 2
        assert state.metrics["valid"] == 2

    def test_json_parse_error_returns_not_ok(self):
        _, res = self._run("{bad json}")
        assert not res["ok"]

    def test_xml_parse_error_returns_not_ok(self):
        _, res = self._run("<broken xml>", fmt="xml")
        assert not res["ok"]

    def test_empty_json_array(self):
        state, res = self._run("[]")
        assert res["ok"]
        assert state.metrics["total"] == 0
        assert state.metrics["valid"] == 0

    def test_value_exactly_at_boundary_1e6(self):
        data = json.dumps([{"id": "SMP-9010", "patientId": "PAT-10",
                             "specimenType": "BLOOD", "status": "RECEIVED",
                             "value": 1_000_001, "unit": "MG/DL",
                             "collectedAt": "2026-01-15T09:00:00Z", "receivedAt": "2026-01-15T09:30:00Z",
                             "accession": "ACC-2026-9010"}])
        state, _ = self._run(data)
        codes = [e["code"] for e in state.errors]
        assert "E003" in codes

    def test_error_by_code_tracking(self):
        state, _ = self._run(MIXED_JSON)
        assert "errorByCode" in state.metrics
        assert isinstance(state.metrics["errorByCode"], dict)


# ═════════════════════════════════════════════
# 6. Processing Tests
# ═════════════════════════════════════════════

class TestProcessing:
    def _intake(self, raw=None, fmt="json"):
        state = CLSDWState()
        intake_and_validate(raw or VALID_JSON, fmt, state)
        return state

    def test_process_all_valid(self):
        state = self._intake()
        out = process_records(state)
        assert len(out) == len(state.valid_records)

    def test_category_chemistry_from_test_name(self):
        data = json.dumps([{"id": "SMP-C001", "patientId": "PAT-C1",
                             "specimenType": "BLOOD", "status": "RECEIVED",
                             "value": 90, "unit": "mg/dL", "test": "Glucose",
                             "collectedAt": "2026-01-15T09:00:00Z", "receivedAt": "2026-01-15T09:30:00Z",
                             "accession": "ACC-2026-C001"}])
        out = process_records(self._intake(data))
        assert out[0]["category"] == "CHEMISTRY"      # bench worked out from the test name

    def test_category_uses_bench_when_given(self):
        data = json.dumps([{"id": "SMP-M001", "patientId": "PAT-M1",
                             "specimenType": "SWAB", "status": "RECEIVED",
                             "value": 1, "unit": "IU/L", "bench": "Microbiology",
                             "collectedAt": "2026-01-15T09:00:00Z", "receivedAt": "2026-01-15T09:30:00Z",
                             "accession": "ACC-2026-M001"}])
        out = process_records(self._intake(data))
        assert out[0]["category"] == "MICROBIOLOGY"

    def test_category_hematology(self):
        data = json.dumps([{"id": "SMP-H001", "patientId": "PAT-H1",
                             "specimenType": "BLOOD", "status": "RECEIVED",
                             "value": 5000, "unit": "CELLS/µL",
                             "collectedAt": "2026-01-15T09:00:00Z", "receivedAt": "2026-01-15T09:30:00Z",
                             "accession": "ACC-2026-H001"}])
        state = self._intake(data)
        out = process_records(state)
        assert out[0]["category"] == "HEMATOLOGY"      # BLOOD specimen, no test name: falls back to the specimen type

    def test_processedAt_set(self):
        state = self._intake()
        out = process_records(state)
        assert all("processedAt" in r for r in out)

    def test_normalized_value_is_float(self):
        state = self._intake()
        out = process_records(state)
        assert all(isinstance(r["normalizedValue"], float) for r in out)

    def test_audit_entries_added(self):
        state = self._intake()
        before = len(state.audit)
        process_records(state)
        assert len(state.audit) > before

    def test_no_valid_records_returns_empty(self):
        state = CLSDWState()
        intake_and_validate(json.dumps([]), "json", state)
        out = process_records(state)
        assert out == []


# ═════════════════════════════════════════════
# 7. Reconciliation Tests
# ═════════════════════════════════════════════

class TestReconciliation:
    def _full_pipeline(self, raw=None):
        state = CLSDWState()
        intake_and_validate(raw or VALID_JSON, "json", state)
        process_records(state)
        return state

    def test_returns_ack_for_each_processed(self):
        state = self._full_pipeline()
        acks = reconcile_records(state)
        assert len(acks) == len(state.processed)

    def test_deterministic_results(self):
        state1 = self._full_pipeline()
        state2 = self._full_pipeline()
        acks1 = reconcile_records(state1, 1500)
        acks2 = reconcile_records(state2, 1500)
        assert [a["delay"] for a in acks1] == [a["delay"] for a in acks2]
        assert [a["ok"] for a in acks1] == [a["ok"] for a in acks2]

    def test_ack_codes_200_or_500(self):
        state = self._full_pipeline()
        acks = reconcile_records(state)
        for a in acks:
            assert a["ackCode"] in (200, 500)

    def test_sla_flag_consistency(self):
        sla = 1500
        state = self._full_pipeline()
        acks = reconcile_records(state, sla_ms=sla)
        for a in acks:
            assert a["sla"] == (a["delay"] <= sla)

    def test_sla_metrics_counted(self):
        state = self._full_pipeline()
        reconcile_records(state, sla_ms=200)  # very tight — lots of breaches
        assert state.metrics["slaBreaches"] >= 0
        assert state.metrics["acked"] >= 0

    def test_delays_in_range(self):
        state = self._full_pipeline()
        acks = reconcile_records(state)
        for a in acks:
            assert 100 <= a["delay"] <= 2500

    def test_no_processed_gives_empty(self):
        state = CLSDWState()
        intake_and_validate(json.dumps([]), "json", state)
        acks = reconcile_records(state)
        assert acks == []


# ═════════════════════════════════════════════
# 8. Audit Tests
# ═════════════════════════════════════════════

class TestAudit:
    def test_audit_populated_after_intake(self):
        state = CLSDWState()
        intake_and_validate(VALID_JSON, "json", state)
        assert len(state.audit) > 0

    def test_audit_fields_present(self):
        state = CLSDWState()
        intake_and_validate(VALID_JSON, "json", state)
        required = {"time", "runId", "stage", "status", "recordId", "checksum", "notes"}
        for entry in state.audit:
            assert required.issubset(entry.keys()), f"Missing keys in {entry}"

    def test_checksum_is_8_hex_chars(self):
        state = CLSDWState()
        intake_and_validate(VALID_JSON, "json", state)
        for entry in state.audit:
            assert len(entry["checksum"]) == 8
            assert all(c in "0123456789abcdef" for c in entry["checksum"])

    def test_csv_export_has_header(self):
        state = CLSDWState()
        intake_and_validate(VALID_JSON, "json", state)
        csv_text = audit_to_csv(state)
        first_line = csv_text.split("\n")[0]
        assert "time" in first_line
        assert "runId" in first_line
        assert "stage" in first_line

    def test_csv_row_count(self):
        state = CLSDWState()
        intake_and_validate(VALID_JSON, "json", state)
        csv_text = audit_to_csv(state)
        lines = [l for l in csv_text.split("\n") if l.strip()]
        assert len(lines) == len(state.audit) + 1  # +1 for header

    def test_run_id_in_all_entries(self):
        state = CLSDWState()
        intake_and_validate(VALID_JSON, "json", state)
        for entry in state.audit:
            assert entry["runId"] == state.run_id


# ═════════════════════════════════════════════
# 9. Dataset serialization tests
# ═════════════════════════════════════════════

class TestSerialization:
    def test_records_to_xml_round_trip(self):
        state = CLSDWState()
        intake_and_validate(VALID_JSON, "json", state)
        xml = records_to_xml(state.records)
        assert "<samples>" in xml
        assert "<sample>" in xml
        assert "SMP-1001" in xml

    def test_safe_json_stringify_sorted_keys(self):
        obj = {"z": 1, "a": 2, "m": 3}
        s = safe_json_stringify(obj)
        parsed = json.loads(s)
        assert list(parsed.keys()) == sorted(parsed.keys())

    def test_xml_escapeing(self):
        state = CLSDWState()
        intake_and_validate(VALID_JSON, "json", state)
        xml = records_to_xml(state.records)
        # Should not contain bare < or & in values
        # (field values are all safe for these test records, but structure should be valid)
        from xml.etree import ElementTree as ET
        ET.fromstring(xml)  # should not raise


# ═════════════════════════════════════════════
# 10. Pattern / regex helper tests
# ═════════════════════════════════════════════

class TestPatterns:
    def test_valid_ids(self):
        for id_ in ["SMP-1001", "PAT001", "A1B2-C3D4", "ABCD"]:
            assert id_pattern(id_), f"Expected valid: {id_}"

    def test_invalid_ids(self):
        for id_ in ["bad id!", "ab", "a" * 41, ""]:
            assert not id_pattern(id_), f"Expected invalid: {id_}"

    def test_valid_accessions(self):
        for acc in ["", "ACC-2026-0001", "ACC-2026-ABCD"]:
            assert accession_pattern(acc), f"Expected valid: {acc}"

    def test_invalid_accessions(self):
        # TOO-LONG: must exceed 30 chars; lowercase: fails [A-Z0-9\-]; short: <8 chars
        for acc in ["TOO-LONG-REF-12345678901234-OVER-THE-TOP", "lower-acc-12345678", "SHORT"]:
            assert not accession_pattern(acc), f"Expected invalid: {acc}"

    def test_valid_iso_dates(self):
        for d in ["2026-01-15T09:00:00Z", "2026-01-16T10:00:00Z"]:
            assert is_iso_date(d)

    def test_invalid_iso_dates(self):
        for d in ["", "2026-01-15", "not-a-date", "2026-01-15T09:00:00+05:00"]:
            assert not is_iso_date(d), f"Expected invalid: {d}"


# ═════════════════════════════════════════════
# 11. Error book completeness
# ═════════════════════════════════════════════

class TestErrorBook:
    def test_all_expected_codes_present(self):
        expected = {"E001", "E002", "E003", "E004", "E005", "E006", "E007", "E009", "E010"}
        assert expected.issubset(ERROR_BOOK.keys())

    def test_each_code_has_title_and_fix(self):
        for code, meta in ERROR_BOOK.items():
            assert "title" in meta, f"{code} missing title"
            assert "fix" in meta, f"{code} missing fix"
            assert isinstance(meta["fix"], list), f"{code} fix should be list"
            assert len(meta["fix"]) > 0, f"{code} fix list empty"


# ═════════════════════════════════════════════
# 12. FastAPI Endpoint Tests
# ═════════════════════════════════════════════

class TestAPIHealth:
    def test_health(self):
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"


class TestAPIIntake:
    def test_intake_valid_json(self):
        r = client.post("/intake", json={"format": "json", "data": VALID_JSON})
        assert r.status_code == 200
        data = r.json()
        assert data["ok"]
        assert "runId" in data
        assert data["summary"]["total"] == 3

    def test_intake_valid_xml(self):
        r = client.post("/intake", json={"format": "xml", "data": VALID_XML})
        assert r.status_code == 200
        data = r.json()
        assert data["ok"]
        assert data["summary"]["total"] == 2

    def test_intake_bad_format(self):
        r = client.post("/intake", json={"format": "csv", "data": "something"})
        assert r.status_code == 422

    def test_intake_malformed_json(self):
        r = client.post("/intake", json={"format": "json", "data": "not json"})
        assert r.status_code == 200
        data = r.json()
        assert not data["ok"]

    def test_intake_empty_array(self):
        r = client.post("/intake", json={"format": "json", "data": "[]"})
        assert r.status_code == 200
        data = r.json()
        assert data["ok"]
        assert data["summary"]["total"] == 0

    def test_intake_mixed_dataset(self):
        r = client.post("/intake", json={"format": "json", "data": MIXED_JSON})
        assert r.status_code == 200
        data = r.json()
        assert data["ok"]
        assert data["summary"]["errors"] > 0
        assert data["summary"]["quarantine"] > 0


class TestAPIValidate:
    def _intake(self, data=None):
        r = client.post("/intake", json={"format": "json", "data": data or VALID_JSON})
        return r.json()["runId"]

    def test_validate_returns_results(self):
        run_id = self._intake()
        r = client.get(f"/validate/{run_id}")
        assert r.status_code == 200
        data = r.json()
        assert "metrics" in data
        assert "errors" in data
        assert "quarantined" in data

    def test_validate_unknown_run(self):
        r = client.get("/validate/nonexistent_run_id_xyz")
        assert r.status_code == 404

    def test_validate_correct_counts(self):
        run_id = self._intake(VALID_JSON)
        r = client.get(f"/validate/{run_id}")
        data = r.json()
        assert data["metrics"]["total"] == 3
        assert data["metrics"]["valid"] == 3
        assert data["metrics"]["errors"] == 0


class TestAPIProcess:
    def _intake(self, data=None):
        r = client.post("/intake", json={"format": "json", "data": data or VALID_JSON})
        return r.json()["runId"]

    def test_process_valid(self):
        run_id = self._intake()
        r = client.post(f"/process/{run_id}")
        assert r.status_code == 200
        data = r.json()
        assert data["processedCount"] == 3

    def test_process_unknown_run(self):
        r = client.post("/process/nonexistent")
        assert r.status_code == 404

    def test_process_no_valid_records(self):
        run_id = self._intake("[]")
        r = client.post(f"/process/{run_id}")
        assert r.status_code == 400

    def test_process_result_has_category(self):
        run_id = self._intake()
        r = client.post(f"/process/{run_id}")
        data = r.json()
        for rec in data["records"]:
            assert "category" in rec


class TestAPIReconcile:
    def _get_processed_run(self):
        r = client.post("/intake", json={"format": "json", "data": VALID_JSON})
        run_id = r.json()["runId"]
        client.post(f"/process/{run_id}")
        return run_id

    def test_reconcile_basic(self):
        run_id = self._get_processed_run()
        r = client.post(f"/reconcile/{run_id}")
        assert r.status_code == 200
        data = r.json()
        assert "acked" in data
        assert "slaBreaches" in data
        assert len(data["acks"]) == 3

    def test_reconcile_before_process(self):
        r = client.post("/intake", json={"format": "json", "data": VALID_JSON})
        run_id = r.json()["runId"]
        r2 = client.post(f"/reconcile/{run_id}")
        assert r2.status_code == 400

    def test_reconcile_custom_sla(self):
        run_id = self._get_processed_run()
        r = client.post(f"/reconcile/{run_id}?sla_ms=200")
        assert r.status_code == 200
        data = r.json()
        assert data["slaMs"] == 200

    def test_reconcile_deterministic(self):
        # Two separate runs with identical data should yield same delays
        r1 = client.post("/intake", json={"format": "json", "data": VALID_JSON})
        run1 = r1.json()["runId"]
        client.post(f"/process/{run1}")
        acks1 = client.post(f"/reconcile/{run1}").json()["acks"]

        r2 = client.post("/intake", json={"format": "json", "data": VALID_JSON})
        run2 = r2.json()["runId"]
        client.post(f"/process/{run2}")
        acks2 = client.post(f"/reconcile/{run2}").json()["acks"]

        delays1 = [a["delay"] for a in acks1]
        delays2 = [a["delay"] for a in acks2]
        assert delays1 == delays2


class TestAPIAudit:
    def _intake(self):
        r = client.post("/intake", json={"format": "json", "data": VALID_JSON})
        return r.json()["runId"]

    def test_audit_json(self):
        run_id = self._intake()
        r = client.get(f"/audit/{run_id}")
        assert r.status_code == 200
        data = r.json()
        assert "audit" in data
        assert len(data["audit"]) > 0

    def test_audit_csv_download(self):
        run_id = self._intake()
        r = client.get(f"/audit/{run_id}/csv")
        assert r.status_code == 200
        assert "text/csv" in r.headers["content-type"]
        assert "time" in r.text


class TestAPIDashboard:
    def test_dashboard_after_intake(self):
        r = client.post("/intake", json={"format": "json", "data": VALID_JSON})
        run_id = r.json()["runId"]
        r2 = client.get(f"/dashboard/{run_id}")
        assert r2.status_code == 200
        data = r2.json()
        assert "errorRate" in data
        assert "throughputPerDay" in data
        assert data["throughputPerDay"] == 1.0      # one REPORTED record in the example data


class TestAPIPlaybooks:
    def test_playbooks_endpoint(self):
        r = client.get("/playbooks")
        assert r.status_code == 200
        data = r.json()
        assert "E001" in data
        assert "E010" in data


class TestAPINewEntry:
    def _intake(self):
        r = client.post("/intake", json={"format": "json", "data": VALID_JSON})
        return r.json()["runId"]

    def test_add_valid_entry(self):
        run_id = self._intake()
        entry = {
            "id": "SMP-NEW1", "patientId": "PAT-NEW",
            "specimenType": "BLOOD", "status": "RECEIVED",
            "value": 75.0, "unit": "MG/DL",
            "collectedAt": "2026-01-15T10:00:00Z", "receivedAt": "2026-01-15T10:30:00Z",
            "accession": "ACC-2026-1001"
        }
        r = client.post(f"/new_entry/{run_id}", json=entry)
        assert r.status_code == 200
        data = r.json()
        assert data["ok"]
        assert data["summary"]["total"] == 4  # 3 original + 1 new

    def test_add_invalid_entry_flagged(self):
        run_id = self._intake()
        entry = {
            "id": "bad id!", "patientId": "PAT-BAD",
            "specimenType": "BLOOD", "status": "RECEIVED",
            "value": -10.0, "unit": "MG/DL",
            "collectedAt": "2099-01-01T00:00:00Z", "receivedAt": "2099-01-01T00:30:00Z",
            "accession": "ACC-2026-BAD0"
        }
        r = client.post(f"/new_entry/{run_id}", json=entry)
        assert r.status_code == 200
        data = r.json()
        # Intake OK but with validation errors flagged
        assert data["summary"]["errors"] > 0


class TestAPIDataset:
    def _intake(self):
        r = client.post("/intake", json={"format": "json", "data": VALID_JSON})
        return r.json()["runId"]

    def test_get_dataset_json(self):
        run_id = self._intake()
        r = client.get(f"/dataset/{run_id}")
        assert r.status_code == 200
        data = r.json()
        assert "records" in data
        assert len(data["records"]) == 3

    def test_get_dataset_xml(self):
        run_id = self._intake()
        r = client.get(f"/dataset/{run_id}/xml")
        assert r.status_code == 200
        assert "application/xml" in r.headers["content-type"]
        assert "<samples>" in r.text


class TestAPIFullPipeline:
    def test_pipeline_json(self):
        r = client.post("/pipeline", json={"format": "json", "data": VALID_JSON})
        assert r.status_code == 200
        data = r.json()
        assert data["ok"]
        assert "runId" in data
        assert "validation" in data
        assert "processing" in data
        assert "reconciliation" in data
        assert "dashboard" in data

    def test_pipeline_xml(self):
        r = client.post("/pipeline?sla_ms=800", json={"format": "xml", "data": VALID_XML})
        assert r.status_code == 200
        data = r.json()
        assert data["ok"]

    def test_pipeline_bad_data(self):
        r = client.post("/pipeline", json={"format": "json", "data": "{bad}"})
        assert r.status_code == 200
        data = r.json()
        assert not data["ok"]
        assert data["stage"] == "intake"


# ═════════════════════════════════════════════
# 13. Integration / end-to-end tests
# ═════════════════════════════════════════════

class TestEndToEnd:
    def test_full_workflow_json(self):
        """Complete JSON workflow: intake → validate → process → reconcile → audit → dashboard."""
        # Step 1: Intake
        r = client.post("/intake", json={"format": "json", "data": VALID_JSON})
        assert r.status_code == 200
        run_id = r.json()["runId"]

        # Step 2: Validate
        r = client.get(f"/validate/{run_id}")
        assert r.status_code == 200
        v = r.json()
        assert v["metrics"]["valid"] == 3

        # Step 3: Process
        r = client.post(f"/process/{run_id}")
        assert r.status_code == 200
        assert r.json()["processedCount"] == 3

        # Step 4: Reconcile
        r = client.post(f"/reconcile/{run_id}?sla_ms=1500")
        assert r.status_code == 200
        rec = r.json()
        assert len(rec["acks"]) == 3

        # Step 5: Audit
        r = client.get(f"/audit/{run_id}")
        assert r.status_code == 200
        audit_entries = r.json()["audit"]
        assert len(audit_entries) > 0

        # Step 6: Dashboard
        r = client.get(f"/dashboard/{run_id}")
        assert r.status_code == 200
        dash = r.json()
        assert dash["throughputPerDay"] == 1.0

    def test_full_workflow_xml(self):
        """Complete XML workflow."""
        r = client.post("/intake", json={"format": "xml", "data": VALID_XML})
        run_id = r.json()["runId"]

        client.post(f"/process/{run_id}")
        r = client.post(f"/reconcile/{run_id}")
        assert r.status_code == 200
        assert len(r.json()["acks"]) == 2

    def test_run_ids_are_unique(self):
        """Multiple intakes must produce unique run IDs."""
        ids = set()
        for _ in range(10):
            r = client.post("/intake", json={"format": "json", "data": VALID_JSON})
            ids.add(r.json()["runId"])
        assert len(ids) == 10

    def test_csv_audit_downloadable(self):
        r = client.post("/intake", json={"format": "json", "data": VALID_JSON})
        run_id = r.json()["runId"]
        client.post(f"/process/{run_id}")
        client.post(f"/reconcile/{run_id}")
        r = client.get(f"/audit/{run_id}/csv")
        assert r.status_code == 200
        lines = [l for l in r.text.split("\n") if l.strip()]
        assert len(lines) > 1  # header + data rows

    def test_large_dataset(self):
        """50 valid records should all pass through."""
        records = []
        for i in range(50):
            records.append({
                "id": f"SMP-{5000+i:04d}",
                "patientId": f"PAT-{i+1:03d}",
                "specimenType": "BLOOD",
                "status": "RECEIVED",
                "value": 80 + i * 0.1,
                "unit": "MG/DL",
                "collectedAt": f"2026-02-01T{9 + i // 60:02d}:{i % 60:02d}:00Z",
                "receivedAt": f"2026-02-01T{10 + i // 60:02d}:{i % 60:02d}:00Z",
                "accession": f"ACC-2026-{5000+i:04d}",
            })
        r = client.post("/intake", json={"format": "json", "data": json.dumps(records)})
        assert r.status_code == 200
        data = r.json()
        assert data["ok"]
        assert data["summary"]["total"] == 50
        assert data["summary"]["valid"] == 50


# ─────────────────────────────────────────────
# Efficiency: arrival time, lab time, bench shifts
# ─────────────────────────────────────────────

from clsdw_core import compute_efficiency, compute_bench_shifts, bench_for, is_known_unit


def _rec(i, **kw):
    base = {"id": f"SMP-E{i:03d}", "patientId": f"PAT-E{i}", "specimenType": "SERUM", "status": "REPORTED",
            "value": 140, "unit": "mEq/L", "test": "Sodium", "priority": "ROUTINE",
            "collectedAt": "2026-11-01T01:00:00Z", "expectedAt": "2026-11-01T03:00:00Z",
            "receivedAt": "2026-11-01T03:00:00Z", "reportedAt": "2026-11-01T06:00:00Z",
            "accession": f"ACC-2026-E{i:03d}"}
    base.update(kw)
    return normalize_record(base)


class TestArrivalAndFields:
    def test_receivedAt_required(self):
        data = json.dumps([{"id": "SMP-R001", "patientId": "PAT-R1", "specimenType": "SERUM", "status": "RECEIVED",
                            "value": 140, "unit": "mEq/L", "collectedAt": "2026-01-15T09:00:00Z"}])
        state = CLSDWState()
        intake_and_validate(data, "json", state)
        assert any(e["code"] == "E001" and e["field"] == "receivedAt" for e in state.errors)

    def test_reported_needs_test_and_reportedAt(self):
        r = _rec(1, test="", reportedAt="")
        state = CLSDWState()
        intake_and_validate(json.dumps([r]), "json", state)
        assert any(e["code"] == "E005" for e in state.errors)

    def test_bench_received_before_arrival_is_error(self):
        r = _rec(2, benchReceivedAt="2026-11-01T02:00:00Z")
        state = CLSDWState()
        intake_and_validate(json.dumps([r]), "json", state)
        assert any(e["code"] == "E004" and e["field"] == "benchReceivedAt" for e in state.errors)

    def test_unit_matching_ignores_case_and_micro_sign(self):
        assert is_known_unit("mg/dL") and is_known_unit("MG/DL")
        assert is_known_unit("cells/\u00b5L") and is_known_unit("CELLS/\u03bcL")
        assert not is_known_unit("XYZ")

    def test_defaults(self):
        r = _rec(3)
        assert r["testCount"] == 1 and r["benchReceivedAt"] == "" and r["bench"] == ""

    def test_bench_from_test_and_specimen(self):
        assert bench_for(_rec(4, test="Hemoglobin A1c")) == "Hematology"
        assert bench_for(_rec(5, test="", specimenType="URINE")) == "Urinalysis"
        assert bench_for(_rec(6, bench="Blood Bank")) == "Blood Bank"


class TestEfficiencyMetrics:
    def test_lab_time_starts_at_arrival(self):
        eff = compute_efficiency([_rec(1)])
        assert eff["labTimeHoursAvg"] == 3.0          # 0300 to 0600, not from collection

    def test_delivery_lateness(self):
        late = _rec(2, receivedAt="2026-11-01T05:00:00Z", reportedAt="2026-11-01T08:00:00Z")
        eff = compute_efficiency([_rec(1), late])
        assert eff["onTimeDeliveryRate"] == 50
        assert eff["avgLatenessOfLateSamplesHours"] == 2.0

    def test_deadline_breach_uses_priority(self):
        stat = _rec(1, priority="STAT", reportedAt="2026-11-03T04:00:00Z")      # 49 h after arrival
        routine = _rec(2, reportedAt="2026-11-03T04:00:00Z")
        eff = compute_efficiency([stat, routine])
        assert eff["slaBreaches"] == 1

    def test_api_dashboard_and_bench_endpoints(self):
        data = json.dumps([_rec(1, benchReceivedAt="2026-11-01T03:10:00Z")])
        run_id = client.post("/intake", json={"format": "json", "data": data}).json()["runId"]
        dash = client.get(f"/dashboard/{run_id}").json()
        assert dash["labTimeHoursAvg"] == 3.0
        bench = client.get(f"/bench/{run_id}").json()
        assert bench["benchShifts"][0]["bench"] == "Chemistry"


class TestBenchShifts:
    """Shifts on the EST clock (UTC-5): Night 0000-0800, Morning 0800-1600, Afternoon 1600-0000."""

    def _batch(self):
        # EST night shift = 05:00Z to 13:00Z. Bench needs about 2 h per test.
        recs = []
        for i in range(6):          # six normal samples arrive early in the shift, finished in 2 h
            recs.append(_rec(i, receivedAt=f"2026-11-01T0{5 + i % 2}:00:00Z",
                             reportedAt=f"2026-11-01T0{7 + i % 2}:00:00Z"))
        # one sample reaches the bench with 1 h left, so it cannot finish in the shift
        recs.append(_rec(20, receivedAt="2026-11-01T12:00:00Z", reportedAt="2026-11-01T14:00:00Z"))
        return recs

    def test_raw_vs_fair(self):
        rows = compute_bench_shifts(self._batch())
        night = next(r for r in rows if r["bench"] == "Chemistry" and r["shift"] == "Night")
        assert night["received"] == 7
        assert night["relativeEfficiencyRaw"] == 86       # 6 of 7 finished
        assert night["lateArrivals"] == 1
        assert night["trueEfficiencyFair"] == 100         # late arrival left out
        assert night["gapPoints"] == 14

    def test_more_tests_need_more_time(self):
        # 2 tests need about 4 h; reaching the bench with 3 h left is a late arrival
        recs = self._batch()
        recs.append(_rec(30, testCount=2, receivedAt="2026-11-01T10:00:00Z", reportedAt="2026-11-01T14:00:00Z"))
        night = next(r for r in compute_bench_shifts(recs) if r["shift"] == "Night")
        assert night["lateArrivals"] == 2
        assert night["multiTestSamples"] == 1

    def test_handoff_wait_uses_bench_received(self):
        r = _rec(40, testCount=2, benchReceivedAt="2026-11-01T05:30:00Z")
        r["receivedAt"] = "2026-11-01T03:00:00Z"
        rows = compute_bench_shifts([r])
        assert rows[0]["avgHandoffWaitHours"] == 2.5

    def test_shift_by_bench_receipt_not_arrival(self):
        # arrives 1850 EST (Afternoon) but the bench gets it 0010 EST next day (Night)
        r = _rec(41, receivedAt="2026-11-01T23:50:00Z", benchReceivedAt="2026-11-02T05:10:00Z",
                 reportedAt="2026-11-02T07:00:00Z")
        assert compute_bench_shifts([r])[0]["shift"] == "Night"


# ─────────────────────────────────────────────
# Automatic results (NBME reference ranges) and guideline categories
# ─────────────────────────────────────────────

from reference_ranges import classify_record, canonical_test, unit_key
from diagnostic_criteria import diagnose_record


def _lab(test, value, unit, **kw):
    r = {"test": test, "value": float(value), "unit": unit, "sex": "", "context": "", "age": "", "ascvdRisk": ""}
    r.update(kw)
    return r


class TestAutomaticResults:
    def test_normal_and_abnormal(self):
        assert classify_record(_lab("Sodium", 140, "mEq/L"))["result"] == "NORMAL"
        high = classify_record(_lab("Sodium", 150, "mEq/L"))
        assert (high["result"], high["flag"]) == ("ABNORMAL", "HIGH")
        low = classify_record(_lab("Sodium", 120, "mEq/L"))
        assert (low["result"], low["flag"]) == ("ABNORMAL", "LOW")

    def test_borderline_total_cholesterol(self):
        assert classify_record(_lab("Cholesterol, total", 220, "mg/dL"))["result"] == "BORDERLINE"
        assert classify_record(_lab("Cholesterol, total", 250, "mg/dL"))["result"] == "ABNORMAL"

    def test_si_units_accepted(self):
        assert classify_record(_lab("Glucose", 5.0, "mmol/L", context="FASTING"))["result"] == "NORMAL"

    def test_sex_specific_range_needs_sex(self):
        assert classify_record(_lab("Hemoglobin", 14, "g/dL"))["result"] == "SEX_NEEDED"
        assert classify_record(_lab("Hemoglobin", 14, "g/dL", sex="F"))["result"] == "NORMAL"
        assert classify_record(_lab("Hemoglobin", 11, "g/dL", sex="F"))["result"] == "ABNORMAL"

    def test_context_needed(self):
        assert classify_record(_lab("Glucose", 90, "mg/dL"))["result"] == "CONTEXT_NEEDED"

    def test_unit_mismatch_and_no_range(self):
        assert classify_record(_lab("Sodium", 140, "mg/dL"))["result"] == "UNIT_MISMATCH"
        assert classify_record(_lab("Not a test", 1, "mg/dL"))["result"] == "NO_RANGE"

    def test_alias_and_case(self):
        assert canonical_test("  glucose ") == "Glucose"
        assert unit_key("CELLS/\u00b5L") == unit_key("cells/mm3") == "/MM3"

    def test_computed_result_replaces_typed(self):
        data = json.dumps([_rec(1, value=150, result="NORMAL")])      # typed NORMAL, but 150 is high
        state = CLSDWState()
        intake_and_validate(data, "json", state)
        assert state.valid_records[0]["result"] == "ABNORMAL"
        assert state.result_info["SMP-E001"]["typed"] == "NORMAL"

    def test_typed_inconclusive_is_kept(self):
        state = CLSDWState()
        intake_and_validate(json.dumps([_rec(1, result="INCONCLUSIVE")]), "json", state)
        assert state.valid_records[0]["result"] == "INCONCLUSIVE"

    def test_results_endpoint(self):
        data = json.dumps([_rec(1, value=150, result="NORMAL")])
        run_id = client.post("/intake", json={"format": "json", "data": data}).json()["runId"]
        res = client.get(f"/results/{run_id}").json()
        assert res["count"] == 1
        row = res["results"][0]
        assert row["result"] == "ABNORMAL" and row["flag"] == "HIGH" and row["typedResultReplaced"] is True
        dash = client.get(f"/dashboard/{run_id}").json()
        assert dash["resultCounts"]["ABNORMAL"] == 1

    def test_invalid_result_context_risk_age(self):
        recs = [_rec(i, **kw) for i, kw in enumerate([{"result": "MAYBE"}, {"context": "LUNCH"},
                                                      {"ascvdRisk": "EXTREME"}, {"age": 150}], start=1)]
        state = CLSDWState()
        intake_and_validate(json.dumps(recs), "json", state)
        assert state.metrics["valid"] == 0
        codes = {e["code"] for e in state.errors}
        assert {"E002", "E003"} <= codes


class TestGuidelineCategories:
    def test_glucose_fasting_ada(self):
        assert diagnose_record(_lab("Glucose", 130, "mg/dL", context="FASTING"), "Glucose")["code"] == "DIABETES_RANGE"
        assert diagnose_record(_lab("Glucose", 110, "mg/dL", context="FASTING"), "Glucose")["code"] == "PREDIABETES"
        assert diagnose_record(_lab("Glucose", 90, "mg/dL", context="FASTING"), "Glucose")["code"] == "BELOW_DIABETES"

    def test_glucose_random(self):
        assert diagnose_record(_lab("Glucose", 210, "mg/dL", context="RANDOM"), "Glucose")["code"] == "RANDOM_HIGH"
        assert diagnose_record(_lab("Glucose", 120, "mg/dL", context="RANDOM"), "Glucose")["code"] == "RANDOM_NOT_DX"

    def test_a1c_both_units(self):
        assert diagnose_record(_lab("Hemoglobin A1c", 6.5, "%"), "Hemoglobin A1c")["code"] == "DIABETES_RANGE"
        assert diagnose_record(_lab("Hemoglobin A1c", 40, "mmol/mol"), "Hemoglobin A1c")["code"] == "PREDIABETES"

    def test_anemia_who_by_sex_age_pregnancy(self):
        f = lambda v, **kw: diagnose_record(_lab("Hemoglobin", v, "g/dL", **kw), "Hemoglobin")["code"]
        assert f(12.5, sex="F", age=30) == "ANEMIA_NONE"
        assert f(11.5, sex="F", age=30) == "ANEMIA_MILD"
        assert f(12.5, sex="M", age=30) == "ANEMIA_MILD"      # men need 13.0
        assert f(10.5, sex="F", age=30, context="PREGNANT_T1") == "ANEMIA_MILD"
        assert f(7.5, sex="F", age=30) == "ANEMIA_SEVERE"
        assert f(13, sex="F", age="") == "AGE_NEEDED"
        assert f(13, sex="F", age=10) == "AGE_NOT_COVERED"

    def test_ldl_goal_by_risk(self):
        f = lambda v, risk: diagnose_record(_lab("LDL cholesterol", v, "mg/dL", ascvdRisk=risk), "LDL cholesterol")["code"]
        assert f(90, "") == "RISK_NEEDED"
        assert f(150, "LOW") == "LDL_NO_GOAL"
        assert f(90, "INTERMEDIATE") == "LDL_AT_GOAL"
        assert f(80, "HIGH") == "LDL_ABOVE_GOAL"       # high risk goal is under 70

    def test_no_guideline_for_other_tests(self):
        assert diagnose_record(_lab("Sodium", 140, "mEq/L"), "Sodium") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
