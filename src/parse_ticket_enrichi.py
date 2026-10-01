# -*- coding: utf-8 -*-
"""Parse OCR text from French receipts into structured ticket records."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import List, Mapping, NotRequired, Optional, Sequence, TypedDict

logger = logging.getLogger(__name__)


class ParsedItem(TypedDict):
    article: str
    rayon: Optional[str]
    prix_unitaire: Optional[float]
    quantite: float
    poids_kg: Optional[float]
    prix_kg: Optional[float]
    prix_total: float
    tva_code: Optional[str]
    categorie: Optional[str]


class ParsedTicket(TypedDict):
    fichier_source: str
    enseigne: str
    date: Optional[str]
    total_ticket: Optional[float]
    total_remises: Optional[float]
    nombre_articles_ticket: Optional[int]
    articles_detectes: int
    unites_detectees: int
    articles: List[ParsedItem]
    format_magasin: NotRequired[Optional[str]]


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
_MONTH_PATTERN = "|".join(_MONTHS)
_MONEY_PATTERN = r"\d+[.,]\d{2}"
_QUANTITY_PATTERN = r"\d+(?:[.,]\d+)?"

_PAGE_HEADER_RE = re.compile(r"^---\s*page\s+\d+(?:\s+\(ocr\))?\s*---$", re.IGNORECASE)
_DATE_FR_RE = re.compile(
    rf"(?P<day>\d{{1,2}})\s*(?P<month>{_MONTH_PATTERN})\s*"
    r"(?P<year>\d{4})\s*(?P<time>\d{2}:\d{2})?",
    re.IGNORECASE,
)
_DATE_ISO_RE = re.compile(
    r"(?P<year>\d{4})[/-](?P<month>\d{2})[/-](?P<day>\d{2})"
)
_DATE_NUM_RE = re.compile(
    r"(?P<day>\d{2})[/-](?P<month>\d{2})[/-](?P<year>\d{2,4})"
)
# OCR noise on "amount VAT-code" lines: "5:26 1!" instead of "5.26 1".
_NOISY_AMOUNT_RE = re.compile(r"(?P<int>\d+)[:;](?P<dec>\d{2})(?P<tva>\s+\d)[!|lI]?$")
_NOISY_TVA_SUFFIX_RE = re.compile(r"(?P<head>\d[.,]\d{2}\s+\d)[!|lI]$")
_TOTAL_LINE_RE = re.compile(r"^total\b", re.IGNORECASE)
_TICKET_ARTICLE_COUNT_RE = re.compile(
    r"^total\s+(?P<count>\d+)\s+articles?\b", re.IGNORECASE
)
_MONEY_RE = re.compile(_MONEY_PATTERN)
_TOTAL_WITH_TVA_RE = re.compile(
    rf"^(?P<total>{_MONEY_PATTERN})\s+(?P<tva>\d+)$"
)
_NAME_WITH_TOTAL_RE = re.compile(
    rf"^(?P<name>.*?)(?P<total>{_MONEY_PATTERN})\s+(?P<tva>\d+)$"
)
_UNIT_BREAKDOWN_RE = re.compile(
    rf"^(?P<quantity>{_QUANTITY_PATTERN})\s*[xX]\s*"
    rf"(?P<unit>{_MONEY_PATTERN})\s*€\s*$"
)
_WEIGHT_BREAKDOWN_RE = re.compile(
    rf"^(?P<weight>{_QUANTITY_PATTERN})\s*kg\s*[xX]\s*"
    rf"(?P<unit>{_MONEY_PATTERN})\s*€\s*/?\s*kg\s*$",
    re.IGNORECASE,
)
_UNIT_COMBO_RE = re.compile(
    rf"^(?P<quantity>{_QUANTITY_PATTERN})\s*[xX]\s*"
    rf"(?P<unit>{_MONEY_PATTERN})\s*€\s+"
    rf"(?P<total>{_MONEY_PATTERN})\s+(?P<tva>\d+)$"
)
_WEIGHT_COMBO_RE = re.compile(
    rf"^(?P<weight>{_QUANTITY_PATTERN})\s*kg\s*[xX]\s*"
    rf"(?P<unit>{_MONEY_PATTERN})\s*€\s*/?\s*kg\s+"
    rf"(?P<total>{_MONEY_PATTERN})\s+(?P<tva>\d+)$",
    re.IGNORECASE,
)


def _clean_ocr_line(line: str) -> str:
    """Fix frequent OCR misreads of amounts and VAT codes at the end of a line."""
    line = _NOISY_AMOUNT_RE.sub(r"\g<int>.\g<dec>\g<tva>", line)
    return _NOISY_TVA_SUFFIX_RE.sub(r"\g<head>", line)


def categorize_item(item_name: str, categories: Mapping[str, Sequence[str]]) -> str:
    item_lower = item_name.lower()
    for category, keywords in categories.items():
        if any(keyword in item_lower for keyword in keywords):
            return category
    return "autre"


def normalize_enseigne(value: str) -> str:
    normalized = re.sub(r"\s*\.\s*", ".", value).strip()
    tokens = normalized.split()
    result = []
    index = 0

    while index < len(tokens):
        end = index
        while end < len(tokens) and len(tokens[end]) == 1 and tokens[end].isalpha():
            end += 1
        if end - index >= 3:
            result.append("".join(tokens[index:end]))
            index = end
        else:
            result.append(tokens[index])
            index += 1

    return " ".join(result).strip()


def _parse_date(lines: Sequence[str]) -> Optional[datetime]:
    for line in lines:
        match = _DATE_FR_RE.search(line)
        if match:
            month = _MONTHS[match.group("month").lower()]
            time = match.group("time")
            try:
                return datetime.strptime(
                    "{}-{}-{}{}".format(
                        match.group("year"),
                        month,
                        match.group("day"),
                        " {}".format(time) if time else "",
                    ),
                    "%Y-%m-%d %H:%M" if time else "%Y-%m-%d",
                )
            except ValueError as error:
                logger.warning("Date OCR invalide %r: %s", line, error)

        match = _DATE_ISO_RE.search(line)
        if match:
            try:
                return datetime(
                    int(match.group("year")),
                    int(match.group("month")),
                    int(match.group("day")),
                )
            except ValueError as error:
                logger.warning("Date OCR invalide %r: %s", line, error)

        match = _DATE_NUM_RE.search(line)
        if match:
            year = int(match.group("year"))
            if len(match.group("year")) == 2:
                year += 2000
            try:
                return datetime(year, int(match.group("month")), int(match.group("day")))
            except ValueError as error:
                logger.warning("Date OCR invalide %r: %s", line, error)

    return None


def _parse_ticket_summary(
    lines: Sequence[str],
) -> tuple[Optional[int], Optional[float]]:
    fallback_total = None
    for line in lines:
        if not _TOTAL_LINE_RE.match(line):
            continue

        count_match = _TICKET_ARTICLE_COUNT_RE.match(line)
        count = int(count_match.group("count")) if count_match else None
        amounts = _MONEY_RE.findall(line)
        total = float(amounts[-1].replace(",", ".")) if amounts else None
        if count is not None and total is not None:
            return count, total
        if total is not None and fallback_total is None:
            fallback_total = total

    return None, fallback_total


def _build_item(
    name: str,
    section: Optional[str],
    total: float,
    tva_code: str,
    quantity: float = 1,
    unit_price: Optional[float] = None,
    weight: Optional[float] = None,
    price_per_kg: Optional[float] = None,
    categories: Optional[Mapping[str, Sequence[str]]] = None,
) -> ParsedItem:
    return {
        "article": name.strip(),
        "rayon": section,
        "prix_unitaire": unit_price,
        "quantite": quantity,
        "poids_kg": weight,
        "prix_kg": price_per_kg,
        "prix_total": total,
        "tva_code": tva_code,
        "categorie": categorize_item(name, categories or {}),
    }


def parse_ticket(
    ocr_text: str,
    source_file: str = "",
    categories: Optional[Mapping[str, Sequence[str]]] = None,
) -> ParsedTicket:
    """Parse one OCR text into receipt metadata and detailed items."""
    lines = [
        _clean_ocr_line(line.strip())
        for line in ocr_text.splitlines()
        if line.strip() and not _PAGE_HEADER_RE.match(line.strip())
    ]
    if not lines:
        raise ValueError("Le texte OCR du ticket est vide")

    enseigne = normalize_enseigne(lines[0]) or "inconnu"
    parsed_date = _parse_date(lines)
    article_count, ticket_total = _parse_ticket_summary(lines)

    start_index = next(
        (index for index, line in enumerate(lines) if line.startswith(">>")),
        0,
    )
    items: List[ParsedItem] = []
    section = None
    current_name = None
    pending = None

    for line in lines[start_index:]:
        if _TOTAL_LINE_RE.match(line):
            break
        if line.startswith(">>"):
            section = line.replace(">>", "").strip() or None
            current_name = None
            pending = None
            continue

        unit_combo = _UNIT_COMBO_RE.match(line)
        if unit_combo:
            quantity = float(unit_combo.group("quantity").replace(",", "."))
            unit_price = float(unit_combo.group("unit").replace(",", "."))
            items.append(
                _build_item(
                    current_name or "(inconnu)",
                    section,
                    float(unit_combo.group("total").replace(",", ".")),
                    unit_combo.group("tva"),
                    quantity=quantity,
                    unit_price=unit_price,
                    categories=categories,
                )
            )
            current_name = None
            pending = None
            continue

        weight_combo = _WEIGHT_COMBO_RE.match(line)
        if weight_combo:
            weight = float(weight_combo.group("weight").replace(",", "."))
            price_per_kg = float(weight_combo.group("unit").replace(",", "."))
            items.append(
                _build_item(
                    current_name or "(inconnu)",
                    section,
                    float(weight_combo.group("total").replace(",", ".")),
                    weight_combo.group("tva"),
                    quantity=weight,
                    unit_price=price_per_kg,
                    weight=weight,
                    price_per_kg=price_per_kg,
                    categories=categories,
                )
            )
            current_name = None
            pending = None
            continue

        unit_breakdown = _UNIT_BREAKDOWN_RE.match(line)
        if unit_breakdown:
            pending = (
                "units",
                float(unit_breakdown.group("quantity").replace(",", ".")),
                float(unit_breakdown.group("unit").replace(",", ".")),
            )
            continue

        weight_breakdown = _WEIGHT_BREAKDOWN_RE.match(line)
        if weight_breakdown:
            pending = (
                "weight",
                float(weight_breakdown.group("weight").replace(",", ".")),
                float(weight_breakdown.group("unit").replace(",", ".")),
            )
            continue

        name_total = _NAME_WITH_TOTAL_RE.match(line)
        if name_total and any(character.isalpha() for character in name_total.group("name")):
            total = float(name_total.group("total").replace(",", "."))
            items.append(
                _build_item(
                    name_total.group("name"),
                    section,
                    total,
                    name_total.group("tva"),
                    unit_price=total,
                    categories=categories,
                )
            )
            current_name = None
            pending = None
            continue

        total_with_tva = _TOTAL_WITH_TVA_RE.match(line)
        if total_with_tva and current_name:
            total = float(total_with_tva.group("total").replace(",", "."))
            tva_code = total_with_tva.group("tva")
            if pending and pending[0] == "units":
                _, quantity, unit_price = pending
                items.append(
                    _build_item(
                        current_name,
                        section,
                        total,
                        tva_code,
                        quantity=quantity,
                        unit_price=unit_price,
                        categories=categories,
                    )
                )
            elif pending and pending[0] == "weight":
                _, weight, price_per_kg = pending
                items.append(
                    _build_item(
                        current_name,
                        section,
                        total,
                        tva_code,
                        quantity=weight,
                        unit_price=price_per_kg,
                        weight=weight,
                        price_per_kg=price_per_kg,
                        categories=categories,
                    )
                )
            else:
                items.append(
                    _build_item(
                        current_name,
                        section,
                        total,
                        tva_code,
                        unit_price=total,
                        categories=categories,
                    )
                )
            current_name = None
            pending = None
            continue

        if any(character.isalpha() for character in line):
            current_name = line
            pending = None

    if not items:
        raise ValueError("Aucun article reconnu dans le texte OCR")

    detected_units = sum(
        1 if item["poids_kg"] is not None else int(round(item["quantite"]))
        for item in items
    )

    return {
        "fichier_source": source_file,
        "enseigne": enseigne,
        "date": (
            parsed_date.strftime("%Y-%m-%d %H:%M")
            if parsed_date and parsed_date.time() != datetime.min.time()
            else parsed_date.date().isoformat() if parsed_date else None
        ),
        "total_ticket": ticket_total,
        "total_remises": None,
        "nombre_articles_ticket": article_count,
        "articles_detectes": len(items),
        "unites_detectees": detected_units,
        "articles": items,
    }
