"""Conservative, layout-agnostic first-pass row/column inference."""
from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass, asdict
from .numbers import parse_cents
from .reader import Token
import re

FIELDS = {
 "ak_anfang": ("ak anfang","anschaffungskosten anfang","historische anschaffungskosten","opening cost","anschaffungswert anfang"),
 "zugang": ("zugänge","zugang","additions","accessions"),
 "abgang": ("abgänge","abgang","disposals"),
 "umbuchung": ("umbuchungen","umbuchung","transfers"),
 "ak_ende": ("ak ende","anschaffungskosten ende","closing cost","ak 31.12","endbestand ak"),
 "afa_kumuliert": ("kumulierte abschreibungen","abschreibungen kumuliert","cum depreciation","afa kumuliert"),
 "afa_jahr": ("abschreibungen im geschäftsjahr","jahresabschreibung","abschreibung gj","depreciation current year","afa jahr"),
 "buchwert_ende": ("buchwert 31.12","buchwert ende","net book value","restbuchwert"),
 "buchwert_vorjahr": ("buchwert vorjahr","buchwert 01.01","prior book value"),
}
HEADER = {f: tuple(re.sub(r"\s+"," ",v.lower()) for v in synonyms) for f,synonyms in FIELDS.items()}
AMOUNT_RE = re.compile(r"^[\-−(]?[\d][\d.,' ]*[)\-]?$|^[—–-]$")

@dataclass
class Entry:
    kind: str
    identifier: str
    row_label: str
    field: str
    value_cents: int | None
    raw_text: str | None
    pointer: dict | None
    status: str
    reason: str | None = None

    def dump(self): return asdict(self)

def classify_header(text: str) -> str | None:
    t=re.sub(r"\s+"," ",text.lower().strip())
    matches=[]
    for field,aliases in HEADER.items():
        for a in aliases:
            if a in t: matches.append((len(a),field))
    return max(matches)[1] if matches else None

def pdf_rows(tokens: list[Token]) -> list[list[Token]]:
    groups=defaultdict(list)
    for t in tokens:
        if t.bbox: groups[(t.page,round((t.bbox[1]+t.bbox[3])/2 / 5)*5)].append(t)
    return [sorted(v,key=lambda t:t.bbox[0]) for _,v in sorted(groups.items())]

def excel_rows(tokens: list[Token]) -> list[list[Token]]:
    groups=defaultdict(list)
    for t in tokens: groups[(t.sheet,t.row)].append(t)
    return [sorted(v,key=lambda t:t.col) for _,v in sorted(groups.items())]

def infer_entries(tokens: list[Token]) -> list[Entry]:
    """High precision only: require header match and a non-total labelled data row.

    Unmatched documents produce a review flag, not invented amounts.
    """
    if not tokens: return [Entry("unknown","","","_review",None,None,None,"blank","No selectable text found; OCR may be needed")]
    pdf=any(t.page is not None for t in tokens)
    rows=pdf_rows(tokens) if pdf else excel_rows(tokens)
    out=[]
    columns={}
    active_page=None
    for row in rows:
        if not row: continue
        page=row[0].page if pdf else row[0].sheet
        if page!=active_page:
            columns={};active_page=page
        # Use individual cells first; a combined header fallback is deliberately omitted to avoid guessing.
        candidates=[(t,classify_header(t.text)) for t in row]
        matched=[(t,f) for t,f in candidates if f]
        if len(matched)>=2:
            columns={t.col if not pdf else (t.bbox[0]+t.bbox[2])/2:f for t,f in matched}
            continue
        if not columns: continue
        text=" ".join(t.text for t in row)
        if not text.strip() or len(row)<2: continue
        first=row[0].text.strip()
        is_total=bool(re.search(r"summe|gesamt|total|zwischensumme",text,re.I))
        kind="subtotal" if is_total else ("asset" if re.fullmatch(r"\d{4,}",first) else "class")
        identifier=first if not is_total else re.sub(r"\s+"," ",text[:75])
        if AMOUNT_RE.fullmatch(first) and kind!="asset" and not is_total: continue
        seen=set()
        for t in row[1:]:
            if not AMOUNT_RE.fullmatch(t.text.strip()): continue
            x=t.col if not pdf else (t.bbox[0]+t.bbox[2])/2
            if pdf:
                nearest=min(columns,key=lambda c:abs(c-x))
                # Require proximity: no cross-table inferred columns.
                if abs(nearest-x)>75: continue
                field=columns[nearest]
            else:
                field=columns.get(x)
            if not field or field in seen:continue
            seen.add(field)
            value=parse_cents(t.text)
            status="ok" if value is not None else "blank"
            out.append(Entry(kind,identifier,text[:130],field,value,t.text,t.pointer(),status,None if status=="ok" else "Unparseable numeric token"))
    if not out:
        out.append(Entry("unknown","","","_review",None,None,None,"blank","No confidently mapped rows/columns; provide official examples or extend header mappings"))
    return out

def checks(entries: list[Entry]) -> list[dict]:
    """Flag contradictions, never repair the original numbers."""
    groups=defaultdict(dict)
    for e in entries:
        if e.status=="ok":groups[(e.kind,e.identifier)][e.field]=e.value_cents
    issues=[]
    for (kind,identifier),v in groups.items():
        needed=("ak_anfang","zugang","abgang","umbuchung","ak_ende")
        if all(k in v for k in needed):
            expected=v["ak_anfang"]+v["zugang"]-v["abgang"]+v["umbuchung"]
            if expected != v["ak_ende"]:
                issues.append({"kind":kind,"identifier":identifier,"check":"cost_rollforward","expected_cents":expected,"printed_cents":v["ak_ende"],"status":"mismatch"})
    return issues
