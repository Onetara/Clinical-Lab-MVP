"""Reference ranges and automatic results (mirrors reference_ranges.js and classifyRecord in app.js).

Ranges come from the NBME Laboratory Values reference sheet. The data lives in reference_ranges.json
(exported from reference_ranges.js, so the browser app and this backend use the same table).
A reference range says Normal or Abnormal; guideline categories are in diagnostic_criteria.py.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_DATA = json.loads((Path(__file__).parent / "reference_ranges.json").read_text(encoding="utf-8"))
REFERENCE_SOURCE: str = _DATA["source"]
REFERENCE_CONTEXTS: dict[str, str] = _DATA["contexts"]

UNIT_ALIAS = {"CELLS/UL": "/MM3", "CELLS/MM3": "/MM3"}

# On-screen wording: Lab/tech term (Short word)
RESULT_LABEL = {
    "NORMAL": "Normal (In range)",
    "ABNORMAL": "Abnormal (Out of range)",
    "INCONCLUSIVE": "Inconclusive (Unclear)",
    "BORDERLINE": "Borderline (Near limit)",
    "NO_RANGE": "No range (Not listed)",
    "UNIT_MISMATCH": "Unit mismatch (Wrong unit)",
    "SEX_NEEDED": "Sex needed (Missing sex)",
    "CONTEXT_NEEDED": "Context needed (Missing detail)",
}
VALID_RESULT = frozenset(RESULT_LABEL)


def unit_key(u) -> str:
    """Compare units ignoring case, spaces and micro-sign variants; CELLS/uL equals /MM3."""
    k = re.sub(r"[Μμµ]", "U", str(u or "").upper())
    k = re.sub(r"\s+", "", k).replace("³", "3").replace("²", "2")
    return UNIT_ALIAS.get(k, k)


def _norm_name(s) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def _num(x) -> str:
    """Number as JavaScript prints it (136.0 shows as 136)."""
    return str(int(x)) if isinstance(x, float) and x.is_integer() else str(x)


REF_INDEX: dict[str, dict] = {}
KNOWN_UNIT_KEYS: set[str] = set(UNIT_ALIAS)
for _name, _specimen, _aliases, _variants in _DATA["tests"]:
    _entry = {
        "name": _name,
        "specimen": _specimen,
        "variants": [
            {"sex": v[0], "ctx": v[1], "unit": v[2], "lo": v[3], "hi": v[4],
             "siUnit": v[5], "siLo": v[6], "siHi": v[7], "flags": (v[8] if len(v) > 8 else None) or {}}
            for v in _variants
        ],
    }
    for _n in [_name, *_aliases]:
        REF_INDEX[_norm_name(_n)] = _entry
    for _v in _entry["variants"]:
        KNOWN_UNIT_KEYS.add(unit_key(_v["unit"]))
        KNOWN_UNIT_KEYS.add(unit_key(_v["siUnit"]))


def is_known_unit(u) -> bool:
    return unit_key(u) in KNOWN_UNIT_KEYS


def canonical_test(test) -> str | None:
    """Name used in the reference table for a typed test name or alias, or None."""
    e = REF_INDEX.get(_norm_name(test))
    return e["name"] if e else None


def _range_text(lo, hi, unit, f) -> str:
    if lo is not None and hi is not None:
        return f"{_num(lo)}–{_num(hi)} {unit}"
    if hi is not None:
        return f"{'<' if f.get('hx') else '≤'}{_num(hi)} {unit}"
    return f"{'>' if f.get('lx') else '≥'}{_num(lo)} {unit}"


def classify_record(r: dict) -> dict:
    """Result, flag and range text for one record, using the reference table."""
    entry = REF_INDEX.get(_norm_name(r.get("test")))
    if not entry:
        return {"result": "NO_RANGE", "flag": "", "rangeText": ""}

    cands = entry["variants"]
    if any(v["sex"] for v in cands):
        if not r.get("sex"):
            return {"result": "SEX_NEEDED", "flag": "", "rangeText": ""}
        cands = [v for v in cands if not v["sex"] or v["sex"] == r["sex"]]
    if len(cands) > 1:
        cands = [v for v in cands if v["ctx"] == r.get("context", "")]
        if not cands:
            return {"result": "CONTEXT_NEEDED", "flag": "", "rangeText": ""}
    v = cands[0]
    k = unit_key(r.get("unit"))
    if k == unit_key(v["unit"]):
        lo, hi, unit, bl = v["lo"], v["hi"], v["unit"], v["flags"].get("bl")
    elif k == unit_key(v["siUnit"]):
        lo, hi, unit, bl = v["siLo"], v["siHi"], v["siUnit"], v["flags"].get("siBl")
    else:
        return {"result": "UNIT_MISMATCH", "flag": "", "rangeText": ""}

    f = v["flags"]
    text = _range_text(lo, hi, unit, f) + (f" ({REFERENCE_CONTEXTS.get(v['ctx'], v['ctx'])})" if v["ctx"] else "")
    val = r["value"]
    low = lo is not None and (val <= lo if f.get("lx") else val < lo)
    high = hi is not None and (val >= hi if f.get("hx") else val > hi)
    if not low and not high:
        return {"result": "NORMAL", "flag": "", "rangeText": text}
    if high and bl and bl[0] <= val <= bl[1]:
        return {"result": "BORDERLINE", "flag": "HIGH", "rangeText": text}
    return {"result": "ABNORMAL", "flag": "LOW" if low else "HIGH", "rangeText": text}
