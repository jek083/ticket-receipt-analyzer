"""Parser for Picard receipt text produced by the shared OCR extractor."""

import logging
import re
from datetime import datetime
from typing import Mapping, Optional, Sequence

from src.parse_ticket_enrichi import (
    ParsedItem,
    ParsedTicket,
    categorize_item,
)

logger = logging.getLogger(__name__)

_AMOUNT_PATTERN = r"-?\d+[.,]\d{2}"
_AMOUNT_RE = re.compile(_AMOUNT_PATTERN)
_PRICE_LINE_RE = re.compile(rf"^\s*(?P<price>{_AMOUNT_PATTERN})\s*€?\s*$")
_PRICE_SUFFIX_RE = re.compile(rf"(?P<price>{_AMOUNT_PATTERN})\s*€?\s*$")
# "V1" is often read as "VI", "Vl" or "Vil" by the OCR.
_ITEM_LINE_RE = re.compile(
    r"^\s*(?:V[1lIi|!]{1,2}\s+)?\*\s*(?P<description>.+?)\s*$",
    re.IGNORECASE,
)
_TOTAL_HEADER_RE = re.compile(
    r"^\s*total(?:\s*\(\s*(?P<count>\d+)\s*\))?\s*$",
    re.IGNORECASE,
)
_INLINE_TOTAL_RE = re.compile(
    rf"^\s*total(?:\s*\(\s*(?P<count>\d+)\s*\))?\s*:?\s*"
    rf"(?P<amount>{_AMOUNT_PATTERN})\s*€?\s*$",
    re.IGNORECASE,
)
_COUNT_LINE_RE = re.compile(
    r"^\s*(?:\((?P<parenthesized>\d+)\)|"
    r"nb\s+lignes\s+ticket\s*:?\s*(?P<ticket_lines>\d+))\s*$",
    re.IGNORECASE,
)
_DISCOUNT_TOTAL_RE = re.compile(r"^\s*total\s+des\s+remises\b", re.IGNORECASE)
_DISCOUNT_DETAIL_RE = re.compile(
    r"^\s*s[ée]lection\s+picard\s*&\s*nous\b",
    re.IGNORECASE,
)
_DATE_RE = re.compile(
    r"(?P<day>\d{2})[./-](?P<month>\d{2})[./-](?P<year>\d{2,4})"
    r"(?:\s+(?P<time>\d{2}:\d{2}))?"
)
_ITEM_SECTION_END_RE = re.compile(
    r"^\s*(?:total\s+sans\s+remise|total\s+des\s+remises|total)\b",
    re.IGNORECASE,
)
_SEPARATOR_RE = re.compile(r"^\s*-{4,}\s*$")


def _parse_date(lines: Sequence[str]) -> Optional[str]:
    for line in lines:
        match = _DATE_RE.search(line)
        if not match:
            continue

        year = int(match.group("year"))
        if len(match.group("year")) == 2:
            year += 2000
        try:
            parsed_date = datetime(
                year,
                int(match.group("month")),
                int(match.group("day")),
            )
        except ValueError as error:
            logger.warning("Date Picard invalide %r: %s", line, error)
            continue

        timestamp = match.group("time")
        if timestamp:
            parsed_date = datetime.strptime(
                "{} {}".format(parsed_date.date().isoformat(), timestamp),
                "%Y-%m-%d %H:%M",
            )
            return parsed_date.strftime("%Y-%m-%d %H:%M")
        return parsed_date.date().isoformat()

    return None


def _parse_ticket_count(lines: Sequence[str]) -> Optional[int]:
    for line in lines:
        inline_total = _INLINE_TOTAL_RE.match(line)
        if inline_total and inline_total.group("count"):
            return int(inline_total.group("count"))

        header = _TOTAL_HEADER_RE.match(line)
        if header and header.group("count"):
            return int(header.group("count"))

        count_match = _COUNT_LINE_RE.match(line)
        if count_match:
            count = count_match.group("parenthesized") or count_match.group(
                "ticket_lines"
            )
            return int(count)

    return None


def _parse_total(lines: Sequence[str]) -> Optional[float]:
    for index, line in enumerate(lines):
        inline_total = _INLINE_TOTAL_RE.match(line)
        if inline_total:
            return float(inline_total.group("amount").replace(",", "."))

        if not _TOTAL_HEADER_RE.match(line):
            continue

        for following_line in lines[index + 1:index + 5]:
            price = _PRICE_LINE_RE.match(following_line)
            if price:
                return float(price.group("price").replace(",", "."))

    return None


def _parse_total_discounts(lines: Sequence[str]) -> Optional[float]:
    for index, line in enumerate(lines):
        if not _DISCOUNT_TOTAL_RE.match(line):
            continue

        amount = _AMOUNT_RE.search(line)
        if not amount and index + 1 < len(lines):
            amount = _AMOUNT_RE.search(lines[index + 1])
        if amount:
            return abs(float(amount.group().replace(",", ".")))

    return None


def _parse_items(
    lines: Sequence[str],
    categories: Optional[Mapping[str, Sequence[str]]],
) -> list[ParsedItem]:
    start_index = next(
        (index for index, line in enumerate(lines) if _ITEM_LINE_RE.match(line)),
        None,
    )
    if start_index is None:
        return []

    items: list[ParsedItem] = []
    pending_name = None

    def add_item(name: str, price: float) -> None:
        category = categorize_item(name, categories or {})
        items.append(
            {
                "article": name,
                "rayon": "SURGELES",
                "prix_unitaire": price,
                "quantite": 1,
                "poids_kg": None,
                "prix_kg": None,
                "prix_total": price,
                "tva_code": None,
                "categorie": None if category == "autre" else category,
            }
        )

    for line in lines[start_index:]:
        if _ITEM_SECTION_END_RE.match(line):
            break

        item_match = _ITEM_LINE_RE.match(line)
        if item_match:
            description = item_match.group("description").strip()
            price_suffix = _PRICE_SUFFIX_RE.search(description)
            if price_suffix:
                name = description[:price_suffix.start()].strip()
                price = float(price_suffix.group("price").replace(",", "."))
                if name:
                    add_item(name, price)
                pending_name = None
            else:
                if pending_name:
                    logger.warning(
                        "Article Picard sans prix avant la ligne suivante"
                    )
                pending_name = description
            continue

        if _DISCOUNT_DETAIL_RE.match(line) or _SEPARATOR_RE.match(line):
            pending_name = None
            continue

        if not pending_name:
            continue

        price_match = _PRICE_LINE_RE.match(line)
        if price_match:
            price = float(price_match.group("price").replace(",", "."))
            add_item(pending_name, price)
            pending_name = None

    return items


def parse_picard_ticket(
    ocr_text: str,
    source_file: str = "",
    categories: Optional[Mapping[str, Sequence[str]]] = None,
) -> ParsedTicket:
    """Parse Picard's two-line product layout into the shared ticket schema."""
    lines = [line.strip() for line in ocr_text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("Le texte OCR du ticket Picard est vide")

    items = _parse_items(lines, categories)
    if not items:
        raise ValueError("Aucun article Picard reconnu dans le texte OCR")

    return {
        "fichier_source": source_file,
        "enseigne": "Picard",
        "date": _parse_date(lines),
        "total_ticket": _parse_total(lines),
        "total_remises": _parse_total_discounts(lines),
        "nombre_articles_ticket": _parse_ticket_count(lines),
        "articles_detectes": len(items),
        "unites_detectees": sum(int(round(item["quantite"])) for item in items),
        "articles": items,
    }
