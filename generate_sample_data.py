"""Generate simulated clinical lab records for the CLSDW dashboard.

Run from this folder:   python3 generate_sample_data.py
Writes (next to this script):
  sample_data_large.json   the records
  sample_data_large.js     same records, loaded by index.html (the "Load Large Sample" button)

Fixed random seed: the same data every run. All data is synthetic.
Times are generated in EST (UTC-5) and stored as UTC ("Z") timestamps.
"""
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

SEED = 2026
N_VALID = 285          # plus invalid records below -> about 300 total
N_INVALID = 15
EST = timezone(timedelta(hours=-5))
START = datetime(2026, 11, 1, 0, 0, tzinfo=EST)          # first collection
END = datetime(2026, 11, 14, 12, 0, tzinfo=EST)          # last collection
NOW = datetime(2026, 11, 14, 21, 0, tzinfo=EST)          # "now" for the simulation

rng = random.Random(SEED)

# test, specimen, unit, low, high, weight, sex_specific(dict or None), context
TESTS = [
    ("Sodium", "SERUM", "mEq/L", 136, 146, 6, None, ""),
    ("Potassium", "SERUM", "mEq/L", 3.5, 5.0, 6, None, ""),
    ("Creatinine", "SERUM", "mg/dL", 0.6, 1.2, 6, None, ""),
    ("Glucose", "SERUM", "mg/dL", 70, 100, 6, None, "FASTING"),
    ("Glucose", "SERUM", "mg/dL", 70, 139, 3, None, "RANDOM"),
    ("Hemoglobin A1c", "BLOOD", "%", 4.5, 5.6, 6, None, ""),
    ("Hemoglobin", "BLOOD", "g/dL", 0, 0, 7, {"M": (13.5, 17.5), "F": (12.0, 16.0)}, ""),
    ("Alanine aminotransferase", "SERUM", "U/L", 10, 40, 5, None, ""),
    ("Aspartate aminotransferase", "SERUM", "U/L", 12, 38, 4, None, ""),
    ("TSH", "SERUM", "µIU/mL", 0.4, 4.0, 4, None, ""),
    ("Ferritin", "SERUM", "ng/mL", 0, 0, 4, {"M": (20, 250), "F": (10, 120)}, ""),
    ("Leukocyte count", "BLOOD", "CELLS/µL", 4500, 11000, 5, None, ""),
    ("Platelet count", "BLOOD", "CELLS/µL", 150000, 400000, 4, None, ""),
    ("Troponin I", "SERUM", "ng/mL", 0.005, 0.04, 3, None, ""),
    ("Triglycerides", "SERUM", "mg/dL", 50, 149, 4, None, ""),
    ("LDL cholesterol", "SERUM", "mg/dL", 60, 159, 5, None, ""),
    ("Cholesterol, total", "SERUM", "mg/dL", 140, 199, 3, None, ""),
    ("Calcium", "SERUM", "mg/dL", 8.4, 10.2, 3, None, ""),
    ("Calcium, urine", "URINE", "mg/24h", 100, 300, 3, None, ""),
    ("Osmolality, urine", "URINE", "mOsmol/kg", 50, 1200, 3, None, ""),
    ("Proteins, total, urine", "URINE", "mg/24h", 20, 149, 2, None, ""),
]
WEIGHTS = [t[5] for t in TESTS]

LATE_PROB = {"BLOOD": 0.20, "SERUM": 0.25, "PLASMA": 0.30, "URINE": 0.45}
RISKS = ["LOW", "BORDERLINE", "INTERMEDIATE", "HIGH"]


def to_utc(dt):
    dt = dt.replace(second=0, microsecond=0)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_value(test, lo, hi, sex_map, sex, context):
    if sex_map:
        lo, hi = sex_map[sex]
    span = hi - lo
    abnormal = rng.random() < 0.25
    if not abnormal:
        v = rng.uniform(lo, hi)
    else:
        if test in ("Triglycerides", "Cholesterol, total", "LDL cholesterol", "Troponin I", "Hemoglobin A1c") or rng.random() < 0.5:
            v = hi + rng.uniform(0.02, 0.8) * span            # high
        else:
            v = max(lo * 0.5, lo - rng.uniform(0.05, 0.5) * span)  # low
    if test == "Glucose" and context == "FASTING" and abnormal:
        v = rng.uniform(101, 190)
    if test == "Hemoglobin A1c" and abnormal:
        v = rng.uniform(5.7, 9.5)
    decimals = 0 if hi >= 200 else (2 if hi < 1 else 1)
    return round(v, decimals) if decimals else int(round(v))


def lateness_hours(specimen):
    if rng.random() < LATE_PROB[specimen]:
        r = rng.random()
        if r < 0.72:
            return rng.uniform(0.25, 3.0)
        if r < 0.93:
            return rng.uniform(3.0, 8.0)
        return rng.uniform(8.0, 14.0)
    return -rng.uniform(0.0, 0.4)           # on time or slightly early


BENCH = {
    "Sodium": "Chemistry", "Potassium": "Chemistry", "Creatinine": "Chemistry", "Glucose": "Chemistry",
    "Alanine aminotransferase": "Chemistry", "Aspartate aminotransferase": "Chemistry", "Calcium": "Chemistry",
    "Triglycerides": "Chemistry", "LDL cholesterol": "Chemistry", "Cholesterol, total": "Chemistry",
    "TSH": "Immunoassay", "Ferritin": "Immunoassay", "Troponin I": "Immunoassay",
    "Hemoglobin A1c": "Hematology", "Hemoglobin": "Hematology", "Leukocyte count": "Hematology", "Platelet count": "Hematology",
}


