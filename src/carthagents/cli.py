import argparse
import sys
from pathlib import Path
from .reader import load_tokens
from .extract import infer_entries, checks, Entry
from .output import save

def main(argv=None):
    parser=argparse.ArgumentParser(description="Evidence-first CarthAgents batch extractor")
    parser.add_argument("--input",required=True,help="Input directory containing PDF / XLSX documents")
    parser.add_argument("--output",required=True,help="Output directory")
    parser.add_argument("--ocr",action="store_true",help="Enable optional Tesseract OCR fallback")
    args=parser.parse_args(argv)
    root=Path(args.input)
    if not root.is_dir():parser.error("--input must be an existing directory")
    paths=sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in (".pdf",".xlsx") and not p.name.startswith("~$"))
    if not paths:
        print("No PDF or XLSX files found",file=sys.stderr);return 1
    for path in paths:
        print("Processing",path.name,flush=True)
        try:
            entries=infer_entries(load_tokens(path,args.ocr))
            issues=checks(entries)
        except Exception as exc:
            entries=[Entry("unknown","","","_review",None,None,None,"blank",f"Extraction error: {type(exc).__name__}: {exc}")]
            issues=[]
        save(path,entries,issues,Path(args.output))
        print(f"  mapped={sum(x.status=='ok' for x in entries)}, flagged={sum(x.status!='ok' for x in entries)}, checks={len(issues)}",flush=True)
    return 0

if __name__=="__main__":raise SystemExit(main())
