"""Exact number grammar for amounts printed in German fixed-asset schedules.

Integers only (never float). Every parse is relative to a `NumberStyle` detected over many samples of
the same column/document; a value whose interpretation the style cannot decide is never guessed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from itertools import combinations
from typing import Iterable

DECIMALS = (",", ".")
GROUPS: tuple[str | None, ...] = (None, ".", ",", " ", "'")  # NBSP / narrow NBSP are normalised to " "
NEG_FORMS = frozenset({"leading", "trailing", "parens"})
PLACEHOLDERS = frozenset({"-", "–", "—", "−", "--"})


@dataclass(frozen=True)
class NumberStyle:
    decimal: str               # "," or "."
    group: str | None          # thousands separator; None = never observed (only ungrouped integers accepted)
    decimals: int | None       # fixed decimals printed (2, or 0 for TEUR/whole euros); None = mixed (0-2)
    negatives: frozenset[str]  # subset of {"leading", "trailing", "parens"} observed


@dataclass(frozen=True)
class Amount:
    cents: int     # signed value in cents at the printed scale (multiply by unit_scale separately)
    negative: bool
    raw: str


def _norm(text: str) -> str:
    return str(text).strip().replace(" ", " ").replace(" ", " ").replace("−", "-")


def is_zero_placeholder(text: str) -> bool:
    """True for a dash printed instead of 0 ('-', '–', '—'). parse_amount returns None for these;
    the caller maps them to 0 only when the document visibly uses dashes for zero."""
    return _norm(text) in PLACEHOLDERS


@lru_cache(maxsize=None)
def _pattern(decimal: str, group: str | None, decimals: int | None, negatives: frozenset[str]) -> re.Pattern[str]:
    if group:
        intpart = rf"(?P<i>0|[1-9]\d{{0,2}}(?:{re.escape(group)}\d{{3}})*)"
    else:
        intpart = r"(?P<i>0|[1-9]\d*)"
    d = re.escape(decimal)
    frac = {0: "", 2: rf"{d}(?P<f>\d{{2}})", None: rf"(?:{d}(?P<f>\d{{1,2}}))?"}[decimals]
    core = intpart + frac
    alts = [rf"(?P<p>{core})"]
    if "leading" in negatives:
        alts.append(rf"(?P<l>-\s?{core.replace('?P<i>', '?P<i2>').replace('?P<f>', '?P<f2>')})")
    if "trailing" in negatives:
        alts.append(rf"(?P<t>{core.replace('?P<i>', '?P<i3>').replace('?P<f>', '?P<f3>')}\s?-)")
    if "parens" in negatives:
        alts.append(rf"(?P<b>\({core.replace('?P<i>', '?P<i4>').replace('?P<f>', '?P<f4>')}\))")
    return re.compile("|".join(alts))


def parse_amount(text: str, style: NumberStyle) -> Amount | None:
    """Strict parse: None unless the whole text matches the style exactly (placeholders and blanks → None)."""
    if style.decimals not in (0, 2, None) or style.group == style.decimal:
        return None
    s = _norm(text)
    m = _pattern(style.decimal, style.group, style.decimals, style.negatives).fullmatch(s)
    if not m:
        return None
    gd = m.groupdict()
    i = next(j for j in ("", "2", "3", "4") if gd.get("i" + j) is not None)
    digits, frac = gd["i" + i], gd.get("f" + i) or ""
    if style.group:
        digits = digits.replace(style.group, "")
    cents = int(digits) * 100 + int(frac.ljust(2, "0"))
    negative = m.group("p") is None
    return Amount(-cents if negative else cents, negative, str(text))


def parse_cents(raw: str) -> int | None:
    """Convenience: parse one value with the style detected from itself alone (None if ambiguous)."""
    style = detect_style([raw])
    a = parse_amount(raw, style) if style else None
    return a.cents if a else None


_AMOUNT_LIKE = re.compile(r"\(?-?\s?\d[\d.,' ]*\d?\s?-?\)?")


def is_amount_like(text: str) -> bool:
    """Cheap style-free pre-filter: digits with separators and optional sign/parentheses only."""
    s = _norm(text)
    return bool(s) and bool(_AMOUNT_LIKE.fullmatch(s)) and s[-1] in "0123456789-)"


def _candidates() -> list[NumberStyle]:
    return [NumberStyle(d, g, k, NEG_FORMS) for d in DECIMALS for g in GROUPS if g != d for k in (0, 2, None)]


def _neg_form(s: str) -> str | None:
    s = _norm(s)
    return "parens" if s.startswith("(") else "leading" if s.startswith("-") else "trailing" if s.endswith("-") else None


def detect_style(samples: Iterable[str]) -> NumberStyle | None:
    """Infer the one style that parses every sample; None when samples are ambiguous or contradictory.

    Blanks, placeholders, non-amount text and strings no candidate style can parse (glued values, OCR junk)
    are ignored. All remaining samples must fit one style; every fitting style must give the same cents for
    every sample; the decimal separator must be unique among fitting styles (pure integers → None).
    """
    cands = _candidates()
    rows: list[tuple[str, list[Amount | None]]] = []
    for raw in samples:
        if not is_amount_like(raw) or is_zero_placeholder(raw):
            continue
        parsed = [parse_amount(raw, c) for c in cands]
        if any(parsed):
            rows.append((raw, parsed))
    if not rows:
        return None
    fits = [j for j in range(len(cands)) if all(p[j] for _, p in rows)]
    if not fits:
        return None
    for _, p in rows:
        if len({p[j].cents for j in fits}) > 1:  # type: ignore[union-attr]
            return None
    if len({cands[j].decimal for j in fits}) > 1:
        return None
    ks = {cands[j].decimals for j in fits}
    k = 2 if 2 in ks else 0 if 0 in ks else None
    groups = {cands[j].group for j in fits if cands[j].decimals == k}
    if None in groups:
        group = None
    elif len(groups) == 1:
        group = groups.pop()
    else:
        return None
    negs = frozenset(f for f in (_neg_form(r) for r, _ in rows) if f)
    return NumberStyle(cands[fits[0]].decimal, group, k, negs)


def split_glued(text: str, style: NumberStyle, n: int = 2) -> list[tuple[str, ...]]:
    """ALL ways to cut `text` into n adjacent amounts that each parse strictly under `style`.
    More than one result means the split is ambiguous: abstain or disambiguate with an accounting check."""
    s = _norm(text).replace(" ", "") if style.group != " " else _norm(text)
    out: list[tuple[str, ...]] = []
    for cuts in combinations(range(1, len(s)), n - 1):
        parts = tuple(s[a:b] for a, b in zip((0, *cuts), (*cuts, len(s))))
        if all(parse_amount(p, style) for p in parts):
            out.append(parts)
    return out


_THOUSAND = re.compile(
    r"\bT\s?EUR\b|\bT\s?€|\bTEuro\b|\bk(?:EUR\b|€)|\b(?:in\s+)?(?:Tausend|Tsd\b\.?)(?:\s*(?:EUR\b|€|Euro\b))?", re.I)
_EURO = re.compile(r"\bEUR\b|€|\bEuro\b", re.I)
_OTHER = re.compile(r"\bMio\b|\bMrd\b|\bMillionen\b|\bMEUR\b", re.I)


def unit_scale(texts: Iterable[str]) -> int | None:
    """1 for EUR, 1000 for TEUR/T€/Tsd. EUR/in Tausend …; None when no unit marker, a million/other
    marker, or both EUR and thousand markers appear."""
    thousand = euro = False
    for t in texts:
        t = _norm(t)
        if _OTHER.search(t):
            return None
        if _THOUSAND.search(t):
            thousand = True
        if _EURO.search(_THOUSAND.sub(" ", t)):
            euro = True
    if thousand and euro:
        return None
    return 1000 if thousand else 1 if euro else None
