"""Write the answer keys themselves as filled Arbeitsversion workbooks.

This is the scorer's self-test: the oracle must score 100 % correct with no
wrong values, and controlled damage to its output must cost exactly what the
rules say. It also exercises the template writer end to end.

    python -m carthagents.oracle --keys data/A3-Hackathon-Corpus --out outputs/oracle
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .arbeitsversion import GITTER_KLASSE, VERZEICHNIS, OutRow, Value, output_name, write_workbook
from .gt import Doc, load_corpus


def key_pointer(row_pointer: dict, field: str) -> dict:
    if "cells" in row_pointer:  # key without a cell for this field: point at the row's anchor cell
        return {"sheet": row_pointer["sheet"], "cell": row_pointer["cells"].get(field, row_pointer["cell"])}
    return {"page": row_pointer["page"], "bbox": row_pointer["bbox"], "origin": "answer_key"}


def oracle_rows(doc: Doc, year: int) -> list[OutRow]:
    rows = []
    for r in doc.rows:
        if r.year != year or r.tab is None:
            continue
        raw = r.raw
        if r.tab == VERZEICHNIS:
            no = raw.get("asset_no") or ""
            if raw.get("sub_no") not in (None, "", "0"):
                no = f"{no}-{raw['sub_no']}"
            text = {"anlagen_nr": no, "bezeichnung": raw.get("name")}
        else:
            ident = raw.get("class") if r.tab == GITTER_KLASSE else raw.get("position")
            text = {"anlagenklasse_nr": ident, "bezeichnung": raw.get("name") or ("Summe" if r.total else ident),
                    "zeilenart": r.level}
        values = {f: Value(c, key_pointer(r.pointer, f)) for f, c in r.fields.items()}
        rows.append(OutRow(r.tab, values, {k: v for k, v in text.items() if v}, total=r.total))
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--keys", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args(argv)
    for doc in load_corpus(args.keys):
        if args.only and doc.doc_id not in args.only and doc.meta["family"] not in args.only:
            continue
        for year in doc.years:
            write_workbook(args.out / output_name(doc.doc_id, year), oracle_rows(doc, year), doc.period_end(year))
        print("wrote", doc.doc_id, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
