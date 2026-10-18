# CarthAgents — implementation plan and decisions

Challenge brief: `TUM-ai-Challenge-Brief-CarthAgents-detailed.pdf` (repo root). Sample corpus with answer keys:
`data/A3-Hackathon-Corpus/fNN_*/` (72 docs: xlsx, text PDF, scan, outline PDF; each with `<doc>.key.json`).
The keys were written by the generator together with the documents and are exact ground truth.
Template: `src/carthagents/resources/Arbeitsversion.xlsx` (byte-identical copy of `data/Arbeitsversion-merged-layout-20.xlsx`).

## Hard rules
1. **Pure code, no LLM** anywhere in the pipeline.
2. Every value is an exact copy of printed characters / an Excel cell, parsed by code. Never computed, never rebuilt
   from accounting identities (e.g. truncated columns, implied transfers) — abstain instead.
3. **Unsure → blank + flag.** Scoring is +1 correct, 0 blank, −3 wrong. Precision beats coverage.
4. Few per-layout rules: the hidden test set has unseen layouts. Rules must be semantic/generic.
5. Read, never judge: a client's broken sum is reported as printed and flagged.
6. Never modify the template's structure. Never modify files under `data/`.

## Decisions (from the user)
- One output workbook per document **and fiscal year**: `<stem>_<year>_Arbeitsversion.xlsx`, plus one `<stem>_audit.json` per document.
- Only the Arbeitsversion workbook is scored (`carthagents.scorer`). The JSON is the audit trail.
- Key fields/rows without a home in the template are ignored for scoring: account-level rows/subtotals, carry-over lines,
  `abgang_bw`. Asset-level `afa_gj` goes to JSON only (Verzeichnis only has planmäßig/außerplanmäßig columns).
- Pointers live in cell comments (`arbeitsversion.format_pointer`). PDF pointer = page + bbox of the printed characters,
  in the same coordinate frame as the answer keys. Excel pointer = sheet + cell.

## Canonical vocabulary (answer-key field names)
Amounts: `ahk_open, zugang, abgang, umbuchung, ahk_close, kum_afa_open, afa_gj, afa_abgang, zuschreibung,
kum_afa_close, bw_close, bw_prev, abgang_bw` and net movements `bew_ahk, bew_wb` (SAP summary layouts).
Keys hold **magnitudes** (SAP `1.234,56-` → 123456) except `umbuchung`, `bew_ahk`, `bew_wb`, which keep their sign.
TEUR documents: key holds EUR cents (`49` TEUR → 4 900 000).
Row kinds: asset, class rows, Bilanzposition rows, class/position/account subtotals, grand totals, carry-over.

## Architecture (module ownership)
| Module | Role |
|---|---|
| `grid.py` | **Contract**: Document → Page → Line → Cell (text, pointer, geometry, xlsx number/formula/hidden, OCR conf, char boxes) |
| `readers/xlsx.py` | xlsx → grid (cached values + formulas, hidden rows/cols, numbers-as-text left as text) |
| `readers/pdf.py` | PDF → grid; char-level text layer, cell grouping, rotation, format detection (pdf/scan/outline), calls OCR |
| `readers/ocr.py` | raster/outline page → grid via Tesseract, with confidences and char boxes |
| `numbers.py` | number grammar: style detection, exact parse to cents, glued-number splitting, units |
| `columns.py` | header vocabulary: column header texts → canonical field (or role / ignore / unknown) |
| `interpret.py` | phase 2: header band, column model, row roles, values → `model.Extraction` |
| `checks.py` | phase 2: row laws, subtotal sums, cross-year ties → flags only |
| `pipeline.py`, `cli.py`, `output.py` | single entrypoint: folder in → per-doc per-year workbooks + JSON |
| `arbeitsversion.py`, `gt.py`, `scorer.py`, `oracle.py` | template writer, ground truth, scorer (done) |

## Evaluation
`python -m carthagents.scorer --keys data/A3-Hackathon-Corpus --pred <outdir> --report reports/<name>`
Primary metric later: leave-one-family-out. CI rule: wrong count must not increase.
