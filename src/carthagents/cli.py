"""Batch entrypoint: a folder of documents in, per-document outputs out.

    python -m carthagents --input DIR --output DIR [--no-ocr] [--jobs N]

For every .pdf / .xlsx below DIR (recursively): <stem>_<year>_Arbeitsversion.xlsx per
fiscal year and <stem>_audit.json. A document that fails is recorded in its JSON and
the batch goes on; the exit code is non-zero only for usage errors or an empty input.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

SUFFIXES = (".pdf", ".xlsx")


def find_documents(root: Path) -> list[Path]:
    """All .pdf/.xlsx files below root, skipping Office lock files (~$…) and hidden files/folders."""
    root = Path(root)
    out = []
    for p in root.rglob("*"):
        rel = p.relative_to(root).parts
        if any(part.startswith(".") for part in rel) or p.name.startswith("~$"):
            continue
        if p.suffix.lower() in SUFFIXES and p.is_file():
            out.append(p)
    return sorted(out, key=lambda p: p.relative_to(root).as_posix())


def assign_stems(root: Path, paths: list[Path]) -> dict[Path, str]:
    """Output stem per document: the file stem, or — if several documents would share it
    (same name in different folders, or a.pdf next to a.xlsx) — the relative path joined by '__'."""
    root = Path(root)
    by_stem: dict[str, list[Path]] = {}
    for p in paths:
        by_stem.setdefault(p.stem.lower(), []).append(p)
    stems: dict[Path, str] = {}
    for group in by_stem.values():
        for p in group:
            if len(group) == 1:
                stems[p] = p.stem
                continue
            parts = list(p.relative_to(root).parent.parts) + [p.stem]
            if sum(q.relative_to(root).with_suffix("") == p.relative_to(root).with_suffix("") for q in group) > 1:
                parts[-1] += "_" + p.suffix.lower().lstrip(".")
            stems[p] = "__".join(parts)
    return stems


def process(path: Path, out_dir: Path, stem: str, ocr: bool = True) -> dict:
    """Extract and save one document; never raises. Returns a short summary for the progress line."""
    from .model import Extraction
    from .output import save
    from .pipeline import extract

    t0 = time.monotonic()
    ex = extract(path, ocr=ocr)
    try:
        audit = save(ex, out_dir, stem)
    except Exception as exc:  # noqa: BLE001
        audit = save(Extraction(str(path), ex.format, [], [],
                                ex.notes + [f"error during output: {type(exc).__name__}: {exc}"]), out_dir, stem)
    return {"path": str(path), "stem": stem, "format": audit["format"], "workbooks": audit["workbooks"],
            "summary": audit["summary"], "notes": audit["notes"],
            "error": any(n.startswith("error during") for n in audit["notes"]),
            "seconds": round(time.monotonic() - t0, 1)}


def _crashed(path: Path, out_dir: Path, stem: str, exc: BaseException) -> dict:
    """The worker process died (e.g. a native crash): record it in the document's JSON."""
    from .model import Extraction
    from .output import save
    audit = save(Extraction(str(path), path.suffix.lower().lstrip("."), [], [],
                            [f"error during worker: {type(exc).__name__}: {exc}"]), out_dir, stem)
    return {"path": str(path), "stem": stem, "format": audit["format"], "workbooks": {}, "summary": audit["summary"],
            "notes": audit["notes"], "error": True, "seconds": 0.0}


def _line(i: int, n: int, r: dict) -> str:
    s = r["summary"]
    if r["error"]:
        what = "FAILED: " + next(x for x in r["notes"] if x.startswith("error during"))
    elif not r["workbooks"]:
        first = next((x for x in r["notes"] if "no fiscal year" not in x), "")
        what = "no fiscal year found, no workbook" + (f" ({first[:120]})" if first else "")
    else:
        what = (f"{len(r['workbooks'])} workbook(s) {','.join(r['workbooks'])}; "
                f"{s['ok']} ok, {s['blank_flagged']} flagged, {s['not_in_template']} json-only")
    return f"[{i}/{n}] {r['stem']} ({r['format']}, {r['seconds']}s): {what}"


