"""Table interpreter: grid.Document -> Extraction (records per printed row, years).

Scope of this version (precision first, unsure -> nothing written):
- per page: header band = lines from the first line with schedule vocabulary down to the first data line;
  leaf columns = cells of the densest header line, parts = every header cell above that overlaps it
  (group headers such as "Anschaffungskosten" cover their sub-columns); columns.classify_columns maps them.
- data rows: a line whose identifier column (asset number) is filled and that holds amounts in mapped
  columns -> asset row -> Anlagenverzeichnis. Subtotals, totals, class/position rows, SAP blocks are not
  emitted yet (their tab cannot be decided safely without more work).
- one NumberStyle per document, unit scale from title/header texts (unknown -> nothing written),
  period end from title texts ("zum 31.12.2025", "bis 31.12.2025", ...), carried to following pages.
Never computes a value: every amount is a printed cell parsed exactly.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from . import numbers as nb
from .arbeitsversion import FIELD_IDS, VERZEICHNIS
from .grid import Cell, Document, Line, Page
from .model import Extraction, Record, YearResult

AMOUNT_FIELDS = ("ahk_open", "zugang", "abgang", "umbuchung", "ahk_close", "kum_afa_open", "afa_gj", "afa_abgang",
                 "zuschreibung", "kum_afa_close", "bw_close", "bw_prev", "abgang_bw", "bew_ahk", "bew_wb")
SIGNED = {"umbuchung", "bew_ahk", "bew_wb"}

try:  # the shared header vocabulary (other module); fallback below only maps unambiguous headers
    from .columns import ColumnHeader, classify_columns  # type: ignore
except Exception:  # noqa: BLE001
    ColumnHeader = None  # type: ignore
    classify_columns = None  # type: ignore

# ------------------------------------------------------------------ minimal fallback header matcher
_DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{2}|\d{4})\b")
_HEADER_WORDS = re.compile(r"zug[aä]ng|abg[aä]ng|buchwert|anschaffungs|herstellungs|abschreibung|umbuchung|"
                           r"\bahk\b|\bafa\b|bezeichnung|zuschreibung", re.I)


def _dates(text: str) -> list[date]:
    out = []
    for d, m, y in _DATE.findall(text):
        y = int(y)
        y = y + 2000 if y < 100 else y
        try:
            out.append(date(y, int(m), int(d)))
        except ValueError:
            pass
    return out


def _fallback_field(parts: list[str], period_end: date | None) -> str | None:
    text = " ".join(parts)
    low = text.lower()
    if re.search(r"anl(?:agen)?[.\-\s]*nr|anlagen-?nummer|inventar-?nr", low):
        return "asset_no"
    if re.fullmatch(r"\s*bezeichnung\s*", low):
        return "name"
    ahk = bool(re.search(r"anschaffungs|herstellungs|\bahk\b", low))
    afa = bool(re.search(r"abschreibung|\bafa\b", low))
    bw = "buchwert" in low
    if ahk + afa + bw != 1:
        return None
    ds = _dates(text)
    kind = None
    if ds and period_end:
        if len(ds) != 1:
            return None
        d = ds[0]
        prev_end = period_end.replace(year=period_end.year - 1)
        start = prev_end + timedelta(days=1)
        if d == period_end:
            kind = "close"
        elif d == start:
            kind = "open"
        elif d == prev_end:
            kind = "prev"
        else:
            return None
    words = [w for w in ("zug", "abg", "umbuch", "zuschreib", "geschäftsjahr") if w in low.replace("ä", "ä")]
    words = [w for w in ("zug", "abg", "umbuch", "zuschreib", "geschäftsjahr") if re.search(
        {"zug": r"zug[aä]ng", "abg": r"abg[aä]ng", "umbuch": r"umbuch", "zuschreib": r"zuschreib",
         "geschäftsjahr": r"gesch[aä]ftsjahr"}[w], low)]
    if kind and words:
        return None
    if len(words) > 1:
        return None
    if ahk:
        if kind == "open":
            return "ahk_open"
        if kind == "close":
            return "ahk_close"
        return {"zug": "zugang", "abg": "abgang", "umbuch": "umbuchung"}.get(words[0]) if words else None
    if afa:
        if kind == "open":
            return "kum_afa_open"
        if kind == "close":
            return "kum_afa_close"
        return {"abg": "afa_abgang", "zuschreib": "zuschreibung", "geschäftsjahr": "afa_gj"}.get(words[0]) if words else None
    if bw and not words:
        return {"close": "bw_close", "prev": "bw_prev"}.get(kind or "")
    return None


def _classify(headers: list[tuple[object, list[str]]], period_end: date | None) -> dict:
    if classify_columns is not None:
        try:
            res = classify_columns([ColumnHeader(k, p) for k, p in headers], period_end)
            return {k: getattr(a, "field", None) for k, a in res.items()}
        except Exception:  # noqa: BLE001
            pass
    return {k: _fallback_field(p, period_end) for k, p in headers}


# ------------------------------------------------------------------ helpers
def _is_amount(c: Cell) -> bool:
    if c.number is not None:
        return True
    return nb.is_amount_like(c.text) and not _dates(c.text)


def _period_end(texts: list[str]) -> date | None:
    for pat in (r"(?:zum|bis|per|stichtag|berichtsdatum|bis zum)\s*:?\s*(\d{1,2}\.\d{1,2}\.\d{2,4})",):
        found = []
        for t in texts:
            for m in re.finditer(pat, t, re.I):
                found += _dates(m.group(1))
        if found and len(set(found)) == 1:
            return found[0]
    return None


class _Col:
    def __init__(self, key, x0, x1, parts, field):
        self.key, self.x0, self.x1, self.parts, self.field = key, x0, x1, parts, field


def _header(page: Page) -> tuple[int, int, list[str]] | None:
    """(first header line idx, first data line idx, title texts) or None."""
    lines = page.lines
    start = None
    for i, ln in enumerate(lines):
        hits = sum(bool(_HEADER_WORDS.search(c.text)) for c in ln.cells if not c.hidden)
        if hits >= 2 and start is None:
            start = i
        if start is not None and i > start:
            amounts = sum(_is_amount(c) for c in ln.cells if not c.hidden)
            if amounts >= 2:
                return start, i, [c.text for l in lines[:i] for c in l.cells]
    return None


def _columns(page: Page, h0: int, h1: int, period_end: date | None) -> list[_Col]:
    band = [ln for ln in page.lines[h0:h1] if not ln.hidden]
    leaf_line = max(band, key=lambda ln: (len([c for c in ln.cells if not c.hidden]), ln.y0))
    leaves = [c for c in leaf_line.cells if not c.hidden]
    heads = []
    for k, c in enumerate(leaves):
        parts = []
        for ln in band:
            if ln.y0 > leaf_line.y0:
                continue
            for o in ln.cells:
                if o.hidden:
                    continue
                if o is c or (ln is not leaf_line and min(o.x1, c.x1) - max(o.x0, c.x0) > 0):
                    parts.append(o.text)
        heads.append((k, parts))
    fields = _classify(heads, period_end)
    cols = [_Col(k, c.x0, c.x1, p, fields.get(k)) for (k, p), c in zip(heads, leaves)]
    # a field mapped twice is ambiguous: drop both
    seen = {}
    for c in cols:
        if c.field:
            seen[c.field] = seen.get(c.field, 0) + 1
    for c in cols:
        if c.field and seen[c.field] > 1:
            c.field = None
    return cols


def _col_of(cell: Cell, cols: list[_Col], xlsx: bool) -> _Col | None:
    if xlsx:
        hits = [c for c in cols if c.x0 <= cell.x0 < c.x1]
        return hits[0] if len(hits) == 1 else None
    best, ov = None, 0.0
    second = 0.0
    for c in cols:
        o = min(c.x1, cell.x1) - max(c.x0, cell.x0)
        if o > ov:
            best, second, ov = c, ov, o
        elif o > second:
            second = o
    if best is None or second > 0:
        return None
    return best


def _cents(cell: Cell, style: nb.NumberStyle | None) -> int | None:
    parsed = nb.parse_amount(cell.text, style) if style else None
    if cell.number is not None:
        q = cell.number.quantize(Decimal("0.01"), ROUND_HALF_UP)
        cents = int(q * 100)
        if abs(cell.number - q) > Decimal("0.000001"):
            return None
        if parsed is None or parsed.cents != cents:
            return None
        return cents
    return parsed.cents if parsed else None


# ------------------------------------------------------------------ main
def interpret(doc: Document) -> Extraction:
    ex = Extraction(doc.path, doc.format, [], [], [])
    xlsx = doc.format == "xlsx"
    pages = []
    titles: list[str] = []
    for p in doc.pages:
        h = _header(p)
        if h is None:
            continue
        pages.append((p, h))
        titles += h[2]
    if not pages:
        ex.notes.append("no schedule table found")
        return ex
    scale = nb.unit_scale(titles)
    ex.unit_scale = scale
    # identifiers (first cell of a line) and dates are not amounts
    samples = [c.text for p, (h0, h1, _) in pages for ln in p.lines[h1:] for c in ln.cells[1:]
               if not c.hidden and _is_amount(c) and nb.is_amount_like(c.text)]
    style = nb.detect_style(samples)
    if style is None:
        ex.notes.append("number style undetermined")
    if scale is None:
        ex.notes.append("unit unknown: no values written")
    row_id = 0
    years: dict[int, date] = {}
    period = None
    for p, (h0, h1, title) in pages:
        pe = _period_end(title)
        period = pe or period
        if period is None:
            ex.notes.append(f"page {p.index}: period end unknown")
        cols = _columns(p, h0, h1, period)
        id_col = next((c for c in cols if c.field == "asset_no"), None)
        name_col = next((c for c in cols if c.field == "name"), None)
        amount_cols = [c for c in cols if c.field in AMOUNT_FIELDS]
        if id_col is None or not amount_cols:
            continue
        year = period.year if period else None
        if period:
            years[period.year] = period
        for ln in p.lines[h1:]:
            if ln.hidden:
                continue
            if sum(bool(_HEADER_WORDS.search(c.text)) for c in ln.cells if not c.hidden) >= 2:
                break  # a second table header: the column model ends here
            cells = [c for c in ln.cells if not c.hidden]
            placed = {}
            ident = name = None
            bad = False
            for c in cells:
                col = _col_of(c, cols, xlsx)
                if col is None:
                    if _is_amount(c):
                        bad = True
                    continue
                if col is id_col:
                    ident = c
                elif col is name_col:
                    name = c
                elif col.field in AMOUNT_FIELDS:
                    if col.key in placed:
                        bad = True
                    placed[col.key] = c
                elif _is_amount(c):
                    pass
            if ident is None or not re.fullmatch(r"\d[\d\-/.]*", ident.text):
                continue
            if sum(1 for c in placed.values() if c.text.strip()) < 2:
                continue  # too few printed amounts to trust the row
            label = " ".join(c.text for c in cells if not _is_amount(c))
            if re.search(r"summe|gesamt|total|übertrag|vortrag", label, re.I):
                continue
            row_text = {"anlagen_nr": ident.text}
            if name is not None:
                row_text["bezeichnung"] = name.text
            for col in amount_cols:
                f = col.field
                tab = VERZEICHNIS if f in FIELD_IDS[VERZEICHNIS] else None
                c = placed.get(col.key)
                status, flag, cents, ptr, raw = "ok", None, None, None, None
                if bad:
                    status, flag = "blank_flagged", "row_cells_unaligned"
                elif scale is None:
                    status, flag = "blank_flagged", "unit_unknown"
                elif c is None:
                    if xlsx:
                        cents, raw, ptr = 0, "", p.address(int(col.x0) + 1, ln.row)
                    else:  # PDF empty cell: column/row geometry not verified yet -> abstain
                        status, flag = "blank_flagged", "empty_cell_unverified"
                else:
                    raw, ptr = c.text, c.pointer
                    v = _cents(c, style)
                    if p.origin == "ocr" and (c.conf is None or c.conf < 0.95):
                        status, flag = "blank_flagged", "ocr_low_confidence"
                    elif v is None:
                        status, flag = "blank_flagged", "unparseable"
                    else:
                        cents = v * scale
                        if f not in SIGNED:
                            cents = abs(cents)
                if status != "ok":
                    cents = None
                if tab is None:
                    status = "not_in_template"
                ex.records.append(Record(year, tab, ident.text, "asset", f, cents, raw, ptr, status, flag,
                                         row_id=row_id, row_text=row_text))
            row_id += 1
    ex.years = [YearResult(y, pe) for y, pe in sorted(years.items())]
    return ex
