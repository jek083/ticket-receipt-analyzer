"""Parser for Lidl receipts ("Ticket de vente") read by OCR.

Article line: ``<désignation> <P.U.> <Qté> <montant> <A|B> [T]`` (``A`` = TVA 5,5 %,
``B`` = TVA 20 %, ``T`` = éligible titres-restaurant). A ``Rem …`` line gives the
discount of the article above, and a weighed article is followed by a
``0,726 kg x 2,09 EUR/kg`` line.

The OCR often misreads one of the amounts of a line. Each line carries its
total and ``P.U. × Qté``; when the two disagree, the combination that matches
the printed ``A payer`` is kept.
"""

import itertools
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Mapping, Optional, Sequence, Tuple

from src.ocr_common import build_item, parse_amount
from src.parse_ticket_enrichi import ParsedItem, ParsedTicket

logger = logging.getLogger(__name__)

_AMOUNT = r"(?<!\d)\d+[,.]\d{2}(?!\d)"
_AMOUNT_RE = re.compile(_AMOUNT)
_TABLE_HEADER_RE = re.compile(r"^\W*article\b", re.IGNORECASE)
_END_OF_ITEMS_RE = re.compile(r"^\W*(?:nombre\s+de\b|[aà]\s*payer\b|total\b)", re.IGNORECASE)
# The OCR sometimes puts a stray glyph before "Rem".
_DISCOUNT_RE = re.compile(r"^\W*(?:\w\W+)?rem\b", re.IGNORECASE)
_NEGATIVE_AMOUNT_RE = re.compile(r"(?<!\d)-\s?\d+[,.]\d{2}(?!\d)")
_WEIGHT_RE = re.compile(
    r"(?P<weight>\d+[,.]\d{3})\s*kg\s*[xX]\s*(?P<unit>\d+[,.]\d{2})", re.IGNORECASE
)
_QUANTITY_RE = re.compile(r"(?<![\d,.])(?P<quantity>\d{1,3})(?![\d,.])")
_QUANTITY_GLYPH_RE = re.compile(r"[|Il!]")
_TVA_RE = re.compile(r"(?:^|[\s\d])(?P<tva>[AB])(?:\s?T)?\W*$")
_TRAILING_NOISE_RE = re.compile(r"[\s\d/|,.:;'‘’“”~°*]+$")
_LEADING_NOISE_RE = re.compile(r"^[\s|~°*'‘’“”.,:;]+")
_PAYABLE_RE = re.compile(rf"^\W*[aà]\s*payer\D{{0,6}}(?P<amount>{_AMOUNT})", re.IGNORECASE)
_PROMOTION_RE = re.compile(
    rf"^\W*total\s+promotion\D{{0,6}}(?P<amount>{_AMOUNT})", re.IGNORECASE
)
_LINE_COUNT_RE = re.compile(r"^\W*nombre\s+de\b.*?(?P<count>\d+)\D*$", re.IGNORECASE)
_DATE_RE = re.compile(
    r"(?<![\d.])(?P<day>\d{2})\.(?P<month>\d{2})\.(?P<year>\d{2})\s+"
    r"(?P<time>\d{2}:\d{2}):\d{2}"
)
# Brute-force reconciliation stays cheap: 2 ** 12 combinations at most.
_MAX_AMBIGUOUS_LINES = 12


@dataclass
class _Line:
    name: str
    unit_price: Optional[float]
    quantity: int
    printed_total: Optional[float]
    tva_code: Optional[str]
    weight: Optional[float] = None
    price_per_kg: Optional[float] = None
    candidates: List[float] = field(default_factory=list)


def _clean_name(raw: str) -> str:
    return _TRAILING_NOISE_RE.sub("", _LEADING_NOISE_RE.sub("", raw)).strip()


def _parse_article_line(line: str) -> Optional[_Line]:
    matches = list(_AMOUNT_RE.finditer(line))
    if not matches:
        return None

    first = matches[0]
    name = _clean_name(line[: first.start()])
    if not re.search(r"[A-Za-zÀ-ÿ]{2}", name):
        return None

    after_first = line[first.end():]
    quantity_match = _QUANTITY_RE.search(after_first)
    glyph = _QUANTITY_GLYPH_RE.search(after_first[:6])
    has_quantity = quantity_match is not None or glyph is not None

    if len(matches) == 1 and not has_quantity:
        unit_price, total = None, parse_amount(first.group())
    elif len(matches) == 1:
        unit_price, total = parse_amount(first.group()), None
    else:
        unit_price, total = parse_amount(first.group()), parse_amount(matches[-1].group())

    quantity = int(quantity_match.group("quantity")) if quantity_match else 1
    tail = line[matches[-1].end():]
    tva_match = _TVA_RE.search(tail if total is not None else after_first)
    return _Line(
        name=name,
        unit_price=unit_price,
        quantity=max(quantity, 1),
        printed_total=total,
        tva_code=tva_match.group("tva") if tva_match else None,
    )


def _candidates(line: _Line) -> List[float]:
    values: List[float] = []
    if line.unit_price is not None and line.weight is None:
        computed = round(line.unit_price * line.quantity, 2)
        values.append(computed)
        # A quantity that the printed total does not confirm may be a misread glyph.
        if line.quantity != 1 and line.printed_total != computed:
            values.append(line.unit_price)
    if line.weight is not None and line.price_per_kg is not None:
        values.append(round(line.weight * line.price_per_kg, 2))
    if line.printed_total is not None:
        values.append(line.printed_total)
    unique = list(dict.fromkeys(values))
    return unique or [0.0]


