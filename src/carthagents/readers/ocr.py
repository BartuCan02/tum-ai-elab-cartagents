"""Raster / outline PDF page -> grid.Page via Tesseract (hOCR with per-character boxes and confidences).

Steps: render the page grey at `dpi`; find the content orientation (text-line axis by projection
sharpness, then 0 vs 180 by a Tesseract confidence vote) and turn it upright; estimate and undo the
skew (projection profile); run Tesseract once on the page (hOCR, char boxes); split each Tesseract
line into cells at wide gaps; re-read all numeric-looking cells in a second, digits-only pass and
set the confidence to 0 where the two reads disagree.

Coordinates: Cell x0/x1/y0/y1 are in the upright reading frame (points). Pointer bbox and
Cell.chars are in the *print frame* = the frame of the answer keys: PyMuPDF's native page frame,
except for scanned pages the scanner evidently turned (content upside down, or width/height swapped
against the document's majority). Those are turned back assuming the print had its content at
0 or 90 degrees CCW (SAP prints read bottom-to-top). Page.rotation is the print-frame content
rotation that was normalised away. Char boxes come from Tesseract's LSTM; where they are
inconsistent the word box is split evenly per character.
"""
from __future__ import annotations

import io
import math
import re
import subprocess
import xml.etree.ElementTree as ET
from statistics import median

import numpy as np
import pymupdf
from PIL import Image

from ..grid import Cell, Line, Page

TESSERACT = "/opt/homebrew/bin/tesseract"
LANG = "deu"
PSM = "6"
SPLIT = 1.0           # cell break: horizontal gap > SPLIT * median character width of the line
NUMERIC = re.compile(r"^[-+]?[\d.,'\s]*\d[\d.,'\s]*[-+]?$")
NUM_WHITELIST = "0123456789.,-"
MAX_SKEW = 3.0        # degrees searched for deskew
TIMEOUT = 60          # seconds per Tesseract call
HALO = 2              # px grown around detected rules before whitening them
NS = "{http://www.w3.org/1999/xhtml}"

Box = tuple[float, float, float, float]
Word = dict           # {"text", "box", "conf", "chars": [(ch, box)]}


def ocr_page(page: pymupdf.Page, index: int, dpi: int = 300) -> Page:
    """OCR one page into lines of cells (origin "ocr"); never raises: on any failure an empty Page."""
    try:
        return _ocr_page(page, index, dpi)
    except Exception:  # noqa: BLE001  (Tesseract missing / timeout / malformed output)
        return Page(index=index, lines=[], origin="ocr", width=page.rect.width, height=page.rect.height)


def _ocr_page(page: pymupdf.Page, index: int, dpi: int) -> Page:
    scale = dpi / 72.0
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY, alpha=False)
    native = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    rot = _orientation(native)                       # content turned `rot` degrees CCW in the native image
    up = native.rotate(-rot, expand=True) if rot else native
    skew = _skew(up)
    if abs(skew) > 0.03:
        up = up.rotate(skew, resample=Image.BICUBIC, fillcolor=255)
    up = _remove_rules(up, scale)
    turn = _print_turn(page, rot) if _is_raster(page) else 0
    pw, ph = page.rect.width, page.rect.height

    def to_print(b: Box) -> tuple[float, float, float, float]:
        corners = ((b[0], b[1]), (b[2], b[1]), (b[0], b[3]), (b[2], b[3]))
        pts = [_turn_back(*[v / scale for v in _to_native(x, y, up.size, skew, rot, native.size)], turn, pw, ph)
               for x, y in corners]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return round(min(xs), 2), round(min(ys), 2), round(max(xs), 2), round(max(ys), 2)

    lines = _cells(_hocr_words(_tesseract(up, PSM, "hocr", "-c", "hocr_char_boxes=1")))
    _numeric_pass(up, [c for ln in lines for c in ln])
    out: list[Line] = []
    for ln in lines:
        cells = [Cell(text=c["text"], pointer={"page": index, "bbox": list(to_print(c["box"])), "origin": "ocr"},
                      x0=c["box"][0] / scale, x1=c["box"][2] / scale, y0=c["box"][1] / scale, y1=c["box"][3] / scale,
                      conf=round(c["conf"], 4), chars=[(ch, to_print(b)) for ch, b in c["chars"]]) for c in ln]
        out.append(Line(cells=cells, y0=min(c.y0 for c in cells), y1=max(c.y1 for c in cells)))
    out.sort(key=lambda ln: (ln.y0 + ln.y1) / 2)
    return Page(index=index, lines=out, origin="ocr", width=up.width / scale, height=up.height / scale,
                rotation=(rot - turn + page.rotation) % 360)


