"""Run the pipeline on the sample corpus and score it.

    python -m carthagents.evaluate --data data/A3-Hackathon-Corpus --out outputs/eval \
        [--only f01_net_spiegel f04_verzeichnis_03 ...] [--report reports/<name>] [--jobs N] [--no-ocr]

Every document next to a `<doc_id>.key.json` is processed exactly like the `carthagents`
command does it (output stem = doc_id), then `carthagents.scorer` scores --out.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import scorer
from .cli import SUFFIXES, default_jobs, run, summary_line


def corpus_documents(data: Path, only: list[str] | None = None) -> list[tuple[Path, str]]:
    """(document path, doc_id) for every answer key, filtered by doc_id or family."""
    out = []
    for key in sorted(Path(data).rglob("*.key.json")):
        doc_id = key.name[: -len(".key.json")]
        family = key.parent.name
        if only and doc_id not in only and family not in only:
            continue
        docs = [key.with_name(doc_id + s) for s in SUFFIXES if key.with_name(doc_id + s).is_file()]
        if docs:
            out.append((docs[0], doc_id))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run carthagents on the sample corpus and score the outputs")
    ap.add_argument("--data", type=Path, default=Path("data/A3-Hackathon-Corpus"), help="corpus with *.key.json")
    ap.add_argument("--out", type=Path, default=Path("outputs/eval"), help="output folder (old outputs in it are removed first)")
    ap.add_argument("--only", nargs="*", help="only these doc_ids or families (folder names)")
    ap.add_argument("--report", type=Path, help="write summary.json and outcomes.csv here")
    ap.add_argument("--jobs", type=int, default=default_jobs())
    ap.add_argument("--no-ocr", action="store_true")
    ap.add_argument("--score-only", action="store_true", help="skip extraction, score what is in --out")
    args = ap.parse_args(argv)
    docs = corpus_documents(args.data, args.only)
    if not docs:
        print(f"no documents with answer keys found in {args.data}", file=sys.stderr)
        return 1
    if not args.score_only:
        if args.out.is_dir():
            # stale workbooks from an earlier run would be scored as if they were new
            for p in [*args.out.glob("*_Arbeitsversion.xlsx"), *args.out.glob("*_audit.json")]:
                p.unlink()
        args.out.mkdir(parents=True, exist_ok=True)
        results = run(docs, args.out, ocr=not args.no_ocr, jobs=args.jobs, log=lambda s: print(s, flush=True))
        print(summary_line(results), end="\n\n", flush=True)
    sargs = ["--keys", str(args.data), "--pred", str(args.out)]
    if args.only:
        sargs += ["--only", *args.only]
    if args.report:
        sargs += ["--report", str(args.report)]
    return scorer.main(sargs)


if __name__ == "__main__":
    raise SystemExit(main())
