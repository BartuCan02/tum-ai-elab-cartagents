"""Batch entrypoint: one JSON per document, failures recorded not fatal, unique stems,
workbooks named <stem>_<year>_Arbeitsversion.xlsx."""
import json
import os
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import openpyxl
import pytest

from carthagents import cli, pipeline
from carthagents.arbeitsversion import GITTER_FIRST, GITTER_KLASSE, OutRow, Value, amount_columns, output_name
from carthagents.model import Extraction, Record, YearResult

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "data" / "A3-Hackathon-Corpus"
needs_corpus = pytest.mark.skipif(not CORPUS.is_dir(), reason="sample corpus not present")


def _run_cli(*args):
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    return subprocess.run([sys.executable, "-m", "carthagents", *map(str, args)],
                          capture_output=True, text=True, env=env, timeout=600)


def _audit(out, stem):
    return json.loads((out / f"{stem}_audit.json").read_text(encoding="utf-8"))


@needs_corpus
def test_cli_on_sample_documents_and_a_corrupt_file(tmp_path):
    inp, out = tmp_path / "in", tmp_path / "out"
    inp.mkdir()
    xlsx = next(CORPUS.rglob("f0*_*.xlsx"))
    pdf = next(CORPUS.rglob("f0*_*.pdf"))
    shutil.copy(xlsx, inp)
    shutil.copy(pdf, inp)
    (inp / "x.pdf").write_bytes(os.urandom(2048))
    (inp / "~$lock.xlsx").write_bytes(b"junk")
    (inp / ".hidden.pdf").write_bytes(b"junk")
    r = _run_cli("--input", inp, "--output", out, "--jobs", "2", "--no-ocr")
    assert r.returncode == 0, r.stderr
    for stem in (xlsx.stem, pdf.stem, "x"):
        a = _audit(out, stem)
        assert set(a["summary"]) >= {"ok", "blank_flagged", "not_in_template"}
        assert a["tool"]["name"] == "carthagents" and a["run_at"]
        for name in a["workbooks"].values():
            assert (out / name).is_file()
    bad = _audit(out, "x")
    assert bad["workbooks"] == {} and len(bad["notes"]) >= 2  # the reader's/pipeline's error + "no fiscal year"
    assert not (out / "~$lock_audit.json").exists() and not (out / ".hidden_audit.json").exists()
    assert "done: 3 document(s)" in r.stdout


def test_corrupt_file_does_not_stop_batch(tmp_path):
    inp, out = tmp_path / "in", tmp_path / "out"
    inp.mkdir()
    (inp / "x.pdf").write_bytes(os.urandom(1024))
    (inp / "y.xlsx").write_bytes(os.urandom(1024))
    assert cli.main(["--input", str(inp), "--output", str(out), "--jobs", "1", "--no-ocr"]) == 0
    for stem in ("x", "y"):
        a = _audit(out, stem)
        assert a["years"] == [] and a["workbooks"] == {}
        assert any("no fiscal year" in n for n in a["notes"])
        assert len(a["notes"]) >= 2  # plus the reader's or pipeline's own error note


def test_pipeline_never_raises(tmp_path, monkeypatch):
    def boom(path, ocr=True):
        raise RuntimeError("kaputt")

    monkeypatch.setattr("carthagents.readers.load_document", boom)
    ex = pipeline.extract(tmp_path / "a.pdf")
    assert ex.years == [] and ex.records == [] and ex.format == "pdf"
    assert ex.notes[0].startswith("error during read: RuntimeError: kaputt")
    assert pipeline.extract(tmp_path / "missing.pdf").years == []  # real reader, missing file


def test_usage_errors(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert cli.main(["--input", str(empty), "--output", str(tmp_path / "o")]) == 1
    with pytest.raises(SystemExit) as e:
        cli.main(["--input", str(tmp_path / "nope"), "--output", str(tmp_path / "o")])
    assert e.value.code != 0


def test_stem_collisions(tmp_path):
    files = ["a.pdf", "sub/a.pdf", "sub/deeper/a.xlsx", "b.pdf", "b.xlsx", "c.pdf", "Sub2/C.PDF"]
    for f in files:
        (tmp_path / f).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / f).write_bytes(b"")
    paths = cli.find_documents(tmp_path)
    stems = {p.relative_to(tmp_path).as_posix(): s for p, s in cli.assign_stems(tmp_path, paths).items()}
    assert stems == {"a.pdf": "a", "sub/a.pdf": "sub__a", "sub/deeper/a.xlsx": "sub__deeper__a",
                     "b.pdf": "b_pdf", "b.xlsx": "b_xlsx", "c.pdf": "c", "Sub2/C.PDF": "Sub2__C"}
    assert len(set(stems.values())) == len(stems)


def test_unique_stem_keeps_file_stem(tmp_path):
    (tmp_path / "f01").mkdir()
    (tmp_path / "f01" / "f01_doc_01.pdf").write_bytes(b"")
    paths = cli.find_documents(tmp_path)
    assert cli.assign_stems(tmp_path, paths) == {paths[0]: "f01_doc_01"}


def test_workbook_written_with_year_name(tmp_path, monkeypatch):
    pointer = {"sheet": "Tabelle1", "cell": "C8"}

    def fake_interpret(doc):
        row = OutRow(GITTER_KLASSE, {"ahk_open": Value(123456, pointer), "zugang": Value(None, None, "unklar")},
                     text={"bezeichnung": "Maschinen"})
        rec = Record(2024, GITTER_KLASSE, "Maschinen", "class", "ahk_open", 123456, "1.234,56", pointer, "ok")
        return Extraction(doc.path, doc.format, [YearResult(2024, date(2024, 12, 31), [row])], [rec])

    monkeypatch.setattr("carthagents.readers.load_document",
                        lambda path, ocr=True: type("D", (), {"path": str(path), "format": "xlsx", "notes": []})())
    monkeypatch.setattr("carthagents.interpret.interpret", fake_interpret)
    inp, out = tmp_path / "in", tmp_path / "out"
    inp.mkdir()
    (inp / "doc.xlsx").write_bytes(b"")
    assert cli.main(["--input", str(inp), "--output", str(out), "--jobs", "1"]) == 0
    wb_path = out / output_name("doc", 2024)
    assert wb_path.name == "doc_2024_Arbeitsversion.xlsx" and wb_path.is_file()
    ws = openpyxl.load_workbook(wb_path)[GITTER_KLASSE]
    cell = ws[f"{amount_columns(GITTER_KLASSE)['ahk_open']}{GITTER_FIRST}"]
    assert cell.value == 1234.56 and "Tabelle1" in cell.comment.text
    a = _audit(out, "doc")
    assert a["workbooks"] == {"2024": wb_path.name} and a["years"] == [2024]
    assert a["summary"]["ok"] == 1 and a["records"][0]["cents"] == 123456
