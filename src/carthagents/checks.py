"""Validation: row laws, subtotal sums, cross-year ties, OCR / implied-zero gating.

Never changes a printed value. It only appends check names to Record.checks, downgrades
"ok" records to "blank_flagged" (OCR values without arithmetic support, empty cells read
as zero inside a failing row law) and adds OpenPoints. Never raises.
"""
from __future__ import annotations

from dataclasses import dataclass

from .arbeitsversion import OpenPoint
from .model import Extraction, Record

# (name, target, {term: sign}, terms counted as 0 when the whole document never prints them)
LAWS = [
    ("law_ahk", "ahk_close", {"ahk_open": 1, "zugang": 1, "abgang": -1, "umbuchung": 1}, {"umbuchung"}),
    ("law_afa", "kum_afa_close", {"kum_afa_open": 1, "afa_gj": 1, "afa_abgang": -1, "zuschreibung": -1},
     {"zuschreibung"}),
    ("law_bw", "bw_close", {"ahk_close": 1, "kum_afa_close": -1}, set()),
    ("law_bw_prev", "bw_prev", {"ahk_open": 1, "kum_afa_open": -1}, set()),
    ("law_net", "bw_close", {"bw_prev": 1, "zugang": 1, "umbuchung": 1, "abgang_bw": -1, "afa_gj": -1,
                             "zuschreibung": 1}, {"umbuchung", "zuschreibung"}),
    ("law_net_sap", "bw_close", {"bw_prev": 1, "bew_ahk": 1, "bew_wb": 1, "afa_gj": -1}, set()),
]
TEUR_TOL = 50_000                     # cents per term, as in the corpus checker
BASE = {"asset": "asset", "class": "class_subtotal", "position": "position_subtotal",
        "account": "account_subtotal"}
TOTALS = ("class_subtotal", "account_subtotal", "position_subtotal", "grand_total")


@dataclass
class Identity:
    name: str
    year: int | None
    target: Record
    terms: list[tuple[int, Record]]
    tol: int
    row: bool                         # row law (implied-zero rule applies) vs. sum check

    def records(self) -> list[Record]:
        return [self.target] + [r for _, r in self.terms]

    def holds(self) -> bool:
        diff = self.target.cents - sum(s * r.cents for s, r in self.terms)
        return abs(diff) <= self.tol

    def complete(self) -> bool:
        return all(r.cents is not None and r.status == "ok" for r in self.records())


def _implied_zero(r: Record) -> bool:
    return r.cents == 0 and not (r.raw_text or "").strip()


def _is_ocr(r: Record) -> bool:
    return isinstance(r.pointer, dict) and r.pointer.get("origin") == "ocr"


def _tol(ex: Extraction, n: int) -> int:
    return TEUR_TOL * n if ex.unit_scale == 1000 else 0


def _rows(ex: Extraction) -> dict[tuple, dict[str, Record]]:
    rows: dict[tuple, dict[str, Record]] = {}
    for r in sorted(ex.records, key=lambda r: r.row_id):
        if r.cents is None or r.status != "ok":
            continue
        rows.setdefault((r.year, r.row_id), {}).setdefault(r.field, r)
    return rows


def _row_laws(ex: Extraction, rows: dict[tuple, dict[str, Record]]) -> list[Identity]:
    present = {r.field for r in ex.records}
    out = []
    for (year, _), f in rows.items():
        if next(iter(f.values())).row_kind == "other":
            continue
        for name, target, terms, optional in LAWS:
            if target not in f:
                continue
            if any(t not in f and (t not in optional or t in present) for t in terms):
                continue
            used = [(s, f[t]) for t, s in terms.items() if t in f]
            out.append(Identity(name, year, f[target], used, _tol(ex, len(used) + 1), True))
    return out


def _ranks(seq: list[str], closer: list[bool]) -> dict[str, int] | None:
    """Per-document level order from 'printed total directly follows row' transitions; None on a cycle."""
    kinds = set(seq)
    edges = {(a, b) for a, b, c in zip(seq, seq[1:], closer[1:]) if c and a != b}
    edges |= {("asset", k) for k in kinds if k != "asset"} if "asset" in kinds else set()
    edges |= {(k, "grand_total") for k in kinds if k != "grand_total"} if "grand_total" in kinds else set()
    rank = {k: 0 for k in kinds}
    for _ in range(len(kinds) + 1):
        changed = False
        for a, b in edges:
            if rank[b] <= rank[a]:
                rank[b], changed = rank[a] + 1, True
        if not changed:
            return rank
    return None


