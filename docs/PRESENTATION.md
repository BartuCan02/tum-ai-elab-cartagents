# CarthAgents — 1-minute pitch

Slides: [`CarthAgents-pitch.pdf`](CarthAgents-pitch.pdf) (editable deck: https://claude.ai/artifact/93iv3ZBmx2WuDT9FDcRjcN, private until shared).

## 1 · The problem (0:00–0:15)
**Slide:** "Every figure typed by hand. Every audit." — excerpts of a text PDF, an SAP print and a scan.

> Every German audit checks fixed assets. Clients hand over their schedules as PDFs, SAP prints and scans — in a new
> layout every time — and today an assistant types every single number into the auditor's working file, the
> Arbeitsversion, by hand. One typo, and the audit stands on it. CarthAgents automates exactly that.

## 2 · The rule: deterministic (0:15–0:30)
**Slide:** "A wrong number and the audit fails." / "So purely deterministic code extracts every number and matches it to its source."

> Our rule is: deterministic. In an audit, one wrong number and the audit fails. So we use purely deterministic code
> to extract the numbers and match each one back to its source — the page and box in the PDF, or the cell in Excel.
> No language model ever writes a figure.

## 3 · How it works (0:30–1:00)
**Slide:** four stages — Read · Understand · Check · Write — and `carthagents --input docs --output out`.

> It runs in four stages. Read: Excel cells, PDF characters, and OCR for scans. Understand: a strict number grammar
> turns text into exact cents, and a header vocabulary maps columns to fields. Check: accounting laws and subtotal
> sums flag problems — and an OCR value only survives if the arithmetic confirms it. Write: the filled
> Arbeitsversion, one per year, with a pointer on every cell. All of it runs unattended with this one command on any
> folder of documents. Thank you.