def run(docs: list[tuple[Path, str]], out_dir: Path, ocr: bool = True, jobs: int = 1, log=print) -> list[dict]:
    """Process (path, stem) pairs, in parallel if jobs > 1. Results come back in input order."""
    n = len(docs)
    results: list[dict | None] = [None] * n
    done = 0

    def finish(k: int, r: dict) -> None:
        nonlocal done
        done += 1
        results[k] = r
        log(_line(done, n, r))

    if jobs <= 1 or n <= 1:
        for k, (p, stem) in enumerate(docs):
            finish(k, process(p, out_dir, stem, ocr))
        return results  # type: ignore[return-value]

    pending = list(range(n))
    with ProcessPoolExecutor(max_workers=min(jobs, n)) as pool:
        futs = {k: pool.submit(process, docs[k][0], out_dir, docs[k][1], ocr) for k in pending}
        broken = []
        for k, f in futs.items():
            try:
                finish(k, f.result())
            except BrokenProcessPool:
                broken.append(k)
            except Exception as exc:  # noqa: BLE001
                finish(k, _crashed(*docs[k], exc))
    # a native crash kills the whole pool: retry the affected documents one by one, isolated
    for k in broken:
        p, stem = docs[k]
        with ProcessPoolExecutor(max_workers=1) as solo:
            try:
                finish(k, solo.submit(process, p, out_dir, stem, ocr).result())
            except Exception as exc:  # noqa: BLE001
                finish(k, _crashed(Path(p), out_dir, stem, exc))
    return results  # type: ignore[return-value]


def summary_line(results: list[dict]) -> str:
    wbs = sum(len(r["workbooks"]) for r in results)
    failed = sum(r["error"] for r in results)
    noyear = sum(not r["error"] and not r["workbooks"] for r in results)
    ok = sum(r["summary"]["ok"] for r in results)
    flagged = sum(r["summary"]["blank_flagged"] for r in results)
    return (f"done: {len(results)} document(s), {wbs} workbook(s), {ok} values written, {flagged} flagged blank; "
            f"{failed} failed, {noyear} without fiscal year")


def default_jobs() -> int:
    return max(1, min(4, os.cpu_count() or 1))


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="carthagents",
                                 description="Fill the Arbeitsversion from fixed-asset schedules (PDF / XLSX). "
                                             "No LLM; unsure values are left blank and flagged.")
    ap.add_argument("--input", required=True, type=Path, help="folder with .pdf/.xlsx documents (searched recursively)")
    ap.add_argument("--output", required=True, type=Path, help="folder for the workbooks and audit JSON files")
    ap.add_argument("--no-ocr", action="store_true", help="do not OCR scanned / outline-only PDF pages")
    ap.add_argument("--jobs", type=int, default=default_jobs(),
                    help=f"parallel worker processes (default: min(4, cpu) = {default_jobs()})")
    return ap


def main(argv=None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    if not args.input.is_dir():
        ap.error(f"--input {args.input} is not a directory")
    if args.jobs < 1:
        ap.error("--jobs must be >= 1")
    paths = find_documents(args.input)
    if not paths:
        print(f"no .pdf or .xlsx documents found in {args.input}", file=sys.stderr)
        return 1
    stems = assign_stems(args.input, paths)
    args.output.mkdir(parents=True, exist_ok=True)
    print(f"{len(paths)} document(s) from {args.input} -> {args.output} "
          f"(jobs={args.jobs}, ocr={'off' if args.no_ocr else 'on'})", flush=True)
    t0 = time.monotonic()
    results = run([(p, stems[p]) for p in paths], args.output, ocr=not args.no_ocr, jobs=args.jobs,
                  log=lambda s: print(s, flush=True))
    print(summary_line(results) + f" in {time.monotonic() - t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
