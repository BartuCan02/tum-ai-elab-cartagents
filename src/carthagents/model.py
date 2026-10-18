"""What the pipeline returns for one document; consumed by the CLI and writers."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date

from .arbeitsversion import OpenPoint, OutRow, Value


@dataclass
class Record:
    """One amount the document prints (or that we abstained on). Goes to the JSON audit trail."""
    year: int | None
    tab: str | None                 # Arbeitsversion tab, None if the row/field has no home there
    row_label: str                  # printed identifier of the row (asset number, class text, "Summe …")
    row_kind: str                   # asset | class | position | class_subtotal | position_subtotal | account_subtotal | grand_total | other
    field: str                      # canonical field name (answer-key vocabulary)
    cents: int | None               # None = blank on purpose, see `flag`
    raw_text: str | None            # the printed characters the value was parsed from
    pointer: dict | None
    status: str                     # "ok" | "blank_flagged" | "not_in_template"
    flag: str | None = None         # reason code + short text when blank or when a check failed
    checks: list[str] = field(default_factory=list)  # failed checks touching this value (value itself unchanged)
    row_id: int = -1                # document-wide row sequence number; records of one printed row share it
    row_text: dict = field(default_factory=dict)  # Feld-ID -> printed text for the row (anlagen_nr, bezeichnung, zeilenart, ...)
    total: bool = False             # the row is a client total ("Summe …"), written at the Summe row of a Gitter tab

    def dump(self) -> dict:
        return asdict(self)


@dataclass
class YearResult:
    year: int
    period_end: date | None
    rows: list[OutRow] = field(default_factory=list)   # filled by assemble() from the records after checks
    open_points: list[OpenPoint] = field(default_factory=list)


@dataclass
class Extraction:
    source: str
    format: str
    years: list[YearResult]
    records: list[Record]
    notes: list[str] = field(default_factory=list)
    unit_scale: int | None = 1      # 1 = EUR, 1000 = TEUR (cents already scaled to EUR); None = unknown


def assemble(ex: Extraction) -> None:
    """Build each year's workbook rows from the (checked) records, in document row order.

    The interpreter emits records only; checks may still blank values. Only records with a
    tab and status "ok" or "blank_flagged" reach the workbook.
    """
    by_year = {y.year: y for y in ex.years}
    rows: dict[tuple[int, int], OutRow] = {}
    for r in sorted(ex.records, key=lambda r: r.row_id):
        if r.tab is None or r.year not in by_year or r.status == "not_in_template":
            continue
        row = rows.get((r.year, r.row_id))
        if row is None:
            row = rows[(r.year, r.row_id)] = OutRow(r.tab, {}, dict(r.row_text), total=r.total)
            by_year[r.year].rows.append(row)
        if r.status == "ok":
            row.values[r.field] = Value(r.cents, r.pointer)
        else:
            row.values[r.field] = Value(None, r.pointer, r.flag or "blank")
