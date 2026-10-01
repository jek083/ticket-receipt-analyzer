"""Parser for Auchan receipt text produced by the shared OCR extractor.

Recognized layout: one line per article ``[*]<désignation tronquée..> <montant>``
between the ``Hôte(sse)`` header and the ``Total`` line. The leading star flags
articles eligible to meal vouchers; it is not a VAT code and is dropped.
"""

import logging
import re
from typing import List, Mapping, Optional, Sequence

from src.ocr_common import build_item, find_french_date, parse_amount
from src.parse_ticket_enrichi import ParsedItem, ParsedTicket

logger = logging.getLogger(__name__)

_AMOUNT = r"\d+[.,]\d{2}"
_ITEM_LINE_RE = re.compile(
    rf"^\s*(?P<name>.+?)\s+(?P<price>(?<![-\d]){_AMOUNT})\s*€?\s*$"
)
_TOTAL_RE = re.compile(rf"^\s*total\s+(?P<amount>{_AMOUNT})\s*€?\s*$", re.IGNORECASE)
_ARTICLE_COUNT_RE = re.compile(r"^\s*(?P<count>\d+)\s+articles?\s*$", re.IGNORECASE)
_HEADER_END_RE = re.compile(r"h[ôo]te\(sse\)|caisse\s*:", re.IGNORECASE)
_HEADER_SCAN_LINES = 12
# Star and quotes are frequently misread by the OCR (#, +, ‘...).
_NAME_PREFIX_RE = re.compile(r"^[*#+‘’'`\"«.\s]+")
_NAME_SUFFIX_RE = re.compile(r"[\s.,]+$")


def _clean_name(raw: str) -> str:
    return _NAME_SUFFIX_RE.sub("", _NAME_PREFIX_RE.sub("", raw))


def _parse_items(
    lines: Sequence[str],
    categories: Optional[Mapping[str, Sequence[str]]],
) -> List[ParsedItem]:
    start = 0
    for index, line in enumerate(lines[:_HEADER_SCAN_LINES]):
        if _HEADER_END_RE.search(line):
            start = index + 1

    items: List[ParsedItem] = []
    for line in lines[start:]:
        if _TOTAL_RE.match(line):
            break
        match = _ITEM_LINE_RE.match(line)
        if not match:
            continue
        name = _clean_name(match.group("name"))
        if name:
            items.append(build_item(name, parse_amount(match.group("price")), categories))
    return items


def parse_auchan_ticket(
    ocr_text: str,
    source_file: str = "",
    categories: Optional[Mapping[str, Sequence[str]]] = None,
) -> ParsedTicket:
    """Parse an Auchan receipt into the shared ticket schema."""
    lines = [line.strip() for line in ocr_text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("Le texte OCR du ticket Auchan est vide")

    items = _parse_items(lines, categories)
    if not items:
        raise ValueError("Aucun article Auchan reconnu dans le texte OCR")

    total_match = next((m for m in map(_TOTAL_RE.match, lines) if m), None)
    count_match = next((m for m in map(_ARTICLE_COUNT_RE.match, lines) if m), None)
    return {
        "fichier_source": source_file,
        "enseigne": "Auchan",
        "date": find_french_date(lines),
        "total_ticket": parse_amount(total_match.group("amount")) if total_match else None,
        "total_remises": None,
        "nombre_articles_ticket": int(count_match.group("count")) if count_match else None,
        "articles_detectes": len(items),
        "unites_detectees": len(items),
        "articles": items,
    }
