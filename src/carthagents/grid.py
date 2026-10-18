"""Shared contract between readers and the table interpreter.

Every reader (xlsx, PDF text layer, OCR) turns a document into the same structure:
pages of lines of cells. A cell is one visually contiguous piece of printed text
with its exact pointer. Readers never interpret meaning (no fields, no amounts);
they only report what is printed and where.

Coordinates: PDF/OCR cell x0/x1/y0/y1 use PDF points in an upright reading frame,
origin top-left (y grows downwards). Pointer bboxes and char boxes stay in PyMuPDF's
native page frame, which is the frame the answer keys use (differs on rotated content). xlsx uses column
numbers as x (cell in column C spans x0=2, x1=3) and row numbers as y.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class Cell:
    text: str                       # printed text exactly as shown (whitespace-trimmed)
    pointer: dict                   # {"sheet": str, "cell": "C8"}  or  {"page": int, "bbox": [x0,y0,x1,y1], "origin": str}
    x0: float
    x1: float
    y0: float
    y1: float
    number: Decimal | None = None   # xlsx only: exact numeric cell value (cached result for formulas); None for text cells
    formula: str | None = None      # xlsx only: the formula text if the cell holds one
    hidden: bool = False            # xlsx only: cell is in a hidden row or column
    conf: float | None = None       # OCR only: minimum character confidence 0..1
    chars: list[tuple[str, tuple[float, float, float, float]]] | None = None  # PDF/OCR: per-character boxes in the POINTER frame, len == len(text) (word gaps are ' ' entries)


@dataclass
class Line:
    cells: list[Cell]               # left to right
    y0: float
    y1: float
    row: int | None = None          # xlsx: sheet row number
    hidden: bool = False            # xlsx: row is hidden


@dataclass
class Page:
    index: int                      # 1-based page number (PDF) or sheet position (xlsx)
    lines: list[Line]               # top to bottom
    origin: str                     # "excel_cell" | "text_layer" | "ocr"
    sheet: str | None = None        # xlsx sheet name
    width: float = 0.0
    height: float = 0.0
    rotation: int = 0               # content rotation undone for the upright frame (0/90/180/270); not the /Rotate value
    hidden_columns: frozenset[int] = frozenset()  # xlsx: hidden column numbers

    def address(self, col: int, row: int) -> dict:
        """Pointer for an (empty) xlsx cell, e.g. an empty amount cell that counts as zero."""
        from openpyxl.utils import get_column_letter
        return {"sheet": self.sheet, "cell": f"{get_column_letter(col)}{row}"}


@dataclass
class Document:
    path: str
    format: str                     # "xlsx" | "pdf" (text layer) | "scan" | "outline"
    pages: list[Page]
    notes: list[str] = field(default_factory=list)  # reader-level problems (e.g. "page 3: no text, OCR failed")
