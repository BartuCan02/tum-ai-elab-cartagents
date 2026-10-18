"""PDF reader against the answer keys: format detection, cell texts and pointer boxes."""
from __future__ import annotations

import json
from collections import Counter
from functools import lru_cache
from pathlib import Path

import pytest

from carthagents.grid import Cell, Document
from carthagents.readers.pdf import read_pdf

CORPUS = Path(__file__).resolve().parents[1] / "data" / "A3-Hackathon-Corpus"
KEYS = sorted(CORPUS.glob("f*/*.key.json"))
TOL = 1.5


def _key(p: Path) -> dict:
    return json.loads(p.read_text("utf-8"))


PDF_KEYS = [k for k in KEYS if _key(k)["meta"]["format"] != "xlsx"]
TEXT_KEYS = [k for k in PDF_KEYS if _key(k)["meta"]["format"] == "pdf"]


@lru_cache(maxsize=None)
def _read(key: Path, ocr: bool = False) -> Document:
    return read_pdf(key.with_name(key.name.replace(".key.json", ".pdf")), ocr=ocr)


def _inside(c: Cell, bb: list[float]) -> bool:
    x0, y0, x1, y1 = c.pointer["bbox"]
    return bb[0] - TOL <= (x0 + x1) / 2 <= bb[2] + TOL and bb[1] - TOL <= (y0 + y1) / 2 <= bb[3] + TOL


@pytest.mark.parametrize("key", PDF_KEYS, ids=lambda k: k.name[:-9])
def test_format_detection(key: Path) -> None:
    assert _read(key).format == _key(key)["meta"]["format"]


@pytest.mark.parametrize("key", TEXT_KEYS, ids=lambda k: k.name[:-9])
def test_printed_values_have_cells(key: Path) -> None:
    """Every printed value is one cell inside its key row box; glued / cut-off values are planted exceptions."""
    k, doc = _key(key), _read(key)
    cells = {p.index: [c for ln in p.lines for c in ln.cells] for p in doc.pages}
    glued = {(w["row"]["page"], tuple(w["row"]["bbox"])): w["printed"]
             for p in k["planted"] if p["type"] == "glued_numbers" for w in p["where"]}
    cut = any(p["type"] == "column_cut_off" for p in k["planted"])
    stats, missing = Counter(), []
    for r in k["rows"]:
        page, bb = r["pointer"]["page"], r["pointer"]["bbox"]
        here = [c for c in cells.get(page, []) if _inside(c, bb)]
        for field, text in r.get("printed", {}).items():
            if not text:
                continue
            g = glued.get((page, tuple(bb)))
            if g and text in g:
                stats["glued"] += 1
                assert any(c.text == g for c in here), (page, g)
            elif any(c.text == text for c in here):
                stats["ok"] += 1
            elif cut and any(text.startswith(c.text) and len(c.text) < len(text) for c in here):
                stats["cut_off"] += 1
            else:
                missing.append((page, field, text, [c.text for c in here]))
    assert not missing, missing[:10]
    assert stats["ok"] > 0


def test_rotated_sap_print() -> None:
    """f06 prints read bottom to top: lines come out horizontal, pointers stay in the key frame."""
    key = CORPUS / "f06_sap_asset_gitter" / "f06_sap_asset_gitter_04.key.json"
    page = next(p for p in _read(key).pages if p.index == 2)
    assert page.rotation == 90 and page.width > page.height
    row = next(r for r in _key(key)["rows"] if r["pointer"]["page"] == 2)
    line = next(ln for ln in page.lines if any(c.text == row["printed"]["ahk_open"] and _inside(c, row["pointer"]["bbox"])
                                               for c in ln.cells))
    assert len(line.cells) >= len([v for v in row["printed"].values() if v])
    assert max(c.y1 for c in line.cells) - min(c.y0 for c in line.cells) < 10


@pytest.mark.parametrize("key", TEXT_KEYS[:6], ids=lambda k: k.name[:-9])
def test_chars_align_with_text(key: Path) -> None:
    for p in _read(key).pages:
        for ln in p.lines:
            for c in ln.cells:
                assert c.chars is not None and "".join(ch for ch, _ in c.chars) == c.text
                assert c.text == c.text.strip() and "  " not in c.text
                assert p.origin == c.pointer["origin"] == "text_layer" and c.pointer["page"] == p.index


def test_odd_files_never_raise(tmp_path: Path) -> None:
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"%PDF-1.4 not really")
    assert read_pdf(bad).notes and read_pdf(tmp_path / "missing.pdf").notes
