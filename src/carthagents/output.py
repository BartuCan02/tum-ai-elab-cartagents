"""Write one document's results: a filled Arbeitsversion per fiscal year + one audit JSON.

    <stem>_<year>_Arbeitsversion.xlsx   (scored; pointers in cell comments, flags under Offene Punkte)
    <stem>_audit.json                   (one record per value, notes, summary)
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .arbeitsversion import output_name, write_workbook
from .model import Extraction

STATUSES = ("ok", "blank_flagged", "not_in_template")


def audit_name(stem: str) -> str:
    return f"{stem}_audit.json"


def save(ex: Extraction, out_dir: Path, stem: str) -> dict:
    """Write the outputs and return the audit dict (also written as JSON)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    notes = list(ex.notes)
    workbooks: dict[str, str] = {}
    for yr in ex.years:
        name = output_name(stem, yr.year)
        try:
            write_workbook(out_dir / name, yr.rows, yr.period_end, yr.open_points)
            workbooks[str(yr.year)] = name
        except Exception as exc:  # noqa: BLE001 - keep the other years and the JSON
            notes.append(f"workbook {name} not written: {type(exc).__name__}: {exc}")
    if not ex.years:
        msg = "no fiscal year could be determined: no workbook written"
        notes.append(msg)
        print(f"{stem}: {msg}", file=sys.stderr)

    counts = Counter(r.status for r in ex.records)
    audit = {
        "source": ex.source,
        "format": ex.format,
        "tool": {"name": "carthagents", "version": __version__},
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "years": [yr.year for yr in ex.years],
        "workbooks": workbooks,
        "summary": {**{s: counts.get(s, 0) for s in STATUSES},
                    **{s: n for s, n in counts.items() if s not in STATUSES},
                    "records": len(ex.records),
                    "open_points": sum(len(yr.open_points) for yr in ex.years)},
        "notes": notes,
        "records": [r.dump() for r in ex.records],
    }
    (out_dir / audit_name(stem)).write_text(json.dumps(audit, ensure_ascii=False, indent=1, default=str),
                                            encoding="utf-8")
    return audit
