from pathlib import Path

import pytest

from carthagents.interpret import interpret
from carthagents.readers import load_document

DEMO = Path(__file__).resolve().parents[1] / "data/A3-Hackathon-Corpus/f00_demo/f00_demo_01.xlsx"


@pytest.mark.skipif(not DEMO.exists(), reason="corpus not available")
def test_demo_xlsx_asset_rows():
    ex = interpret(load_document(DEMO, ocr=False))
    assert [y.year for y in ex.years] == [2025]
    ok = [r for r in ex.records if r.status == "ok"]
    assert ok and all(r.row_kind == "asset" for r in ok)
    first = next(r for r in ok if r.row_label == "27001" and r.field == "ahk_open")
    assert first.cents == 4041497 and first.pointer == {"sheet": "Anlagenspiegel", "cell": "C8"}
    assert not any("Summe" in r.row_label for r in ex.records)