def _sums(ex: Extraction, rows: dict[tuple, dict[str, Record]]) -> list[Identity]:
    out = []
    years = sorted({y for y, _ in rows}, key=lambda y: (y is None, y or 0))
    for year in years:
        block = [f for (y, _), f in rows.items() if y == year]
        block = [f for f in block if next(iter(f.values())).row_kind != "other"]
        raw = [next(iter(f.values())).row_kind for f in block]
        kinds = [BASE.get(k, k) for k in raw]
        closer = [k in TOTALS for k in raw]
        if not block or any(k not in TOTALS and k != "asset" for k in kinds):
            continue
        if closer[-1]:
            pass                                    # totals close the rows above them
        elif closer[0]:                             # header-style totals: close the rows below
            block, kinds, closer = block[::-1], kinds[::-1], closer[::-1]
        else:
            continue                                # ambiguous orientation: skip
        rank = _ranks(kinds, closer)
        if rank is None:
            continue
        stack: list[tuple[int, dict[str, Record]]] = []
        for f, k, c in zip(block, kinds, closer):
            kids = []
            if c:
                while stack and stack[-1][0] < rank[k]:
                    kids.append(stack.pop()[1])
            stack.append((rank[k], f))
            if not kids:
                continue
            for field, tgt in f.items():
                terms = [(1, c[field]) for c in kids if field in c]
                if len(terms) == len(kids):
                    out.append(Identity("sum", year, tgt, terms, _tol(ex, len(kids) + 1), False))
    return out


def _where(r: Record) -> str:
    return f"{r.row_label} / {r.field}"


def _open_point(ex: Extraction, ident: Identity, question: str) -> None:
    for y in ex.years:
        if y.year == ident.year or (ident.year is None and len(ex.years) == 1):
            y.open_points.append(OpenPoint(ident.target.tab or "", _where(ident.target), question))
            return


def _fmt(c: int) -> str:
    return f"{c / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _annotate_failure(ex: Extraction, ident: Identity) -> None:
    diff = ident.target.cents - sum(s * r.cents for s, r in ident.terms)
    for r in ident.records():
        if ident.name not in r.checks:
            r.checks.append(ident.name)
    kind = "Summe der Zeilen" if not ident.row else "Zeilenlogik"
    ocr = " (Wert(e) aus OCR)" if any(_is_ocr(r) for r in ident.records()) else ""
    _open_point(ex, ident, f"{ident.name}: {kind} geht nicht auf, Differenz {_fmt(diff)} EUR "
                           f"(Wert wie gedruckt übernommen){ocr}")


def _cross_year(ex: Extraction) -> None:
    by = {}
    for r in ex.records:
        if r.cents is not None and r.status == "ok" and r.year is not None:
            by.setdefault((r.row_label, r.row_kind, r.year, r.field), r)
    for (label, kind, year, field), cur in list(by.items()):
        for f_now, f_prev in (("bw_prev", "bw_close"), ("ahk_open", "ahk_close")):
            prev = by.get((label, kind, year - 1, f_prev))
            if field != f_now or prev is None or abs(cur.cents - prev.cents) <= _tol(ex, 2):
                continue
            name = f"tie_{f_now}"
            for r in (cur, prev):
                if name not in r.checks:
                    r.checks.append(name)
            ident = Identity(name, year, cur, [(1, prev)], 0, False)
            _open_point(ex, ident, f"{name}: {f_now} {year} ≠ {f_prev} {year - 1} (Differenz "
                                   f"{_fmt(cur.cents - prev.cents)} EUR)")


def _run(ex: Extraction) -> None:
    rows = _rows(ex)
    laws = _row_laws(ex, rows)
    try:
        sums = _sums(ex, rows)
    except Exception as exc:  # noqa: BLE001 - sum checks are optional
        sums = []
        ex.notes.append(f"checks: sum checks skipped ({type(exc).__name__}: {exc})")
    idents = laws + sums
    failing = [i for i in idents if not i.holds()]
    for i in failing:
        _annotate_failure(ex, i)
    for i in failing:
        if i.row:
            for r in i.records():
                if r.status == "ok" and _implied_zero(r):
                    r.status, r.flag = "blank_flagged", "implied_zero_unverified: leere Zelle in nicht " \
                                                        f"aufgehender Zeilenlogik ({i.name})"
    holding = [i for i in idents if i.holds()]
    while True:
        supported = {id(r) for i in holding if i.complete() for r in i.records()}
        drop = [r for r in ex.records if r.status == "ok" and r.cents is not None and _is_ocr(r)
                and id(r) not in supported]
        if not drop:
            break
        for r in drop:
            r.status, r.flag = "blank_flagged", "ocr_unverified: OCR-Wert durch keine aufgehende Rechnung bestätigt"
    _cross_year(ex)


def run_checks(ex: Extraction) -> None:
    """Annotate and gate the records of one extraction in place. Never raises."""
    try:
        _run(ex)
    except Exception as exc:  # noqa: BLE001 - checks must never break a document
        ex.notes.append(f"checks: internal error {type(exc).__name__}: {exc}")
