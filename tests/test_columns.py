"""Header classification: labelled dataset (from answer keys) must give zero wrong fields."""
import datetime as dt
import json
from pathlib import Path

from carthagents.columns import ColumnHeader, classify_columns

DATA = Path(__file__).parent / "data" / "headers.json"
# builder label errors (key fields merged into one column by the PDF geometry, not a classifier error)
KNOWN_BAD_LABELS = {("f02_asset_spiegel_05", ("AHK", "31.12.2025"))}


def _run():
    wrong, n = [], 0
    for t in json.loads(DATA.read_text()):
        hs = [ColumnHeader(i, c["parts"]) for i, c in enumerate(t["columns"])]
        out = classify_columns(hs, dt.date.fromisoformat(t["period_end"]) if t.get("period_end") else None)
        for i, c in enumerate(t["columns"]):
            if c["expected"] == "?" or (t["doc"], tuple(c["parts"])) in KNOWN_BAD_LABELS:
                continue
            n += 1
            p = out[i].field
            if p is not None and p != c["expected"]:
                wrong.append((t["doc"], c["parts"], c["expected"], p, out[i].reason))
    return wrong, n


def test_dataset_zero_wrong():
    wrong, n = _run()
    assert n > 500
    assert not wrong, wrong


def cls(*cols, pe=dt.date(2025, 12, 31)):
    out = classify_columns([ColumnHeader(i, list(c)) for i, c in enumerate(cols)], pe)
    return [out[i].field for i in range(len(cols))]


def test_group_and_dates():
    g, k, b = "Anschaffungs- und Herstellungskosten", "Kumulierte Abschreibungen", "Buchwerte"
    assert cls((g, "01.01.2025"), (g, "Zugänge"), (g, "Abgänge"), (g, "31.12.2025"), (k, "01.01.2025"),
               (k, "Geschäftsjahr"), (k, "Abgänge"), (k, "31.12.2025"), (b, "31.12.2025"), (b, "31.12.2024")) == \
        ["ahk_open", "zugang", "abgang", "ahk_close", "kum_afa_open", "afa_gj", "afa_abgang", "kum_afa_close",
         "bw_close", "bw_prev"]


def test_sap_summary_kum_is_opening():
    assert cls(("kum AnschWert",), ("Bew AnschWert",), ("kum AfA",), ("gep AfA",), ("Bew Wertber",),
               ("Ende-Buchwert",), ("Beg-Buchwert",)) == \
        ["ahk_open", "bew_ahk", "kum_afa_open", "afa_gj", "bew_wb", "bw_close", "bw_prev"]


def test_net_layout_and_ignores():
    assert cls(("Buchwert Vorjahr",), ("Zugänge, Geschäftsjahr",), ("davon aktivierte Zinsen Fremdkapital",),
               ("Abgänge / Minderungen Geschäftsjahr",), ("Normal-AfA Geschäftsjahr",), ("Sonder-AfA Geschäftsjahr",),
               ("Abschreibungen (kumuliert)",), ("Buchwert 31.12.",)) == \
        ["bw_prev", "zugang", "ignore", "abgang_bw", "afa_gj", "ignore", "kum_afa_close", "bw_close"]


def test_roles_and_hyphenation():
    assert cls(("Unter-nummer",), ("Währ-ung",), ("Zugang", "am"), ("Inventar-", "Nummer"), ("AfA", "Art")) == \
        ["sub_no", "currency", "acq_date", "asset_no", "method"]


def test_ambiguous_block_columns_unknown():
    assert cls(("Stand zum", "01.01.2025"), ("Zugang", "Abgang-"), ("Abschreibung", "Zuschreibung-")) == [None] * 3
