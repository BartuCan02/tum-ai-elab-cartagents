"""Header vocabulary: column header texts -> canonical field, role, "ignore" or unknown (None).

Pure rules, no layout lists. A header is decomposed into small concepts:
  domain   cost (AHK) / dep (AfA, Abschreibung, Wertberichtigung) / bv (Buchwert)
  movement zugang / abgang / umbuchung / zuschreibung / year (AfA of the year) / bew (SAP net movement)
  time     open (GJ-Beg, Beginn, Vorjahr, earlier date) / close (aktuell, lfd, Ende, later date)
Movements map directly (domain decides: 'Abgänge' under AfA = afa_abgang). State columns
(no movement) get their time jointly: explicit markers, then dates relative to each other and to
the period end, then position relative to the movements of the same domain (left = open,
right = close; this makes SAP 'kum AfA' before 'gep AfA' the opening value and 'kumulierte AfA'
after the movements the closing value). Two columns with the same field -> both None.
Columns must be passed left to right.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass


@dataclass
class ColumnHeader:
    key: object                 # caller's column id
    parts: list[str]            # header texts stacked top -> bottom (group headers included)


@dataclass
class Assignment:
    field: str | None           # canonical field / role / "ignore"; None = unknown or ambiguous
    reason: str


AMOUNT = {"ahk_open", "zugang", "abgang", "umbuchung", "ahk_close", "kum_afa_open", "afa_gj", "afa_abgang",
          "zuschreibung", "kum_afa_close", "bw_close", "bw_prev", "abgang_bw", "bew_ahk", "bew_wb"}
STATE = {("cost", "open"): "ahk_open", ("cost", "close"): "ahk_close", ("dep", "open"): "kum_afa_open",
         ("dep", "close"): "kum_afa_close", ("bv", "open"): "bw_prev", ("bv", "close"): "bw_close"}
DOM_MOVES = {"cost": {"zugang", "abgang", "umbuchung", "bew_ahk"},
             "dep": {"afa_gj", "afa_abgang", "zuschreibung", "bew_wb"}}

_R = lambda p: re.compile(p)
IGNORE = _R(r"\blt\b|laut|differenz|summe|zinsen|aktivierte|nachaktiv|invest|foerder|sonder|entwurf|abweich|"
            r"kontroll|pruef|check|erloes|verkauf|abgang am|abgangsdatum|datum des abgangs|deakt")
ROLES = [  # order matters: first match wins
    ("acq_date", _R(r"aktivierung|aktivdatum|akt datum|aktiviert|anschaff\w* datum|ahk datum|ansch dat|"
                    r"zugang am|zugangsdatum")),
    ("sub_no", _R(r"^(unr|unternummer|unternr|unter nr)\b")),
    ("asset_no", _R(r"^(anlage|anl nr|anlage nr|anlagennummer|anlagenummer|inv nr|inventar nummer|"
                    r"inventarnummer|anlagen nr)$")),
    ("cost_center", _R(r"kostenstelle|^kst$")),
    ("currency", _R(r"^(waehrg|waehrung|whrg|wahrung)$")),
    ("position", _R(r"^(bilanzpos|bilpos|bil pos|bilanzposition|bilanzposten)$")),
    ("class_no", _R(r"^(anlagen)?klasse$")),
    ("account", _R(r"^(konto|bestkto ?ahk|bestandskonto( ?ahk)?|anl ?konto|sachkonto)$")),
    ("useful_life", _R(r"\bnd\b|nutzungsdauer")),
    ("method", _R(r"afa art|afa methode|^methode$|afa meth")),
    ("name", _R(r"^(inventar |anlagen|klassen)?bezeichnung$|^inventarbezeichnung$")),
]
DOMS = [("bv", _R(r"buchwe?rt|buchwrt|\bbw\b|restwert")),
        ("dep", _R(r"\bafa\b|abschreib|abschr|absch\b|wertber|\bwb\b")),
        ("cost", _R(r"\bahk\b|\bak ?hk\b|anschaff|herstell|anschwert|ansch\b|anschaffungswert"))]
MOVES = [("bew", _R(r"^bew\b|\bbew\b|bewegung")),
         ("zuschreibung", _R(r"zuschreib")),
         ("umbuchung", _R(r"umbuch")),
         ("zugang", _R(r"\bzugae?nge?\b|\bzugang")),
         ("abgang", _R(r"\babgae?nge?\b|\babgang|minderung")),
         ("year", _R(r"geschaeftsjahr|des jahres|lfd jahr|lfd\. jahr|laufendes jahr|\bgep\b|geplant|planmaessig|"
                     r"normal"))]
OPEN = _R(r"gj beg|beginn|\bbeg\b|anfang|vortrag|vorjahr|eroeffnung")
CLOSE = _R(r"aktuell|\blfd\b|\bende\b|endstand|schluss")
DATE = _R(r"\b(\d{1,2})\.(\d{1,2})\.(\d{2,4})?")
UNIT = _R(r"^\(?[+\-/ ]*\)?\s*t?eur$")


def norm(parts: list[str]) -> str:
    """Join stacked header parts; repair hyphenation; fold case/umlauts/punctuation."""
    out = ""
    for p in parts:
        p = p.strip()
        if not p or UNIT.match(p.lower()):
            continue
        if out.endswith("-") and p[:1].islower():
            out = out[:-1] + p
        else:
            out = (out + " " + p).strip()
    t = out.lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        t = t.replace(a, b)
    t = re.sub(r"(\w)- (\w)", r"\1\2", t)            # 'herstellungs- kosten'
    t = re.sub(r"(?<=[a-z])-(?=[a-z]{2,}\b)(?!beg)", "", t) if re.search(r"waehr-ung|unter-nummer", t) else t
    t = re.sub(r"[^a-z0-9.%/ ]+", " ", t.replace("-", " "))
    t = re.sub(r"(?<=[a-z])\.(?=\s|$)", "", t)        # 'zuschreib.' -> 'zuschreib'
    return re.sub(r"\s+", " ", t).strip()


def _dates(text: str, period_end: dt.date | None) -> list[tuple[int, int, int | None]]:
    out = []
    for d, m, y in DATE.findall(text):
        yy = int(y) + (2000 if len(y) == 2 else 0) if y else None
        out.append((int(d), int(m), yy))
    return out


def _date_time(d: tuple[int, int, int | None], dom: str, period_end: dt.date | None) -> str | None:
    """Time slot of a heading date relative to the fiscal year (needs the year)."""
    if period_end is None or d[2] is None:
        return None
    try:
        day = dt.date(d[2], d[1], d[0])
    except ValueError:
        return None
    start = (period_end.replace(year=period_end.year - 1) + dt.timedelta(days=1))
    prior_end = start - dt.timedelta(days=1)
    if day == period_end:
        return "close"
    if day == start or day == prior_end:
        return "open"
    return None


def _analyse(text: str) -> dict:
    a = {"dom": {n for n, r in DOMS if r.search(text)}, "mov": {n for n, r in MOVES if r.search(text)},
         "open": bool(OPEN.search(text)), "close": bool(CLOSE.search(text)), "text": text}
    if "bv" in a["dom"]:
        a["dom"].discard("dep") if not re.search(r"\bafa\b|abschreib", text) else None
    if "zuschreibung" in a["mov"] and re.search(r"\babschreib", text):
        a["mov"].add("year")                 # block layout 'Abschreibung / Zuschreibung-': two meanings
    elif a["mov"] - {"year"}:
        a["mov"].discard("year")             # 'Zugänge, Geschäftsjahr'
    if "year" in a["mov"] and re.search(r"\blfd\b", text):
        a["close"] = False                   # 'AfA lfd. Jahr' is a movement
    return a


def classify_columns(headers: list[ColumnHeader], period_end: dt.date | None = None) -> dict[object, Assignment]:
    """Classify all columns of one table jointly (headers ordered left to right)."""
    res: dict[object, Assignment] = {}
    info = []
    for h in headers:
        text = norm(h.parts)
        a = _analyse(text)
        info.append((h, a))
    gross = any("cost" in a["dom"] for _, a in info)
    states: list[tuple[int, object, dict]] = []
    for i, (h, a) in enumerate(info):
        t = a["text"]
        if not t:
            res[h.key] = Assignment(None, "empty header")
            continue
        if IGNORE.search(t) or ("umbuchung" in a["mov"] and a["dom"] == {"dep"}):
            res[h.key] = Assignment("ignore", f"known non-target column: {t!r}")
            continue
        hit = [n for n, r in ROLES if r.search(t)]
        if len(hit) > 1:
            res[h.key] = Assignment(None, f"several roles {hit}: {t!r}")
            continue
        role = hit[0] if hit else None
        if role and (role in ("acq_date", "method", "useful_life") or not (a["dom"] or a["mov"])):
            res[h.key] = Assignment(role, f"role {role}: {t!r}")
            continue
        dom, mov = a["dom"], a["mov"]
        if len(dom) > 1:
            res[h.key] = Assignment(None, f"mixed domains {sorted(dom)}: {t!r}")
            continue
        d = next(iter(dom), None)
        if len(mov) > 1:
            res[h.key] = Assignment(None, f"several movements {sorted(mov)} in one column: {t!r}")
            continue
        m = next(iter(mov), None)
        f = None
        if m == "bew":
            f = {"cost": "bew_ahk", "dep": "bew_wb"}.get(d)
        elif m == "zuschreibung":
            f = "zuschreibung" if d in (None, "dep") else None
        elif m == "umbuchung":
            f = "umbuchung" if d in (None, "cost") else None
        elif m == "zugang":
            f = {"cost": "zugang", None: "zugang", "dep": "afa_gj"}.get(d)
        elif m == "abgang":
            f = {"cost": "abgang", "dep": "afa_abgang", "bv": "abgang_bw"}.get(d) if d else \
                ("abgang" if gross else ("abgang_bw" if any("bv" in x["dom"] for _, x in info) else None))
        elif m == "year":
            f = "afa_gj" if d == "dep" else None
        if m:
            res[h.key] = Assignment(f, f"movement {m} in domain {d}: {t!r}" if f else f"unclear movement {m}/{d}: {t!r}")
            continue
        if d is None:
            res[h.key] = Assignment(None, f"not understood: {t!r}")
            continue
        states.append((i, h.key, a))
    _resolve_states(states, info, res, period_end)
    # duplicates -> ambiguous
    seen: dict[str, list[object]] = {}
    for k, asg in res.items():
        if asg.field in AMOUNT or (asg.field and asg.field not in ("ignore",)):
            seen.setdefault(asg.field, []).append(k)
    for f, ks in seen.items():
        if len(ks) > 1:
            for k in ks:
                res[k] = Assignment(None, f"ambiguous: {len(ks)} columns look like {f}")
    return {h.key: res[h.key] for h in headers}


def _resolve_states(states, info, res, period_end) -> None:
    """Give state columns (no movement) their open/close time jointly."""
    moves_at = {}                              # domain -> column indices of its movements
    for i, (h, a) in enumerate(info):
        f = res.get(h.key)
        if f and f.field:
            for dom, fs in DOM_MOVES.items():
                if f.field in fs:
                    moves_at.setdefault(dom, []).append(i)
    by_dom: dict[str, list] = {}
    for i, k, a in states:
        by_dom.setdefault(next(iter(a["dom"])), []).append((i, k, a))
    for dom, cols in by_dom.items():
        slot: dict[object, tuple[str | None, str]] = {}
        for i, k, a in cols:                   # explicit markers
            if a["open"] != a["close"]:
                slot[k] = ("open" if a["open"] else "close", "marker")
        dated = [(i, k, ds[0]) for i, k, a in cols if k not in slot and len(ds := _dates(a["text"], period_end)) == 1]
        for i, k, d in dated:                  # dates vs period end
            s = _date_time(d, dom, period_end)
            if s:
                slot[k] = (s, "date vs period end")
        full = [(i, k, d) for i, k, d in dated if k not in slot and d[2]]
        if len(full) == 2 and period_end is None:  # dates relative to each other
            (i1, k1, d1), (i2, k2, d2) = full
            o1, o2 = (d1[2], d1[1], d1[0]), (d2[2], d2[1], d2[0])
            if o1 != o2:
                slot[k1], slot[k2] = (("open", "earlier date"), ("close", "later date")) if o1 < o2 else \
                    (("close", "later date"), ("open", "earlier date"))
        mv = moves_at.get(dom, [])
        for i, k, a in cols:                   # position relative to own movements
            if k in slot or (_dates(a["text"], period_end) and dom == "bv") or not re.search(r"\bkum|\bhist", a["text"]):
                continue                       # only cumulative/historical values are placed by position
            if mv and i < min(mv):
                slot[k] = ("open", "left of the movements")
            elif mv and i > max(mv):
                slot[k] = ("close", "right of the movements")
        rest = [k for i, k, a in cols if k not in slot]
        if dom in ("bv", "cost", "dep") and len(rest) == 1:
            taken = {s for s, _ in slot.values()}
            if len(cols) == 1 and not any(moves_at.values()):
                slot[rest[0]] = ("close", "only value of its kind in a list without movements")
            elif dom == "bv" and taken == {"open"}:
                slot[rest[0]] = ("close", "the other book value is the opening one")
            elif dom == "bv" and taken == {"close"}:
                slot[rest[0]] = ("open", "the other book value is the closing one")
            elif dom == "bv" and len(cols) == 1:
                slot[rest[0]] = ("close", "single unqualified book value")
        for i, k, a in cols:
            s = slot.get(k)
            if s:
                res[k] = Assignment(STATE[(dom, s[0])], f"{dom} {s[0]} ({s[1]}): {a['text']!r}")
            else:
                res[k] = Assignment(None, f"{dom} state with unclear time: {a['text']!r}")
