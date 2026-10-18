# CarthAgents — deterministic fixed-asset schedule extraction

Entry for the TUM.ai **CarthAgents** challenge (brief: `TUM-ai-Challenge-Brief-CarthAgents-detailed.pdf`).
The tool reads German fixed-asset schedules (Anlagenspiegel, SAP Anlagengitter, Anlagenverzeichnis), whether they
come as Excel, text PDF, scanned PDF or outline-only PDF. It fills the official **Arbeitsversion** workbook
with every amount the document prints, and each value carries a pointer to where it came from.

## The one command

```bash
carthagents --input <folder with documents> --output <output folder> [--no-ocr] [--jobs N]
# same as: python -m carthagents --input ... --output ...
```

- Recursively processes every `.pdf` / `.xlsx` (skips `~$` lock files and hidden files).
- Runs unattended. If a document fails, the error is recorded in that document's JSON and the batch goes on.
  The exit code is non-zero only for usage errors or an input folder with no documents.
- `--jobs` sets the number of parallel worker processes (default `min(4, cpu)`). `--no-ocr` skips Tesseract.
- Output names use the file stem. If two inputs share a stem (same name in different subfolders), the relative
  folder path is prefixed with `__`, e.g. `sub__a`.

## Outputs (per document)

| File | Content |
|---|---|
| `<stem>_<year>_Arbeitsversion.xlsx` | One per fiscal year in the document. It is a filled copy of `src/carthagents/resources/Arbeitsversion.xlsx` (the template structure is never changed). Each written amount has a **cell comment with its pointer**: `Herkunft: Seite 2, Box x0 y0 x1 y1 (text_layer)` or `Herkunft: 'Sheet'!C8`. Values left blank on purpose are listed on **Offene Punkte** with the reason. |
| `<stem>_audit.json` | Source, detected format, notes and errors, and one record per value (year, tab, row, field, cents, printed text, pointer, status `ok` / `blank_flagged` / `not_in_template`, flag, failed checks). Also summary counts, the names of the workbooks written, the tool version and the run timestamp. |

If no fiscal year can be determined, no workbook is written: an unscorable workbook would be pointless. The JSON
says so, and the run logs it on stderr.

## Evaluation on the sample corpus

```bash
python -m carthagents.evaluate --data data/A3-Hackathon-Corpus --out outputs/eval \
    [--only f01_net_spiegel f04_verzeichnis_03] [--report reports/<name>]
# score existing outputs only:
python -m carthagents.scorer --keys data/A3-Hackathon-Corpus --pred outputs/eval [--report reports/<name>]
```

The sample corpus is not part of the repository: place it at `data/A3-Hackathon-Corpus/` (and the template
reference at `data/Arbeitsversion-merged-layout-20.xlsx`) to run the evaluation and the data-dependent tests.

`evaluate` runs the same pipeline as the CLI on every document that has a `<doc_id>.key.json`, then prints the
scorer tables by family, format and variant. The scorer (`carthagents.scorer`) reads only the Arbeitsversion
workbooks and follows the challenge rules:

- **+1** correct: the value equals the key **and** its pointer comment shows it.
- **0** blank: a key value left empty. It counts as flagged if Offene Punkte names the cell.
- **−3** wrong: a different value, a missing or bad pointer, a value on the wrong tab, a value the document does
  not print, or a row that matches no key row.

Output rows are matched to key rows **through their pointers**, not through names or numbers. For PDFs, the pointer
must lie inside the key row's box. For Excel, it must be the key's cell. Key fields with no column in the template
are reported as "not scored".

## Design rules

1. **No LLM** anywhere in the pipeline. Every figure is copied from printed characters or an Excel cell and parsed
   to exact integer cents by code.
2. **Never guess.** A value is never computed or rebuilt from accounting identities. If the tool is unsure, it
   leaves the cell blank and flags it, because a wrong value costs three times what a correct one earns.
3. **Read, never judge.** A client's broken sum is written as printed. Checks only add flags.
4. **Generic rules.** The test set uses unseen layouts, so the rules are semantic (header vocabulary, number
   grammar, row roles), not tuned to individual layouts.

## Architecture

Folder in, then `pipeline.extract` per document (`readers` → `interpret` → `checks`), then `output.save`.

| Module | Role |
|---|---|
| `grid.py` | Contract between readers and the interpreter: Document → Page → Line → Cell with pointers |
| `readers/` | `xlsx.py`, `pdf.py` (text layer, format detection), `ocr.py` (Tesseract for scans / outline PDFs) |
| `numbers.py`, `columns.py` | Number grammar (exact cents, SAP trailing minus, TEUR); header vocabulary → canonical fields |
| `interpret.py`, `checks.py` | Table interpretation → `model.Extraction`; row/subtotal/cross-year checks (flags only) |
| `pipeline.py`, `cli.py`, `output.py` | Single entrypoint and writers |
| `arbeitsversion.py` | Template field mapping, pointer notation, workbook writer |
| `gt.py`, `scorer.py`, `oracle.py`, `evaluate.py` | Answer keys, scorer, oracle self-test, corpus evaluation |

Decisions and the detailed plan: [`docs/PLAN.md`](docs/PLAN.md).

## Status

| Component | State on the sample corpus |
|---|---|
| Scorer | Oracle (keys written as workbooks) scores 29,610 / 29,610, 0 wrong |
| xlsx reader | Reproduces every key cell (18 workbooks) |
| PDF reader | Finds every printed value in text PDFs except planted cut-off columns; format detection 60/60 |
| OCR | 97–100 % exact on clean scans/outlines; ~1 % misreads at the 0.95 confidence gate (fax scans unreliable) |
| Number grammar | Parses all 29,676 printed amounts; glued numbers split only when exactly one split is valid |
| Column vocabulary | 552 correct / 168 unknown / 0 wrong on the labelled header set (not yet leave-one-family-out) |
| Checks | 0 false flags on perfect documents; 21 / 21 planted broken subtotals detected |
| Interpreter | **Early stage**: asset rows only → 394 correct, **0 wrong**, rest blank (score +394 without OCR) |

Next steps: subtotal/total/class/position rows, SAP block layouts, empty PDF cells as zero with column checks,
header carry-over across pages, glued-number splitting in the interpreter, per-document timeout.

## Setup

Python ≥ 3.10.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[ocr,dev]'
# Tesseract with German data, needed for scanned and outline-only PDFs:
#   macOS:  brew install tesseract tesseract-lang
#   Ubuntu: sudo apt-get install tesseract-ocr tesseract-ocr-deu
pytest -q        # tests that need data/ are skipped when the corpus is absent
```

CI (`.github/workflows/test.yml`) installs Tesseract and runs the test suite.
