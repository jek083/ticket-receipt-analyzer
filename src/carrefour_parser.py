"""Parser for Carrefour receipt text produced by the shared OCR extractor.

Recognized layout: one line per article ``<code TVA> <désignation> [P.U] <montant>``,
an optional ``Remise Immédiate`` line below the discounted article, and a
``Total <rayon>`` line closing each section of articles. The date, when
printed, is a ``JJ/MM/AAAA`` line in the header.
"""

import logging
import re
from typing import List, Mapping, Optional, Sequence, Tuple

from src.ocr_common import build_item, find_numeric_date, parse_amount
from src.parse_ticket_enrichi import ParsedItem, ParsedTicket

logger = logging.getLogger(__name__)

_AMOUNT = r"\d+[.,]\d{2}"
# The receipt date, when printed, sits in the header above the article table.
_HEADER_LINES = 10
_ITEM_LINE_RE = re.compile(
    rf"^\s*(?P<tva>\d)\s+(?P<body>.+?)\s+(?P<price>(?<![-\d]){_AMOUNT})\s*€?\s*$"
)
# "0,59x4" is often read "0,694": the last digit is the quantity.
_UNIT_PRICE_RE = re.compile(
    r"\s+(?P<unit>\d+[.,]\d{2})\s*(?:[xX]\s*(?P<qty_x>\d{1,2})|(?P<qty_glued>\d))$"
)
_SECTION_TOTAL_RE = re.compile(
    rf"^\s*total\s+(?P<rayon>(?!avant\b|remise\b|des\b|[àa]\s+payer).+?)\s+{_AMOUNT}\s*$",
    re.IGNORECASE,
)
_DISCOUNT_LINE_RE = re.compile(
    rf"^\s*remise\b.*?(?P<amount>{_AMOUNT})\s*-\s*$",
    re.IGNORECASE,
)
_TABLE_HEADER_RE = re.compile(r"^\s*d[ée]signation\b", re.IGNORECASE)
_TOTAL_PAYABLE_RE = re.compile(
    rf"total\s+\S{{0,2}}\s*payer\s+(?P<amount>{_AMOUNT})",
    re.IGNORECASE,
)
_TOTAL_BEFORE_DISCOUNT_RE = re.compile(
    rf"total\s+avant\s+r\w+\s+(?P<amount>{_AMOUNT})",
    re.IGNORECASE,
)
_TOTAL_BENEFITS_RE = re.compile(
    rf"total\s+des\s+avantages.*?(?P<amount>{_AMOUNT})\s*-",
    re.IGNORECASE,
)
_ARTICLE_COUNT_RE = re.compile(r"^\s*(?P<count>\d+)\s+articles?\b", re.IGNORECASE)


def _split_body(body: str, price: float) -> Tuple[str, float, float]:
    """Return ``(désignation, quantité, prix unitaire)`` from an article body."""
    match = _UNIT_PRICE_RE.search(body)
    if not match:
        return body.strip(), 1, price

    name = body[: match.start()].strip()
    quantity = int(match.group("qty_x") or match.group("qty_glued"))
    if not name:
        return body.strip(), 1, price
    quantity = max(quantity, 1)
    return name, quantity, round(price / quantity, 2)


def _parse_items(
    lines: Sequence[str],
    categories: Optional[Mapping[str, Sequence[str]]],
) -> Tuple[List[ParsedItem], float]:
    start = next(
        (index + 1 for index, line in enumerate(lines) if _TABLE_HEADER_RE.match(line)),
        0,
    )
    items: List[ParsedItem] = []
    section_start = 0
    discounts = 0.0

    for line in lines[start:]:
        if _ARTICLE_COUNT_RE.match(line):
            break

        section = _SECTION_TOTAL_RE.match(line)
        if section:
            for item in items[section_start:]:
                item["rayon"] = section.group("rayon").strip()
            section_start = len(items)
            continue

        discount = _DISCOUNT_LINE_RE.match(line)
        if discount:
            discounts += parse_amount(discount.group("amount"))
            continue

        match = _ITEM_LINE_RE.match(line)
        if not match:
            continue
        price = parse_amount(match.group("price"))
        name, quantity, unit_price = _split_body(match.group("body"), price)
        items.append(
            build_item(
                name,
                price,
                categories,
                tva_code=match.group("tva"),
                quantity=quantity,
                unit_price=unit_price,
            )
        )

    return items, round(discounts, 2)


def _find_amount(lines: Sequence[str], pattern: "re.Pattern[str]") -> Optional[float]:
    for line in lines:
        match = pattern.search(line)
        if match:
            return parse_amount(match.group("amount"))
    return None


def _parse_total_discounts(
    lines: Sequence[str],
    total: Optional[float],
    line_discounts: float,
) -> Optional[float]:
    benefits = _find_amount(lines, _TOTAL_BENEFITS_RE)
    if benefits is not None:
        return benefits
    before_discount = _find_amount(lines, _TOTAL_BEFORE_DISCOUNT_RE)
    if before_discount is not None and total is not None:
        return round(before_discount - total, 2)
    return line_discounts or None


def parse_carrefour_ticket(
    ocr_text: str,
    source_file: str = "",
    categories: Optional[Mapping[str, Sequence[str]]] = None,
) -> ParsedTicket:
    """Parse a Carrefour receipt into the shared ticket schema.

    Item prices are gross prices; immediate discounts are reported in
    ``total_remises``. Some Carrefour receipts carry no date.
    """
    lines = [line.strip() for line in ocr_text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("Le texte OCR du ticket Carrefour est vide")

    items, line_discounts = _parse_items(lines, categories)
    if not items:
        raise ValueError("Aucun article Carrefour reconnu dans le texte OCR")

    total = _find_amount(lines, _TOTAL_PAYABLE_RE)
    count_line = next(
        (match for match in map(_ARTICLE_COUNT_RE.match, lines) if match), None
    )
    return {
        "fichier_source": source_file,
        "enseigne": "Carrefour",
        "date": find_numeric_date(lines[:_HEADER_LINES]),
        "total_ticket": total,
        "total_remises": _parse_total_discounts(lines, total, line_discounts),
        "nombre_articles_ticket": int(count_line.group("count")) if count_line else None,
        "articles_detectes": len(items),
        "unites_detectees": sum(int(round(item["quantite"])) for item in items),
        "articles": items,
    }