# --- orientation, skew, frames ----------------------------------------------------------------

def _ink(img: Image.Image, dpi_small: float = 50.0, dpi: float | None = None) -> np.ndarray:
    """Boolean ink mask of a downscaled copy (about 50 dpi for a 300 dpi render)."""
    f = max(1, round(img.width / 600))
    a = np.asarray(img.reduce(f) if f > 1 else img, dtype=np.uint8)
    return a < min(128, int(a.mean()) - 40)


def _sharpness(ink: np.ndarray, max_deg: float = MAX_SKEW, step: float = 0.1) -> tuple[float, float]:
    """Best (score, angle) of the row-projection profile over shears; text lines give sharp profiles."""
    ys, xs = np.nonzero(ink)
    if len(xs) < 50:
        return 0.0, 0.0
    xs = xs - ink.shape[1] / 2
    best = (-1.0, 0.0)
    for deg in np.arange(-max_deg, max_deg + 1e-9, step):
        h = np.bincount(np.round(ys - xs * math.tan(math.radians(deg))).astype(int) + ink.shape[1], minlength=1)
        s = float((h.astype(np.float64) ** 2).sum()) / len(xs) ** 2
        best = max(best, (s, float(deg)))
    return best


def _orientation(img: Image.Image) -> int:
    """Content rotation in degrees CCW (0/90/180/270) by a Tesseract confidence vote on a 150 dpi copy."""
    small = img.reduce(2) if img.width > 2000 or img.height > 2000 else img
    scores = []
    for r in (0, 90, 180, 270):
        tsv = _tesseract(small.rotate(-r, expand=True), PSM, "tsv")
        conf = [float(row[10]) for row in (ln.split("\t") for ln in tsv.splitlines()[1:])
                if len(row) > 11 and len(row[11].strip()) >= 2 and float(row[10]) > 60]
        scores.append(sum(conf))
    return (0, 90, 180, 270)[scores.index(max(scores))]


def _skew(img: Image.Image) -> float:
    """Angle (degrees CCW) that makes text lines horizontal."""
    ink = _ink(img)
    _, coarse = _sharpness(ink)
    best = (-1.0, coarse)
    ys, xs = np.nonzero(ink)
    for deg in np.arange(coarse - 0.1, coarse + 0.1001, 0.02):
        h = np.bincount(np.round(ys - (xs - ink.shape[1] / 2) * math.tan(math.radians(deg))).astype(int) + ink.shape[1])
        best = max(best, (float((h.astype(np.float64) ** 2).sum()), float(deg)))
    return best[1]


def _to_native(x: float, y: float, size: tuple[int, int], skew: float, rot: int, native: tuple[int, int]) -> tuple[float, float]:
    """Point in the upright deskewed image -> point in the native render (pixels)."""
    cx, cy = size[0] / 2, size[1] / 2
    a = math.radians(skew)                 # image was turned `skew` CCW about its centre; turn the point back
    dx, dy = x - cx, y - cy
    u, v = cx + dx * math.cos(a) - dy * math.sin(a), cy + dx * math.sin(a) + dy * math.cos(a)
    w, h = native
    if rot == 90:
        return v, h - u
    if rot == 180:
        return w - u, h - v
    if rot == 270:
        return w - v, u
    return u, v


def _is_raster(page: pymupdf.Page) -> bool:
    area = abs(page.rect) or 1.0
    return sum(abs(r & page.rect) for info in page.get_images(full=True) for r in page.get_image_rects(info[0])) / area > 0.3


def _print_turn(page: pymupdf.Page, rot: int) -> int:
    """Degrees CCW the scanner turned this page, chosen so the print had its content at 0 or 90 degrees."""
    landscape = page.rect.width > page.rect.height
    doc = page.parent
    votes = [p.rect.width > p.rect.height for p in doc] if doc is not None else [landscape]
    swapped = landscape != (sum(votes) * 2 > len(votes)) if sum(votes) * 2 != len(votes) else False
    options = (90, 270) if swapped else (0, 180)
    return next(t for t in options if (rot - t) % 360 in (0, 90))


