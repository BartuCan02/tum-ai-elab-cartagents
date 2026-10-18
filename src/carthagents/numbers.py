"""Exact financial token parsing. Return integer cents, never model-produced floats."""
import re
from decimal import Decimal, InvalidOperation

TOKEN = re.compile(r"(?<![\w])(?:[-−]?\s*\(?\d{1,3}(?:[.,' ]\d{3})+(?:[.,]\d{1,2})?\)?-?|[-−]?\s*\(?\d+(?:[.,]\d{1,2})?\)?-?)(?![\w])")

def parse_cents(raw: str, scale: int = 1) -> int | None:
    s = str(raw).strip().replace("\u00a0", " ").replace("−", "-").replace("€", "")
    if not s or s in {"-", "—", "–"}:
        return None
    negative = s.startswith("-") or s.endswith("-") or (s.startswith("(") and s.endswith(")"))
    s = s.strip("-() ").replace("'", "").replace(" ", "")
    if not re.fullmatch(r"\d[\d.,]*", s):
        return None
    # One separator with three trailing digits is treated as a grouping mark.
    if "," in s and "." in s:
        decimal_sep = "," if s.rfind(",") > s.rfind(".") else "."
    elif "," in s:
        decimal_sep = "," if len(s.rsplit(",", 1)[1]) in (1, 2) else None
    elif "." in s:
        decimal_sep = "." if len(s.rsplit(".", 1)[1]) in (1, 2) else None
    else:
        decimal_sep = None
    if decimal_sep:
        head, tail = s.rsplit(decimal_sep, 1)
        head = head.replace(",", "").replace(".", "")
        normalized = head + "." + tail
    else:
        normalized = s.replace(",", "").replace(".", "")
    try:
        value = Decimal(normalized) * Decimal(100) * Decimal(scale)
        if value != value.to_integral_value():
            return None
        return int(value) * (-1 if negative else 1)
    except (InvalidOperation, ValueError):
        return None
