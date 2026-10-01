"""Helpers shared by the retailer parsers built on OCR text."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Mapping, Optional, Sequence

from src.parse_ticket_enrichi import ParsedItem, categorize_item

_MONTHS = {
    "janvier": 1,
    "février": 2,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "août": 8,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "décembre": 12,
    "decembre": 12,
}
_FRENCH_DATE_RE = re.compile(
    r"(?P<day>\d{1,2})\s*(?P<month>" + "|".join(_MONTHS) + r")\s*(?P<year>\d{4})"
    r"(?:\s*(?:à|a)?\s*(?P<time>\d{2}:\d{2})(?::\d{2})?)?",
    re.IGNORECASE,
)


_NUMERIC_DATE_RE = re.compile(
    r"(?<![\d/.])(?P<day>\d{2})/(?P<month>\d{2})/(?P<year>\d{4})"
    r"(?:\s+(?P<time>\d{2}:\d{2}))?(?!\d)"
)


def parse_amount(value: str) -> float:
    """Convert an amount such as ``12,73`` or ``12.73`` to a float."""
    return float(value.replace(",", "."))


def find_french_date(lines: Sequence[str]) -> Optional[str]:
    """Return the first ``14 juin 2024 à 18:37:58`` date as ``YYYY-MM-DD[ HH:MM]``."""
    for line in lines:
        match = _FRENCH_DATE_RE.search(line)
        if not match:
            continue
        try:
            day = datetime(
                int(match.group("year")),
                _MONTHS[match.group("month").lower()],
                int(match.group("day")),
            )
        except ValueError:
            continue
        time = match.group("time")
        return f"{day:%Y-%m-%d} {time}" if time else f"{day:%Y-%m-%d}"
    return None


def find_numeric_date(lines: Sequence[str]) -> Optional[str]:
    """Return the first ``JJ/MM/AAAA [HH:MM]`` date as ``YYYY-MM-DD[ HH:MM]``."""
    for line in lines:
        match = _NUMERIC_DATE_RE.search(line)
        if not match:
            continue
        try:
            day = datetime(
                int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
            )
        except ValueError:
            continue
        time = match.group("time")
        return f"{day:%Y-%m-%d} {time}" if time else f"{day:%Y-%m-%d}"
    return None


def build_item(
    name: str,
    total: float,
    categories: Optional[Mapping[str, Sequence[str]]],
    *,
    rayon: Optional[str] = None,
    tva_code: Optional[str] = None,
    quantity: float = 1,
    unit_price: Optional[float] = None,
) -> ParsedItem:
    return {
        "article": name,
        "rayon": rayon,
        "prix_unitaire": total if unit_price is None else unit_price,
        "quantite": quantity,
        "poids_kg": None,
        "prix_kg": None,
        "prix_total": total,
        "tva_code": tva_code,
        "categorie": categorize_item(name, categories or {}),
    }
