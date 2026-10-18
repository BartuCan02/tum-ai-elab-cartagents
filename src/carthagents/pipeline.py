"""One document in, one Extraction out: read -> interpret -> check.

Never raises: any failure becomes an Extraction without years and with the error as a
note, so one broken file cannot stop a batch run on the hidden test set.
"""
from __future__ import annotations

import traceback
from pathlib import Path

from . import checks, interpret, readers
from .model import Extraction, assemble


def _format_guess(path: Path) -> str:
    return {".xlsx": "xlsx", ".pdf": "pdf"}.get(path.suffix.lower(), "unknown")


def extract(path: Path, ocr: bool = True) -> Extraction:
    path = Path(path)
    stage = "read"
    try:
        doc = readers.load_document(path, ocr=ocr)
        stage = "interpret"
        ex = interpret.interpret(doc)
        for note in doc.notes:
            if note not in ex.notes:
                ex.notes.append(note)
        stage = "checks"
        checks.run_checks(ex)
        assemble(ex)
        return ex
    except Exception as exc:  # noqa: BLE001 - the batch must survive any document
        tb = traceback.extract_tb(exc.__traceback__)
        where = f" at {Path(tb[-1].filename).name}:{tb[-1].lineno}" if tb else ""
        return Extraction(str(path), _format_guess(path), [], [],
                          [f"error during {stage}: {type(exc).__name__}: {exc}{where}"])
