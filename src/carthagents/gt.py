"""Ground truth: the generator's *.key.json files, projected onto the Arbeitsversion.

Only what the Arbeitsversion can hold is scored. Key rows and fields without a home
there are excluded and counted with a reason, so the scorer can report them:

- asset rows                          -> Anlagenverzeichnis
- class rows / class subtotals        -> Gitter nach Anlagenklasse
- Bilanzposition rows / subtotals     -> Gitter nach Bilanzposition
- grand totals                        -> Summe rows of the position Gitter if the document
                                         prints Bilanzposition rows, else of the class Gitter
- account-level rows/subtotals, carry-over lines  -> not scored (no tab)
- afa_gj on asset rows                -> not scored (JSON only; template splits plan/außerplan)
- abgang_bw (net-layout disposal at book value)   -> not scored (no column)
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .arbeitsversion import FIELD_IDS, GITTER_KLASSE, GITTER_POSITION, VERZEICHNIS


@dataclass
class KeyRow:
    index: int
    kind: str
    level: str
    year: int
    tab: str | None
    fields: dict[str, int]
    pointer: dict
    raw: dict

    @property
    def total(self) -> bool:
        return self.kind == "grand_total"


@dataclass
class Expected:
    row: KeyRow
    field: str
    cents: int


@dataclass
class Doc:
    doc_id: str
    path: Path
    meta: dict
    rows: list[KeyRow]
    excluded: Counter = field(default_factory=Counter)

    @property
    def years(self) -> list[int]:
        return sorted(self.meta["years"])

    def period_end(self, year: int) -> date:
        end = date.fromisoformat(self.meta["period_end"])
        return end.replace(year=end.year - (max(self.years) - year))

    def expected(self, year: int) -> list[Expected]:
        out = []
        for r in self.rows:
            if r.year != year or r.tab is None:
                continue
            for f, cents in r.fields.items():
                if f in FIELD_IDS[r.tab]:
                    out.append(Expected(r, f, cents))
        return out


def _level(r: dict, meta: dict) -> str:
    if r["kind"] != "asset":
        return r["kind"]
    return r.get("level") or meta.get("row_level") or "asset"


def _tab(level: str, has_positions: bool) -> str | None:
    return {
        "asset": VERZEICHNIS,
        "class": GITTER_KLASSE,
        "class_subtotal": GITTER_KLASSE,
        "bilanzposition": GITTER_POSITION,
        "position_subtotal": GITTER_POSITION,
        "grand_total": GITTER_POSITION if has_positions else GITTER_KLASSE,
    }.get(level)


def load_doc(key_path: Path) -> Doc:
    key = json.loads(key_path.read_text(encoding="utf-8"))
    meta = key["meta"]
    levels = [_level(r, meta) for r in key["rows"]]
    has_positions = any(lv in ("bilanzposition", "position_subtotal") for lv in levels)
    doc = Doc(meta["doc_id"], key_path, meta, [])
    for i, (r, lv) in enumerate(zip(key["rows"], levels)):
        tab = _tab(lv, has_positions)
        fields = dict(r.get("fields", {}))
        fields.update(r.get("net_fields", {}))
        row = KeyRow(i, r["kind"], lv, r["year"], tab, fields, r.get("pointer", {}), r)
        doc.rows.append(row)
        if tab is None:
            doc.excluded[f"row level '{lv}' has no tab"] += len(fields)
        else:
            for f in fields:
                if f not in FIELD_IDS[tab]:
                    doc.excluded[f"field '{f}' has no column on {tab}"] += 1
    return doc


def load_corpus(root: Path) -> list[Doc]:
    return [load_doc(p) for p in sorted(Path(root).rglob("*.key.json"))]


# --- pointer containment ---------------------------------------------------------------
PDF_TOLERANCE = 1.5  # points around the key's row box


def _row_of(cell: str) -> int:
    return int(re.search(r"\d+", cell).group())


def pointer_in_row(p: dict | None, row: KeyRow, field: str | None = None) -> bool:
    """True if pointer `p` shows a value of key row `row` (and, for Excel, of `field`).

    PDF keys only give the box of the whole row, so a PDF pointer must sit on the
    same page with its centre inside that box. Excel keys give one cell per field;
    where a key has no cell for a field (block layouts leave empty movement cells
    unaddressed, net-movement fields were never addressed), any cell on the rows
    the key row spans is accepted and only the value decides.
    """
    if not p:
        return False
    k = row.pointer
    if "cells" in k or "cell" in k:
        if p.get("sheet") != k.get("sheet") or not p.get("cell"):
            return False
        cells = k.get("cells", {})
        if field is not None and field in cells:
            return cells[field] == p["cell"]
        rows = [_row_of(c) for c in [*cells.values(), k.get("cell")] if c]
        return min(rows) <= _row_of(p["cell"]) <= max(rows)
    if "bbox" not in k or not p.get("bbox") or p.get("page") != k.get("page"):
        return False
    x0, y0, x1, y1 = k["bbox"]
    px0, py0, px1, py1 = p["bbox"]
    cx, cy = (px0 + px1) / 2, (py0 + py1) / 2
    t = PDF_TOLERANCE
    return x0 - t <= cx <= x1 + t and y0 - t <= cy <= y1 + t
