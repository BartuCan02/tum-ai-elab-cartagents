# CarthAgents — Evidence-first Audit Agent (starter)

A conservative Python baseline for the **TUM.ai CarthAgents Challenge**. It extracts source-backed numeric entries from German asset schedules and writes an editable workbook and a JSON audit trail for every input file.

> **Important:** This is a starter baseline, **not a proven zero-hallucination challenge solution**. The actual Arbeitsversion template, output schema, answer-key samples, and scorer were not supplied. The generated workbook is a provisional schema and cannot be guaranteed to match the organizers' grader. Multiline headers, complex SAP 3–4-line blocks, scanned/vector-only PDFs, thousands of EUR, and layout ambiguity require additional work before serious leaderboard use.

## Quickstart

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
python -m carthagents --input examples/input --output outputs
pytest -q
```

For scanned PDFs, install system Tesseract and optional packages:

```bash
# macOS: brew install tesseract tesseract-lang
# Ubuntu: sudo apt-get install tesseract-ocr tesseract-ocr-deu
pip install -e '.[ocr,dev]'
python -m carthagents --input examples/input --output outputs --ocr
```

Every document produces `<name>_Arbeitsversion.xlsx` and `<name>_audit.json`.

## Design

1. **Deterministic source readers**: PyMuPDF words + page bounding boxes; Excel cells extracted via ZIP/XML, with exact sheet/cell pointers.
2. **Column interpretation**: cautious keyword-based mapping for recognizable single-cell/word headers. No LLM required. Unknown layouts result in review flags, not guesses.
3. **Exact numbers**: `Decimal` parses copied source tokens into integer cents. No LLM or generative system writes numerical values.
4. **Validation**: optional rollforward consistency check flags contradictory source values without changing them.
5. **Outputs**: workbook with `Anlagenverzeichnis`, `Anlagengitter`, totals and review tabs; JSON record list containing source pointer and status.

A word-level PDF bbox identifies printed token location. Excel pointers identify original cells. The JSON `value_cents` field is an integer. Source currencies and 'thousands' scale are NOT inferred in this baseline.

## Known limitations / next steps

- **Official mapping**: import the official Arbeitsversion and field schema once provided. Current field names and output workbook are illustrative.
- **Data**: obtain the 72 synthetic sample hand-overs, answer keys and scorer described in the brief; measure precision and coverage by format and family.
- **PDF tables**: add line/column region inference for multi-word and stacked headers; merge OCR words for cells while preserving exact boxes.
- **SAP blocks**: add block-of-rows interpretation and detection of thousand-EUR scale and trailing minus signs.
- **Blank amounts**: distinguish printed empty numeric cells (which the brief counts as zero) from unknown/unmapped fields.
- **OCR**: default OCR is a fallback for pages without selectable words and is not yet robust to vector-outline PDFs.
- **Confidence**: confidence must come from evidence checks, not model confidence; favour abstention given +1/0/-3 scoring.
- **File collisions**: two files with the same stem in different input subdirectories will overwrite one another in a single output folder.

## GitHub

```bash
git init
git add .
git commit -m "Initial CarthAgents evidence-first audit agent"
git branch -M main
gh repo create carthagents --private --source=. --remote=origin --push
```

Or create a repository on GitHub first, then:

```bash
git remote add origin https://github.com/YOUR_USERNAME/carthagents.git
git push -u origin main
```

The repository intentionally ignores output data and `.env` files. Never commit client data or API keys.
