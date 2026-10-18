"""PDF -> grid.Document: character-level text layer, cell grouping, rotation, format detection.

Each page is classified as "pdf" (usable text layer), "scan" (raster image, no text),
"outline" (glyphs drawn as vector paths, no text) or empty. Pages without text go to OCR.

Text-layer pages are rebuilt from single characters (rawdict): characters are turned
into an upright reading frame (content drawn rotated on the sheet, e.g. SAP prints that
read bottom-to-top), grouped into lines by baseline and split into cells at horizontal
gaps wider than 1.15 spaces of the font, at vertical rules and where texts overprint.
Cell x0/x1/y0/y1 are upright; the pointer bbox and Cell.chars stay in PyMuPDF's native
page frame (top-left origin, unrotated, /Rotate ignored), which is the frame of the answer
keys. Page.rotation is the content rotation that was undone (90 = text reads bottom to top).
"""
from __future__ import annotations

from bisect import bisect_left
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from statistics import median

import pymupdf

from ..grid import Cell, Document, Line, Page

SPLIT = 1.15       # cell break: gap between glyphs > SPLIT * space width of the font
WORD = 0.5         # word break inside a cell: gap > WORD * space width (or a space glyph)
BASELINE = 0.3     # same line: baselines differ by < BASELINE * font size
MIN_TEXT = 1       # non-space characters needed for a page to count as text layer
MIN_PATHS = 50     # drawings needed for a textless page to count as outline
MIN_IMAGE = 0.3    # image area / page area for a textless page to count as scan

Box = tuple[float, float, float, float]


def read_pdf(path: Path, ocr: bool = True) -> Document:
    """Read a PDF into pages of lines of cells; never raises, problems go to Document.notes."""
    doc = Document(path=str(path), format="pdf", pages=[])
    try:
        pdf = pymupdf.open(str(path))
    except Exception as e:  # noqa: BLE001
        doc.notes.append(f"cannot open PDF: {e}")
        return doc
    kinds = Counter()
    for i, page in enumerate(pdf, start=1):
        try:
            kind = page_kind(page)
        except Exception as e:  # noqa: BLE001
            doc.notes.append(f"page {i}: classification failed: {e}")
            kind = "empty"
        if kind != "empty":
            kinds[kind] += 1
        try:
            if kind == "pdf":
                doc.pages.append(_text_page(page, i))
                if page.rotation:
                    doc.notes.append(f"page {i}: /Rotate {page.rotation} ignored, text read in the unrotated frame")
            elif kind in ("scan", "outline") and ocr:
                doc.pages.append(_ocr(page, i, doc.notes))
            else:
                if kind != "empty":
                    doc.notes.append(f"page {i}: {kind} without text layer, OCR disabled")
                doc.pages.append(Page(index=i, lines=[], origin="ocr" if kind != "empty" else "text_layer",
                                      width=page.rect.width, height=page.rect.height))
        except Exception as e:  # noqa: BLE001
            doc.notes.append(f"page {i}: reading failed: {e}")
            doc.pages.append(Page(index=i, lines=[], origin="text_layer", width=page.rect.width, height=page.rect.height))
    if kinds:
        doc.format = max(kinds, key=lambda k: (kinds[k], k == "pdf"))
    else:
        doc.notes.append("no page with content")
    return doc


def page_kind(page: pymupdf.Page) -> str:
    """'pdf' | 'scan' | 'outline' | 'empty' for one page."""
    if sum(not ch.isspace() for ch in page.get_text("text")) >= MIN_TEXT:
        return "pdf"
    area = abs(page.rect) or 1.0
    img = sum(abs(r & page.rect) for info in page.get_images(full=True) for r in page.get_image_rects(info[0]))
    if img / area >= MIN_IMAGE:
        return "scan"
    if len(page.get_cdrawings()) >= MIN_PATHS:
        return "outline"
    return "empty"


def _ocr(page: pymupdf.Page, i: int, notes: list[str]) -> Page:
    try:
        from .ocr import ocr_page
        return ocr_page(page, i)
    except Exception as e:  # noqa: BLE001  (ImportError, NotImplementedError, Tesseract errors, ...)
        notes.append(f"page {i}: no text layer, OCR failed: {type(e).__name__}: {e}")
        return Page(index=i, lines=[], origin="ocr", width=page.rect.width, height=page.rect.height)