def _quantity(line: _Line, total: float) -> int:
    """Quantity implied by the retained total, 1 when it is not a multiple of the price."""
    if line.weight is not None or not line.unit_price:
        return 1
    quantity = round(total / line.unit_price)
    if quantity >= 1 and abs(line.unit_price * quantity - total) <= 0.01:
        return quantity
    return 1


def _reconcile(lines: Sequence[_Line], target: Optional[float]) -> List[float]:
    """Pick one total per line, preferring ``P.U. × Qté`` and the printed payable sum."""
    choices = [_candidates(line) for line in lines]
    chosen = [options[0] for options in choices]
    ambiguous = [index for index, options in enumerate(choices) if len(options) > 1]
    if target is None or not ambiguous or len(ambiguous) > _MAX_AMBIGUOUS_LINES:
        return chosen

    fixed = sum(chosen[index] for index in range(len(chosen)) if index not in ambiguous)
    best: Optional[Tuple[float, int, Tuple[float, ...]]] = None
    for combination in itertools.product(*(choices[index] for index in ambiguous)):
        gap = abs(round(fixed + sum(combination) - target, 2))
        deviations = sum(
            value != choices[index][0] for index, value in zip(ambiguous, combination)
        )
        if best is None or (gap, deviations) < best[:2]:
            best = (gap, deviations, combination)
    if best[0] == 0:
        for index, value in zip(ambiguous, best[2]):
            chosen[index] = value
        return chosen
    # No combination matches the payable (a line may be unreadable): trust the printed totals.
    for index in ambiguous:
        printed = lines[index].printed_total
        if printed in choices[index]:
            chosen[index] = printed
    return chosen


def _parse_date(lines: Sequence[str]) -> Optional[str]:
    for line in lines:
        match = _DATE_RE.search(line)
        if not match:
            continue
        try:
            day = datetime(
                2000 + int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
            )
        except ValueError:
            continue
        return f"{day:%Y-%m-%d} {match.group('time')}"
    return None


def _find_amount(lines: Sequence[str], pattern: "re.Pattern[str]") -> Optional[float]:
    for line in lines:
        match = pattern.match(line)
        if match:
            return parse_amount(match.group("amount"))
    return None


def _parse_lines(lines: Sequence[str]) -> Tuple[List[_Line], float]:
    start = next(
        (index + 1 for index, line in enumerate(lines) if _TABLE_HEADER_RE.match(line)),
        0,
    )
    articles: List[_Line] = []
    discounts = 0.0

    for line in lines[start:]:
        if _END_OF_ITEMS_RE.match(line):
            break

        weight = _WEIGHT_RE.search(line)
        if weight:
            if articles:
                articles[-1].weight = parse_amount(weight.group("weight"))
                articles[-1].price_per_kg = parse_amount(weight.group("unit"))
            continue

        if _DISCOUNT_RE.match(line) or _NEGATIVE_AMOUNT_RE.search(line):
            amounts = _AMOUNT_RE.findall(line)
            if amounts:
                discounts += parse_amount(amounts[-1])
            continue

        article = _parse_article_line(line)
        if article:
            articles.append(article)

    return articles, round(discounts, 2)


def parse_lidl_ticket(
    ocr_text: str,
    source_file: str = "",
    categories: Optional[Mapping[str, Sequence[str]]] = None,
) -> ParsedTicket:
    """Parse a Lidl receipt into the shared ticket schema.

    Item prices are gross prices; ``Rem …`` lines feed ``total_remises``
    (``Total Promotion`` when printed). ``nombre_articles_ticket`` is the
    printed number of lines.
    """
    lines = [line.strip() for line in ocr_text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("Le texte OCR du ticket Lidl est vide")

    articles, line_discounts = _parse_lines(lines)
    if not articles:
        raise ValueError("Aucun article Lidl reconnu dans le texte OCR")

    total = _find_amount(lines, _PAYABLE_RE)
    promotion = _find_amount(lines, _PROMOTION_RE)
    discounts = promotion if promotion is not None else line_discounts
    target = None if total is None else round(total + discounts, 2)
    totals = _reconcile(articles, target)

    items: List[ParsedItem] = []
    for article, line_total in zip(articles, totals):
        item = build_item(
            article.name,
            line_total,
            categories,
            tva_code=article.tva_code,
            quantity=_quantity(article, line_total),
            unit_price=article.unit_price,
        )
        if article.weight is not None:
            item["poids_kg"] = article.weight
            item["prix_kg"] = article.price_per_kg
            item["prix_unitaire"] = article.price_per_kg
        items.append(item)

    count_match = next(
        (match for match in map(_LINE_COUNT_RE.match, lines) if match), None
    )
    return {
        "fichier_source": source_file,
        "enseigne": "Lidl",
        "date": _parse_date(lines),
        "total_ticket": total,
        "total_remises": discounts or None,
        "nombre_articles_ticket": int(count_match.group("count")) if count_match else None,
        "articles_detectes": len(items),
        "unites_detectees": sum(int(round(item["quantite"])) for item in items),
        "articles": items,
    }
