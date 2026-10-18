"""Readers: document file -> grid.Document. Dispatch only; see xlsx.py, pdf.py, ocr.py."""
from __future__ import annotations

from pathlib import Path

from ..grid import Document


def load_document(path: Path, ocr: bool = True) -> Document:
    suffix = Path(path).suffix.lower()
    if suffix == ".xlsx":
        from .xlsx import read_xlsx
        return read_xlsx(Path(path))
    if suffix == ".pdf":
        from .pdf import read_pdf
        return read_pdf(Path(path), ocr=ocr)
    raise ValueError(f"unsupported file type: {suffix}")
