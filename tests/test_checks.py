"""checks.run_checks against the answer keys of the sample corpus."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from carthagents.checks import run_checks
from carthagents.model import Extraction, Record, YearResult

CORPUS = Path(__file__).resolve().parents[1] / "data" / "A3-Hackathon-Corpus"
KEYS = sorted(CORPUS.glob("f*/*.key.json"))
pytestmark = pytest.mark.skipif(not KEYS, reason="corpus not available")
KIND = {"carry_over": "other"}


def _label(r: dict) -> str:
    if r.get("asset_no"):
        return f"{r['asset_no']}/{r.get('sub_no')}"
    return f"{r['kind']} {r.get('name') or r.get('class') or r.get('account') or r.get('position') or ''}"


def build(key: dict, origin: str = "text_layer") -> Extraction:
    """Answer key -> Extraction (one Record per key amount; cut-off fields omitted)."""
    recs = []
    for i, r in enumerate(key["rows"]):
        kind = KIND.get(r["kind"], r["kind"])
        if kind == "asset" and r.get("level") in ("class", "account", "position"):
            kind = r["level"]
        fields = dict(r["fields"], **(r.get("net_fields") or {}))
        for f, c in fields.items():
            if r.get("flags", {}).get(f) == "cut_off":
                continue
            ptr = dict(r["pointer"], origin=origin)
            recs.append(Record(r["year"], "T", _label(r), kind, f, c, r.get("printed", {}).get(f, str(c)),
                               ptr, "ok", row_id=i))
    unit = 1000 if key["meta"]["unit"] == "TEUR" else 1
    return Extraction(key["meta"]["doc_id"], key["meta"]["format"],
                      [YearResult(y, None) for y in key["meta"]["years"]], recs, unit_scale=unit)


def load(doc: str) -> dict:
    return json.loads(next(CORPUS.glob(f"*/{doc}.key.json")).read_text(encoding="utf-8"))


def failed(ex: Extraction) -> list[Record]:
    return [r for r in ex.records if r.checks]


def test_perfect_docs_no_false_flags():
    for p in KEYS:
        key = json.loads(p.read_text(encoding="utf-8"))
        if key["meta"]["variant"] != "perfect":
            continue
        ex = build(key)
        run_checks(ex)
        assert not ex.notes, ex.notes
        assert not failed(ex), (p.name, [(r.row_label, r.field, r.checks) for r in failed(ex)][:5])
        assert all(r.status == "ok" for r in ex.records)


def test_planted_non_tying_detected():
    n = 0
    for p in KEYS:
        key = json.loads(p.read_text(encoding="utf-8"))
        for pl in key.get("planted", []):
            if pl["type"] != "non_tying_subtotal":
                continue
            ex = build(key)
            run_checks(ex)
            for w in pl["where"]:
                hit = [r for r in ex.records if r.field == w["field"] and r.checks
                       and {k: v for k, v in r.pointer.items() if k != "origin"} == w["row"]]
                assert hit and all(r.status == "ok" for r in hit), (p.name, w)
                n += 1
    assert n > 0


def _parse(s: str) -> int:
    s = s.strip().replace(".", "").replace(",", ".").rstrip("-").lstrip("-")
    return round(float(s or 0) * 100)


def test_transfer_breaks_cost_law_printed_zero():
    key = load("f09_excel_register_05")
    ex = build(key)
    for r in ex.records:                           # feed what is printed, not the implied key value
        r.cents = _parse(r.raw_text) * (-1 if r.field == "umbuchung" and r.raw_text.startswith("-") else 1)
    run_checks(ex)
    cell = key["planted"][0]["where"][0]["row"]["cell"]
    rec = next(r for r in ex.records if r.pointer["cell"] == cell and r.field == "umbuchung")
    assert "law_ahk" in rec.checks
    if _parse(rec.raw_text) == 0 and not rec.raw_text.strip():
        assert rec.status == "blank_flagged" and rec.flag.startswith("implied_zero")
    else:                                          # a printed '0' is a printed digit: kept and flagged
        assert rec.status == "ok"
    assert any(op.where.endswith("ahk_close") for op in ex.years[0].open_points)


def test_ocr_perfect_verifies_and_corruption_blanks():
    key = load("f00_demo_01")
    ex = build(key, "ocr")
    run_checks(ex)
    assert all(r.status == "ok" for r in ex.records)
    ex = build(key, "ocr")
    bad = next(r for r in ex.records if r.row_kind == "asset" and r.field == "ahk_close")
    bad.cents += 1
    run_checks(ex)
    assert bad.status == "blank_flagged" and bad.flag.startswith("ocr_unverified")
    blanked = [r for r in ex.records if r.status == "blank_flagged"]
    assert all(r.row_id == bad.row_id or r.checks for r in blanked)


@pytest.mark.parametrize("doc", ["f05_sap_class_gitter_06", "f07_sap_class_summary_06"])
def test_teur_no_rounding_flags(doc):
    key = load(doc)
    ex = build(key)
    run_checks(ex)
    planted = {(json.dumps(w["row"], sort_keys=True), w["field"]) for pl in key.get("planted", [])
               if pl["type"] == "non_tying_subtotal" for w in pl["where"]}
    bad_rows = {json.dumps(w["row"], sort_keys=True) for pl in key.get("planted", [])
                if pl["type"] == "non_tying_subtotal" for w in pl["where"]}
    for r in failed(ex):                           # only the planted row and the rows summed into it
        ptr = json.dumps({k: v for k, v in r.pointer.items() if k != "origin"}, sort_keys=True)
        assert ptr in bad_rows or r.checks == ["sum"], (r.row_label, r.field, r.checks)
    assert all(r.status == "ok" for r in ex.records)
    flagged_targets = {(json.dumps({k: v for k, v in r.pointer.items() if k != "origin"}, sort_keys=True), r.field)
                       for r in failed(ex) if r.row_kind != "asset"}
    assert planted <= flagged_targets or not planted