def _turn_back(x: float, y: float, turn: int, w: float, h: float) -> tuple[float, float]:
    """Native page point (page size w x h) -> print frame of a page the scanner turned `turn` degrees CCW."""
    if turn == 90:
        return h - y, x
    if turn == 180:
        return w - x, h - y
    if turn == 270:
        return y, w - x
    return x, y


def _runs(dark: np.ndarray, length: int) -> np.ndarray:
    """Pixels on horizontal dark runs of at least `length` (1-D morphological opening along axis 1)."""
    c = np.pad(np.cumsum(dark, axis=1, dtype=np.int32), ((0, 0), (1, 0)))
    full = (c[:, length:] - c[:, :-length]) == length              # window starting here is all dark
    full = np.pad(full, ((0, 0), (0, dark.shape[1] - full.shape[1])))
    s = np.pad(np.cumsum(full, axis=1, dtype=np.int32), ((0, 0), (length, 0)))
    return (s[:, length:] - s[:, :-length]) > 0                     # covered by some full window


def _remove_rules(img: Image.Image, scale: float) -> Image.Image:
    """Paint table rules (long horizontal / vertical dark runs) white so Tesseract does not read them as |[_."""
    a = np.asarray(img, dtype=np.uint8).copy()
    dark = a < 170
    rules = _runs(dark, int(30 * scale)) | _runs(dark.T, int(16 * scale)).T
    if not rules.any():
        return img
    for _ in range(HALO):                                           # halo for blurred / anti-aliased edges
        g = rules.copy()
        g[1:] |= rules[:-1]
        g[:-1] |= rules[1:]
        g[:, 1:] |= rules[:, :-1]
        g[:, :-1] |= rules[:, 1:]
        rules = g
    a[rules] = 255
    return Image.fromarray(a)


# --- Tesseract --------------------------------------------------------------------------------

def _tesseract(img: Image.Image, psm: str, *config: str) -> str:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    res = subprocess.run([TESSERACT, "stdin", "stdout", "-l", LANG, "--psm", psm, *config],
                         input=buf.getvalue(), capture_output=True, check=True, timeout=TIMEOUT)
    return res.stdout.decode("utf-8")


def _title(el: ET.Element, key: str) -> list[str]:
    for part in el.get("title", "").split(";"):
        bits = part.split()
        if bits and bits[0] == key:
            return bits[1:]
    return []


def _hocr_words(hocr: str) -> list[list[Word]]:
    """Tesseract lines of words, each word with text, box, confidence (0..1) and per-char boxes."""
    root = ET.fromstring(hocr)
    lines: list[list[Word]] = []
    for ln in root.iter(f"{NS}span"):
        if ln.get("class") not in ("ocr_line", "ocr_textfloat", "ocr_header", "ocr_caption"):
            continue
        words = []
        for w in ln.iter(f"{NS}span"):
            if w.get("class") != "ocrx_word":
                continue
            box = tuple(float(v) for v in _title(w, "bbox"))
            chars, confs = [], []
            for c in w.iter(f"{NS}span"):
                if c.get("class") == "ocrx_cinfo" and c.text:
                    chars.append((c.text, tuple(float(v) for v in _title(c, "x_bboxes"))))
                    confs.append(float((_title(c, "x_conf") or ["0"])[0]))
            text = "".join(ch for ch, _ in chars) or "".join(w.itertext()).strip()
            if not text.strip():
                continue
            wconf = float((_title(w, "x_wconf") or ["0"])[0])
            words.append({"text": text, "box": box, "conf": min([wconf, *confs]) / 100.0,
                          "chars": _char_boxes(text, chars, box)})
        if words:
            lines.append(sorted(words, key=lambda w: w["box"][0]))
    return lines


def _char_boxes(text: str, chars: list[tuple[str, tuple]], box: tuple) -> list[tuple[str, Box]]:
    """LSTM char boxes if they are one per char, inside the word and left-to-right; else an even split."""
    ok = len(chars) == len(text) and all(len(b) == 4 for _, b in chars)
    if ok:
        xs = [b[0] for _, b in chars]
        ok = xs == sorted(xs) and all(b[0] >= box[0] - 2 and b[2] <= box[2] + 2 and b[2] > b[0] for _, b in chars)
    if ok:
        return [(ch, (b[0], box[1], b[2], box[3])) for ch, b in chars]
    w = (box[2] - box[0]) / max(1, len(text))
    return [(ch, (box[0] + i * w, box[1], box[0] + (i + 1) * w, box[3])) for i, ch in enumerate(text)]