# --- text layer ---------------------------------------------------------------------------
@dataclass(slots=True)
class _G:
    """One glyph: char, native bbox (pointer frame), upright bbox, upright baseline, font size, space width."""
    c: str
    nb: Box
    ub: Box
    base: float
    size: float
    space: float


_TURNS = {(1, 0): 0, (0, -1): 90, (-1, 0): 180, (0, 1): 270}  # reading direction -> content rotation (CCW)


def _upright(b: Box, rot: int, w: float, h: float) -> Box:
    """Native page box -> upright reading frame for content turned `rot` degrees counter-clockwise."""
    x0, y0, x1, y1 = b
    if rot == 90:
        return h - y1, x0, h - y0, x1
    if rot == 180:
        return w - x1, h - y1, w - x0, h - y0
    if rot == 270:
        return y0, w - x1, y1, w - x0
    return b


def _text_page(page: pymupdf.Page, index: int) -> Page:
    raw = page.get_text("rawdict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP)
    w, h = page.cropbox.width, page.cropbox.height  # text coordinates ignore /Rotate: unrotated frame
    spans = [(line["dir"], s) for b in raw["blocks"] for line in b.get("lines", ()) for s in line["spans"]]
    turns = Counter()
    for d, s in spans:
        turns[_TURNS.get((round(d[0]), round(d[1])), 0)] += len(s["chars"])
    rot = turns.most_common(1)[0][0] if turns else 0
    glyphs: list[_G] = []
    for _, s in spans:
        size = s["size"] or 1.0
        ubs = [_upright(tuple(c["bbox"]), rot, w, h) for c in s["chars"]]
        widths = [b[2] - b[0] for c, b in zip(s["chars"], ubs) if not c["c"].isspace()]
        mono = len(widths) > 1 and max(widths) - min(widths) < 0.05 * size
        # MuPDF also emits synthetic space glyphs that fill wide gaps: trust the narrowest one, within bounds
        sp = [b[2] - b[0] for c, b in zip(s["chars"], ubs) if c["c"] == " "]
        space = median(widths) if mono else min(max(min(sp, default=0.28 * size), 0.22 * size), 0.4 * size)
        for c, ub in zip(s["chars"], ubs):
            base = _upright((*c["origin"], *c["origin"]), rot, w, h)[1]
            glyphs.append(_G(c["c"], tuple(c["bbox"]), ub, base, size, space))
    rules = _rulings(page, rot, w, h)
    lines = [_line(g, rules) for g in _lines(glyphs)]
    for ln in lines:
        for c in ln.cells:
            c.pointer["page"] = index
    return Page(index=index, lines=[ln for ln in lines if ln.cells], origin="text_layer",
                width=h if rot in (90, 270) else w, height=w if rot in (90, 270) else h,
                rotation=rot)


def _rulings(page: pymupdf.Page, rot: int, w: float, h: float) -> list[Box]:
    """Vertical rules in the upright frame (stroked lines, rectangle edges, hairline fills), sorted by x."""
    out = []
    try:
        drawings = page.get_drawings()
    except Exception:  # noqa: BLE001
        return out
    for d in drawings:
        stroked = "s" in (d.get("type") or "")
        for it in d["items"]:
            if it[0] == "l":
                b = _upright((min(it[1].x, it[2].x), min(it[1].y, it[2].y), max(it[1].x, it[2].x), max(it[1].y, it[2].y)),
                             rot, w, h)
                if stroked and b[2] - b[0] < 0.5 and b[3] - b[1] > 2:
                    out.append(b)
            elif it[0] == "re":
                b = _upright(tuple(it[1]), rot, w, h)
                if b[2] - b[0] < 1.5 and b[3] - b[1] > 2:
                    out.append(b)
                elif stroked:
                    out += [(b[0], b[1], b[0], b[3]), (b[2], b[1], b[2], b[3])]
    return sorted(out)


def _ruled(rules: list[Box], x0: float, x1: float, y: float) -> bool:
    """A vertical rule crosses height y strictly between x0 and x1."""
    k = bisect_left(rules, (x0,))
    while k < len(rules) and rules[k][0] < x1:
        if rules[k][1] <= y <= rules[k][3]:
            return True
        k += 1
    return False


def _lines(glyphs: list[_G]) -> list[list[_G]]:
    """Group glyphs into lines by baseline, top to bottom; each line keeps the content (drawing) order."""
    order = {id(g): i for i, g in enumerate(glyphs)}
    out: list[list[_G]] = []
    for g in sorted(glyphs, key=lambda g: g.base):
        if out and abs(g.base - out[-1][-1].base) < BASELINE * max(g.size, out[-1][-1].size):
            out[-1].append(g)
        else:
            out.append([g])
    return [sorted(ln, key=lambda g: order[id(g)]) for ln in out]


def _runs(glyphs: list[_G]) -> list[list[_G]]:
    """Content-order runs of glyphs that advance left to right, sorted by start x (overprinted text stays apart)."""
    runs: list[list[_G]] = []
    for g in glyphs:
        if runs and -0.3 * (g.ub[2] - g.ub[0]) <= g.ub[0] - runs[-1][-1].ub[2] <= SPLIT * g.space:
            runs[-1].append(g)
        else:
            runs.append([g])
    return sorted(runs, key=lambda r: r[0].ub[0])


def _line(glyphs: list[_G], rules: list[Box]) -> Line:
    """Split one line into cells at wide gaps, vertical rules and overprints; spaces only mark word gaps."""
    cells: list[list[tuple[_G, bool]]] = []
    cur: list[tuple[_G, bool]] = []
    right, gap = -1e9, False
    y = median(g.base - 0.3 * g.size for g in glyphs)
    for run in _runs(glyphs):
        if cur and run[0].ub[0] < right - 0.3 * (run[0].ub[2] - run[0].ub[0]):  # overprint: another text
            cells.append(cur)
            cur, gap = [], False
        for g in run:
            if g.c.isspace():
                gap = gap or bool(cur)
                right = max(right, g.ub[2]) if cur else right
                continue
            if cur:
                a = cur[-1][0]
                d, space = g.ub[0] - a.ub[2], max(g.space, a.space)
                if d > SPLIT * space or _ruled(rules, a.ub[2] - 0.5, g.ub[0] + 0.5, y):
                    cells.append(cur)
                    cur = []
                elif gap or d > WORD * space:
                    cur[-1] = (a, True)
            gap = False
            cur.append((g, False))
            right = g.ub[2] if len(cur) == 1 else max(right, g.ub[2])
    if cur:
        cells.append(cur)
    made = [_cell(c) for c in cells]
    return Line(cells=made, y0=min((c.y0 for c in made), default=0.0), y1=max((c.y1 for c in made), default=0.0))


def _between(a: Box, b: Box) -> Box:
    """Native box of the gap between two neighbouring glyphs (along x or y, whichever separates them)."""
    u = (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
    if max(b[0] - a[2], a[0] - b[2]) >= max(b[1] - a[3], a[1] - b[3]):
        lo, hi = sorted((min(a[2], b[2]), max(a[0], b[0])))
        return lo, u[1], hi, u[3]
    lo, hi = sorted((min(a[3], b[3]), max(a[1], b[1])))
    return u[0], lo, u[2], hi


def _cell(items: list[tuple[_G, bool]]) -> Cell:
    """One cell from glyphs with 'space follows' marks; chars get a ' ' entry per word gap (len(chars) == len(text))."""
    chars: list[tuple[str, Box]] = []
    for k, (g, sp) in enumerate(items):
        chars.append((g.c, g.nb))
        if sp and k + 1 < len(items):
            chars.append((" ", _between(g.nb, items[k + 1][0].nb)))
    nb = [g.nb for g, _ in items]
    ub = [g.ub for g, _ in items]
    bbox = [round(min(b[0] for b in nb), 2), round(min(b[1] for b in nb), 2),
            round(max(b[2] for b in nb), 2), round(max(b[3] for b in nb), 2)]
    return Cell(text="".join(c for c, _ in chars), pointer={"page": None, "bbox": bbox, "origin": "text_layer"},
                x0=min(b[0] for b in ub), x1=max(b[2] for b in ub), y0=min(b[1] for b in ub), y1=max(b[3] for b in ub),
                chars=chars)
