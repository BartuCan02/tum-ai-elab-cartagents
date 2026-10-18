"""OCR reader tests + corpus evaluation against the answer keys.

Fast test: one scanned page. Full evaluation (all scan/outline docs, a few minutes):
    CARTHAGENTS_OCR_EVAL=1 .venv/bin/python -m pytest tests/test_reader_ocr.py -s -k corpus
or  .venv/bin/python tests/test_reader_ocr.py [doc_id ...]
For every non-empty printed amount of every key row: "exact" if a cell on that page with its centre
inside the row box (+-2pt) reads exactly the printed text, "misread" if a leftover numeric cell of that
row is the most similar match (string ratio >= 0.5), else "missing".
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path

import pymupdf
import pytest

from carthagents.readers.ocr import NUMERIC, ocr_page

CORPUS = Path(__file__).resolve().parents[1] / "data" / "A3-Hackathon-Corpus"
THRESHOLDS = (0.0, 0.5, 0.7, 0.8, 0.85, 0.9, 0.95)
TOL = 2.0


def _ocr_docs() -> list[tuple[Path, dict]]:
    out = []
    for k in sorted(CORPUS.glob("f*/*.key.json")):
        key = json.loads(k.read_text())
        if key["meta"]["format"] in ("scan", "outline"):
            out.append((k.with_name(k.name.replace(".key.json", ".pdf")), key))
    return out


def _inside(bbox: list[float], row: list[float]) -> bool:
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    return row[0] - TOL <= cx <= row[2] + TOL and row[1] - TOL <= cy <= row[3] + TOL


def _eval_page(args: tuple[str, int, list[dict]]) -> dict:
    """OCR one page and classify each printed amount of the key rows on it."""
    path, pno, rows = args
    t = time.perf_counter()
    page = ocr_page(pymupdf.open(path)[pno - 1], pno)
    secs = time.perf_counter() - t
    cells = [c for ln in page.lines for c in ln.cells]
    res: dict = {"secs": secs, "exact": [], "misread": [], "missing": []}
    for row in rows:
        box = row["pointer"]["bbox"]
        mine = [c for c in cells if _inside(c.pointer["bbox"], box)]
        pool = [c for c in mine if NUMERIC.match(c.text)]
        want = [v for v in row["printed"].values() if v]
        left = []
        for v in want:
            hit = next((c for c in pool if c.text == v), None)
            if hit:
                pool.remove(hit)
                res["exact"].append(hit.conf)
            else:
                left.append(v)
        for v in left:
            best = max(pool, key=lambda c: SequenceMatcher(None, c.text, v).ratio(), default=None)
            if best is not None and SequenceMatcher(None, best.text, v).ratio() >= 0.5:
                pool.remove(best)
                res["misread"].append((v, best.text, best.conf))
            else:
                res["missing"].append(v)
    return res


def evaluate(doc_ids: list[str] | None = None, workers: int = 8) -> dict:
    """Run the evaluation, print a per-doc and per-threshold report, return the totals."""
    jobs, owner = [], []
    for path, key in _ocr_docs():
        if doc_ids and key["meta"]["doc_id"] not in doc_ids:
            continue
        pages: dict[int, list[dict]] = {}
        for r in key["rows"]:
            if r.get("pointer", {}).get("bbox"):
                pages.setdefault(r["pointer"]["page"], []).append(r)
        for pno, rows in sorted(pages.items()):
            jobs.append((str(path), pno, rows))
            owner.append(f'{key["meta"]["doc_id"]} ({key["meta"]["format"]})')
    with ProcessPoolExecutor(workers) as ex:
        results = list(ex.map(_eval_page, jobs))
    per: dict[str, dict] = {}
    for o, r in zip(owner, results):
        d = per.setdefault(o, {"secs": [], "exact": [], "misread": [], "missing": []})
        d["secs"].append(r["secs"])
        for k in ("exact", "misread", "missing"):
            d[k] += r[k]
    tot = {"secs": [], "exact": [], "misread": [], "missing": []}
    print(f'\n{"doc":34} {"n":>5} {"exact":>7} {"misrd":>7} {"miss":>7} {"s/page":>7}  misread@conf>=' + " ".join(f"{t:>4}" for t in THRESHOLDS))
    for o, d in per.items():
        for k in tot:
            tot[k] += d[k]
        _row(o, d)
    _row("TOTAL", tot)
    print("\nper threshold (all docs): kept exact / kept misread / misread rate among kept")
    for t in THRESHOLDS:
        e = sum(c >= t for c in tot["exact"])
        m = sum(x[2] >= t for x in tot["misread"])
        print(f"  conf>={t:<5} exact {e:5d}  misread {m:4d}  rate {m / max(1, e + m):.4%}")
    print("\nmisread examples (printed -> read, conf):")
    for o, d in per.items():
        for v, got, c in d["misread"][:int(os.environ.get("OCR_EVAL_EXAMPLES", "6"))]:
            print(f"  {o:34} {v!r:>16} -> {got!r:<18} {c:.3f}")
    print("\nmost common missing:", Counter(tot["missing"]).most_common(8))
    return tot


def _row(name: str, d: dict) -> None:
    n = len(d["exact"]) + len(d["misread"]) + len(d["missing"])
    rates = []
    for t in THRESHOLDS:
        e = sum(c >= t for c in d["exact"])
        m = sum(x[2] >= t for x in d["misread"])
        rates.append(f"{m / max(1, e + m):4.1%}"[:4] if m else "   0")
    secs = sum(d["secs"]) / max(1, len(d["secs"]))
    print(f'{name:34} {n:5d} {len(d["exact"]) / max(1, n):7.1%} {len(d["misread"]) / max(1, n):7.1%} '
          f'{len(d["missing"]) / max(1, n):7.1%} {secs:7.1f}  ' + " ".join(f"{r:>4}" for r in rates))


def test_ocr_one_scanned_page() -> None:
    path = CORPUS / "f00_demo" / "f00_demo_03.pdf"
    key = json.loads(path.with_name("f00_demo_03.key.json").read_text())
    rows = [r for r in key["rows"] if r["pointer"]["page"] == 1]
    page = ocr_page(pymupdf.open(path)[0], 1)
    assert page.origin == "ocr" and page.index == 1 and page.lines
    cell = page.lines[0].cells[0]
    assert cell.pointer["page"] == 1 and cell.pointer["origin"] == "ocr" and len(cell.chars) == len(cell.text)
    assert all(0.0 <= c.conf <= 1.0 for ln in page.lines for c in ln.cells)
    res = _eval_page((str(path), 1, rows))
    n = len(res["exact"]) + len(res["misread"]) + len(res["missing"])
    assert len(res["exact"]) >= 0.8 * n
    assert not [m for m in res["misread"] if m[2] >= 0.9]   # no confident misreads


@pytest.mark.skipif(not os.environ.get("CARTHAGENTS_OCR_EVAL"), reason="slow: set CARTHAGENTS_OCR_EVAL=1")
def test_ocr_corpus() -> None:
    tot = evaluate()
    assert len(tot["exact"]) > 0


if __name__ == "__main__":
    evaluate(sys.argv[1:] or None)
