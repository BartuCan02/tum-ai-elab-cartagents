import glob
import json
from pathlib import Path

import pytest

from carthagents.numbers import (
    Amount, NumberStyle, detect_style, is_amount_like, is_zero_placeholder, parse_amount, parse_cents,
    split_glued, unit_scale,
)

CORPUS = Path(__file__).resolve().parents[1] / "data" / "A3-Hackathon-Corpus"
KEYS = sorted(glob.glob(str(CORPUS / "f*" / "*.key.json")))
SIGNED = {"umbuchung", "bew_ahk", "bew_wb"}
DE = NumberStyle(",", ".", 2, frozenset({"leading", "trailing", "parens"}))
SAP = NumberStyle(",", ".", 2, frozenset({"trailing"}))
TEUR = NumberStyle(",", ".", 0, frozenset({"trailing"}))


def _key(path: str) -> dict:
    return json.loads(Path(path).read_text())


def _printed(key: dict):
    for r in key["rows"]:
        for f, t in (r.get("printed") or {}).items():
            yield r, f, t


# ---------- unit tests ----------

@pytest.mark.parametrize("text,cents,neg", [
    ("19.425,96", 1942596, False), ("1.234,56-", -123456, True), ("-1.234,56", -123456, True),
    ("(1.234,56)", -123456, True), ("0,00", 0, False), ("1.227.064,30", 122706430, False),
    ("-0,00", 0, True), ("1.234,56 -", -123456, True), ("−1.234,56", -123456, True),
])
def test_parse_german(text, cents, neg):
    assert parse_amount(text, DE) == Amount(cents, neg, text)


def test_parse_teur_and_space_group():
    assert parse_amount("4.303", TEUR).cents == 430300
    assert parse_amount("49", TEUR).cents == 4900
    assert parse_amount("1.406-", TEUR).cents == -140600
    sp = NumberStyle(",", " ", 2, frozenset())
    assert parse_amount("12 345,67", sp).cents == 1234567
    assert parse_amount("12 345,67", sp).cents == 1234567


@pytest.mark.parametrize("text", [
    "1234,56", "1.23,45", "01,00", "00,00", "0.123,00", "1.234,5", "1.234,567", "1,234.56", "", "-", "abc",
    "1.234,56-", "(1,00", "1.234", "--1,00", "1.234,56--",
])
def test_parse_rejects(text):
    style = NumberStyle(",", ".", 2, frozenset({"leading"}))
    assert parse_amount(text, style) is None


def test_placeholder_and_amount_like():
    assert all(is_zero_placeholder(t) for t in ("-", "–", "—", " - "))
    assert not is_zero_placeholder("0,00")
    assert parse_amount("-", DE) is None
    assert all(is_amount_like(t) for t in ("1.234,56-", "(1,00)", "49", "12 345,67", "0,000,00"))
    assert not any(is_amount_like(t) for t in ("", "-", "EUR", "Konto 1,0a", "1.234,"))


def test_detect_style_basic():
    assert detect_style(["1.234,56", "0,00", "5,00-"]) == NumberStyle(",", ".", 2, frozenset({"trailing"}))
    assert detect_style(["4.303", "49", "40-"]) == NumberStyle(",", ".", 0, frozenset({"trailing"}))
    assert detect_style(["1,234.56", "(3.00)"]) == NumberStyle(".", ",", 2, frozenset({"parens"}))
    assert detect_style(["12 345,67", "-1,00"]) == NumberStyle(",", " ", 2, frozenset({"leading"}))
    # no evidence of grouping → group None, so '1.234,56' is later rejected rather than guessed
    st = detect_style(["0,00", "12,50"])
    assert st == NumberStyle(",", None, 2, frozenset()) and parse_amount("1.234,56", st) is None
    # mixed decimals
    assert detect_style(["1.234,56", "0"]).decimals is None
    # glued / junk samples are ignored, not fatal
    assert detect_style(["1.234,56", "12.977,004.076,11", "Summe", "-"]) is not None