def bench_hours(priority, n_tests):
    """Hours the bench needs once it has the sample (more tests = more time)."""
    per_test = rng.gauss(1.8, 0.7) if priority == "STAT" else rng.gauss(3.5, 1.8)
    return max(0.5, per_test) * n_tests


def build_valid(i):
    test, specimen, unit, lo, hi, _w, sex_map, ctx = rng.choices(TESTS, weights=WEIGHTS)[0]
    sex = rng.choice(["M", "F"])
    age = rng.randint(18, 88)
    value = make_value(test, lo, hi, sex_map, sex, ctx)
    priority = "STAT" if rng.random() < 0.25 else "ROUTINE"

    collected = START + timedelta(minutes=rng.randint(0, int((END - START).total_seconds() // 60)))
    expected = collected + timedelta(hours=2)
    received = expected + timedelta(hours=lateness_hours(specimen))
    if received < collected:
        received = collected + timedelta(minutes=20)
    n_tests = rng.choices([1, 2, 3], weights=[70, 22, 8])[0]
    # one sample, several tests: it visits another bench first, so this bench gets it 2-3 h later
    handoff = rng.uniform(2.0, 3.0) if n_tests > 1 else rng.uniform(0.0, 0.5)
    bench_received = received + timedelta(hours=handoff)
    reported = bench_received + timedelta(hours=bench_hours(priority, n_tests))
    stuck = rng.random()
    if priority == "STAT" and stuck < 0.03:
        reported = received + timedelta(hours=rng.uniform(49, 62))      # missed the 48 h deadline
    elif priority == "ROUTINE" and stuck < 0.02:
        reported = received + timedelta(hours=rng.uniform(170, 195))    # missed the 7 day deadline

    unit_out = unit
    if test == "Creatinine" and rng.random() < 0.03:
        unit_out, value = "mmol/L", round(value * 88.4 / 1000, 2)      # wrong unit on purpose

    if reported <= NOW:
        status, reported_at = "REPORTED", to_utc(reported)
    elif received <= NOW - timedelta(hours=3):
        status, reported_at = "IN_PROGRESS", ""
    else:
        status, reported_at = "RECEIVED", ""

    typed = ""
    if status == "REPORTED":
        r = rng.random()
        if r < 0.03:
            typed = "INCONCLUSIVE"
        elif r < 0.15:
            typed = rng.choice(["NORMAL", "ABNORMAL"])         # may disagree; the app replaces it

    return {
        "id": "",
        "patientId": f"PAT-{rng.randint(1000, 1999)}",
        "sex": sex,
        "age": age,
        "ascvdRisk": rng.choice(RISKS) if test == "LDL cholesterol" else "",
        "specimenType": specimen,
        "test": test,
        "context": ctx,
        "status": status,
        "value": value,
        "unit": unit_out,
        "collectedAt": to_utc(collected),
        "accession": "",
        "result": typed,
        "priority": priority,
        "expectedAt": to_utc(expected),
        "receivedAt": to_utc(received),
        "reportedAt": reported_at,
        "bench": "Urinalysis" if specimen == "URINE" else BENCH.get(test, ""),
        "benchReceivedAt": to_utc(bench_received),
        "testCount": n_tests,
        "_t": collected,
    }


def main():
    records = [build_valid(i) for i in range(N_VALID)]
    # Invalid records, spread through the timeline (same fields, one thing wrong each)
    kinds = ["bad_id", "negative", "bad_unit", "no_test", "reported_before_received", "duplicate"]
    for n in range(N_INVALID):
        base = build_valid(n)
        base["status"] = "REPORTED"
        base["reportedAt"] = to_utc(base["_t"] + timedelta(hours=30))
        base["receivedAt"] = to_utc(base["_t"] + timedelta(hours=2, minutes=30))
        base["benchReceivedAt"] = base["receivedAt"]
        kind = kinds[n % len(kinds)]
        base["_bad"] = kind
        records.append(base)

    records.sort(key=lambda r: r["_t"])
    seen = []
    for i, r in enumerate(records, 1):
        r["id"] = f"SMP-{3000 + i}"
        r["accession"] = f"ACC-2026-{5000 + i}"
        kind = r.pop("_bad", None)
        if kind == "bad_id":
            r["id"] = "bad id!"
        elif kind == "negative":
            r["value"] = -abs(r["value"])
        elif kind == "bad_unit":
            r["unit"] = "XYZ"
        elif kind == "no_test":
            r["test"] = ""
        elif kind == "reported_before_received":
            r["reportedAt"] = to_utc(r["_t"] + timedelta(minutes=30))
        elif kind == "duplicate" and seen:
            r["id"], r["accession"] = seen[rng.randrange(len(seen))]
        if kind != "bad_id":
            seen.append((r["id"], r["accession"]))
        r.pop("_t")

    out_dir = Path(__file__).parent
    (out_dir / "sample_data_large.json").write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = ",\n".join("  " + json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in records)
    (out_dir / "sample_data_large.js").write_text(
        "// Generated by generate_sample_data.py (seed %d). Synthetic data.\n"
        "const LARGE_SAMPLE_TEXT = `[\n%s\n]`;\n" % (SEED, lines), encoding="utf-8")
    print(f"wrote {len(records)} records")


if __name__ == "__main__":
    main()