def _cells(lines: list[list[Word]]) -> list[list[Word]]:
    """Merge the words of each Tesseract line into cells, breaking at gaps wider than SPLIT char widths."""
    out = []
    for words in lines:
        cw = median((w["box"][2] - w["box"][0]) / max(1, len(w["text"])) for w in words)
        cells: list[list[Word]] = [[words[0]]]
        for w in words[1:]:
            if w["box"][0] - cells[-1][-1]["box"][2] > SPLIT * cw:
                cells.append([w])
            else:
                cells[-1].append(w)
        out.append([_merge(c) for c in cells])
    return out


def _merge(words: list[Word]) -> Word:
    """One cell from words; no space where Tesseract split a number after its separator ("6.493, 00-")."""
    chars: list[tuple[str, Box]] = []
    text = ""
    for i, w in enumerate(words):
        if i and not (re.search(r"\d[.,]$", words[i - 1]["text"]) and w["text"][:1].isdigit()):
            a, b = words[i - 1]["box"], w["box"]
            chars.append((" ", (a[2], min(a[1], b[1]), b[0], max(a[3], b[3]))))
            text += " "
        chars.extend(w["chars"])
        text += w["text"]
    conf = min(w["conf"] for w in words)
    return {"text": text, "conf": conf if _plausible(text) else 0.0, "chars": chars,
            "box": (min(w["box"][0] for w in words), min(w["box"][1] for w in words),
                    max(w["box"][2] for w in words), max(w["box"][3] for w in words))}


AMOUNT = re.compile(r"^[-+]?(\d{1,3}(\.\d{3})+(,\d+)?|\d{1,3}(,\d{3})+(\.\d+)?|\d{1,3}('\d{3})+([.,]\d+)?|\d+([.,]\d+)?)[-+]?$")
DATE = re.compile(r"^\d{1,2}\.\d{1,2}\.(\d{2}|\d{4})$")


def _plausible(text: str) -> bool:
    """False for a numeric-looking cell with separators no number or date has (e.g. "426,756,13", "1.2.3,4")."""
    if not NUMERIC.match(text) or " " in text:
        return True
    return bool(AMOUNT.match(text) or DATE.match(text))


def _numeric_pass(img: Image.Image, cells: list[Word], max_height: int = 12000) -> None:
    """Re-read numeric-looking cells digits-only (crops stacked on tall canvases); disagreement -> conf 0."""
    nums = [c for c in cells if NUMERIC.match(c["text"])]
    pad = 6
    crops = [img.crop((int(c["box"][0]) - pad, int(c["box"][1]) - pad, int(c["box"][2]) + pad, int(c["box"][3]) + pad))
             for c in nums]
    reads: list[str] = []
    k = 0
    while k < len(crops):
        gap = max(cr.height for cr in crops)
        n = 1
        while k + n < len(crops) and (n + 1) * 2 * gap + gap <= max_height:
            n += 1
        reads += _stacked(crops[k:k + n], gap, pad)
        k += n
    for c, second in zip(nums, reads):
        if second != c["text"].replace(" ", ""):
            c["conf"] = 0.0


def _stacked(crops: list[Image.Image], gap: int, pad: int) -> list[str]:
    """One digits-only Tesseract call over crops stacked vertically; returns the text read in each crop."""
    canvas = Image.new("L", (max(cr.width for cr in crops) + 4 * pad, len(crops) * 2 * gap + gap), 255)
    bands = []
    for i, cr in enumerate(crops):
        y = gap + i * 2 * gap
        canvas.paste(cr, (2 * pad, y))
        bands.append((y, y + cr.height))
    tsv = _tesseract(canvas, "6", "-c", f"tessedit_char_whitelist={NUM_WHITELIST}", "tsv")
    found: list[list[tuple[float, str]]] = [[] for _ in crops]
    for row in (ln.split("\t") for ln in tsv.splitlines()[1:]):
        if len(row) < 12 or not row[11].strip():
            continue
        cy = float(row[7]) + float(row[9]) / 2
        for k, (a, b) in enumerate(bands):
            if a <= cy <= b:
                found[k].append((float(row[6]), row[11].strip()))
                break
    return ["".join(t for _, t in sorted(f)) for f in found]