@pytest.mark.parametrize("samples", [
    ["49", "0", "12"],                 # pure integers: decimal separator undecidable
    ["1.234", "1,234"],                # same shape, both separators → contradictory
    ["1.23", "1.234,56"],              # '.' decimal vs ',' decimal
    ["1.234,56", "1,234.56"],          # German and English mixed
    ["1 234,56", "1.234,56"],          # two grouping characters
    ["1234,56", "1.234,56"],           # grouped and ungrouped
    [], ["", "-", "Summe"],
])
def test_detect_style_ambiguous_none(samples):
    assert detect_style(samples) is None


def test_parse_cents_wrapper():
    assert parse_cents("1.234,56-") == -123456
    assert parse_cents("49") is None          # ambiguous alone
    assert parse_cents("1,23") == 123


def test_split_glued_examples():
    assert split_glued("12.977,004.076,11", DE) == [("12.977,00", "4.076,11")]
    assert split_glued("0,000,00", DE) == [("0,00", "0,00")]
    assert split_glued("30.251,3312.100,54", DE) == [("30.251,33", "12.100,54")]
    assert split_glued("4.3034.446", TEUR) == [("4.303", "4.446")]
    assert split_glued("334,041,00", DE) == [("334,04", "1,00")]
    assert split_glued("1.234,56-7.890,12-", SAP) == [("1.234,56-", "7.890,12-")]
    # with both leading and trailing minus allowed, the minus between them is ambiguous
    assert set(split_glued("2.939,84-0,00", DE)) == {("2.939,84-", "0,00"), ("2.939,84", "-0,00")}
    assert len(split_glued("1234", TEUR)) == 3      # 1|234, 12|34, 123|4 → ambiguous, caller abstains
    assert split_glued("0,00", DE) == []
    assert split_glued("1,001,002,00", DE, n=3) == [("1,00", "1,00", "2,00")]


def test_unit_scale():
    assert unit_scale(["Anlagenspiegel", "Angaben in TEUR"]) == 1000
    assert unit_scale(["Beträge in T€"]) == 1000
    assert unit_scale(["in Tausend Euro"]) == 1000
    assert unit_scale(["Tsd. EUR"]) == 1000
    assert unit_scale(["Beträge in EUR"]) == 1
    assert unit_scale(["Summe €"]) == 1
    assert unit_scale(["Anlagenspiegel"]) is None
    assert unit_scale(["TEUR", "Beträge in EUR"]) is None
    assert unit_scale(["Mio. EUR"]) is None


# ---------- corpus ground truth ----------

def _doc_style(key: dict) -> NumberStyle | None:
    return detect_style(t for _, _, t in _printed(key))


@pytest.mark.parametrize("path", KEYS, ids=lambda p: Path(p).stem)
def test_corpus_printed_values(path):
    key = _key(path)
    scale = {"EUR": 1, "TEUR": 1000}[key["meta"]["unit"]]
    style = _doc_style(key)
    assert style is not None
    # planted 'transfer_breaks_cost_law': the key holds the implied transfer, not the printed one
    implied = {w["field"] for p in key["planted"] if p["type"] == "transfer_breaks_cost_law" for w in p["where"]}
    for r, f, t in _printed(key):
        want = r["fields"].get(f)
        if not t or want is None or f in implied:
            continue
        if is_zero_placeholder(t):
            assert want == 0
            continue
        a = parse_amount(t, style)
        assert a is not None, (f, t, style)
        assert abs(a.cents * scale) == abs(want), (f, t, want)
        if f in SIGNED:
            assert (a.cents < 0) == (want < 0), (f, t, want)
        else:
            assert want >= 0, (f, t, want)


def _glued_cases():
    for path in KEYS:
        key = _key(path)
        for p in key["planted"]:
            if p.get("type") != "glued_numbers":
                continue
            for w in p["where"]:
                yield Path(path).stem, key, w


@pytest.mark.parametrize("doc,key,where", list(_glued_cases()), ids=lambda x: x if isinstance(x, str) else "")
def test_corpus_glued(doc, key, where):
    pg, ln = where["row"]["page"], where["row"]["line"]
    rows = [r for r in key["rows"] if r.get("pointer", {}).get("page") == pg and r["pointer"].get("line") == ln]
    assert len(rows) == 1
    r = rows[0]
    style = _doc_style(key)
    expected = tuple(r["printed"][f] for f in where["fields"])
    cands = split_glued(where["printed"], style, len(where["fields"]))
    assert expected in cands, (where["printed"], expected, cands)
