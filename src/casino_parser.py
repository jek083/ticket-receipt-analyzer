"""Parser for Casino (hyperFrais) receipts read by OCR.

Article line: ``NOM  2.81€`` with no quantity, VAT or department. A weighed
article is followed by ``0.212kg X 19.95€/kg`` and a multiple by ``10 x 0.70€``.
Item prices are gross: ``TOTAL ACHATS`` is their sum, the ``VOS REMISES`` block
lists the discounts and the customer pays ``TOTAL ACHATS − Total remises``.

The OCR is unreliable on the amount of a line and on ``TOTAL A REGLER`` (large
characters). Each line has up to three candidate amounts (printed, recomputed
from its weight or quantity, printed without a stray digit) and the combination
that adds up to ``TOTAL ACHATS`` is kept. The amount paid is confirmed by
several printed figures.
"""

import itertools
import re
from dataclasses import dataclass
from datetime import datetime
from typing import List, Mapping, Optional, Sequence, Tuple

from src.ocr_common import build_item, parse_amount
from src.parse_ticket_enrichi import ParsedItem, ParsedTicket

_AMOUNT = r"\d{1,3}\s?[.,]\s?\d{2}"
_TOLERANCE = 0.005
_MAX_AMBIGUOUS_LINES = 12
_HEADER_SCAN_LINES = 10
_MIN_YEAR = 2000

_HEADER_END_RE = re.compile(
    r"gratuit|appel|hyper\s?fra|\b[3L(|]?\s?[39]\d{3}\b|question|num[ée]ro",
    re.IGNORECASE,
)
_TOTAL_PURCHASES_RE = re.compile(rf"^\W*total\s+achats\D{{0,4}}(?P<amount>{_AMOUNT})", re.IGNORECASE)
_DISCOUNT_HEADER_RE = re.compile(r"^\W*vos\s+remises", re.IGNORECASE)
_TOTAL_DISCOUNT_RE = re.compile(
    rf"^\W*total\s+remi\s?ses\D{{0,4}}(?P<amount>{_AMOUNT})", re.IGNORECASE
)
_TO_PAY_RE = re.compile(
    r"^\W*total\s+[aà]\s+r[eé]gler\s*[({\[|]?\s*(?P<count>\d{1,3})?\s*[)}\]|]?"
    rf"\s*(?P<amount>{_AMOUNT})?",
    re.IGNORECASE,
)
_PAYMENT_RE = re.compile(
    rf"^\W*(?:cb|carte|esp[eè]ces?|ch[eè]que)\b[^\d]*(?P<amount>{_AMOUNT})\s*\S{{0,2}}$",
    re.IGNORECASE,
)
_DATE_RE = re.compile(
    r"date\s*:?\s*(?P<day>\d{2})\s*/\s*(?P<month>\d{2})\s*/\s*(?P<year>\d{4})"
    r"(?:\D{0,12}?(?P<hour>\d{2})\s*[:h-]\s*(?P<minute>\d{2}))?",
    re.IGNORECASE,
)
_WEIGHT_RE = re.compile(
    r"^\W*(?P<weight>[\dO]\s?[.,]?\s?[\dO]{3})\s*k\w\s*[xX]+\s*(?P<rest>.*)$"
)
_QUANTITY_RE = re.compile(rf"^\W*(?P<quantity>\d{{1,3}})\s*[xX]\s*[.\s]*(?P<unit>{_AMOUNT})")
_ITEM_RE = re.compile(
    rf"^(?P<name>.*?\S)\s+(?P<amount>-?{_AMOUNT})\s*[€E¢&$%a-zA-Z0-9]?\s*[|:;.,'`]*$"
)
_LEADING_NOISE_RE = re.compile(r"^[\s|:;.,‘'’“”`+*!\-]+")
_TRAILING_NOISE_RE = re.compile(r"[\s|:;.,‘'’“”`]+$")
_LETTERS_RE = re.compile(r"[A-Za-zÀ-ÿ]{3}")


@dataclass
class _Line:
    name: str
    amount: float
    quantity: int = 1
    unit_price: Optional[float] = None
    weight: Optional[float] = None
    price_per_kg: Optional[float] = None

    def computed(self) -> Optional[float]:
        if self.weight is not None and self.price_per_kg is not None:
            return round(self.weight * self.price_per_kg, 2)
        if self.unit_price is not None and self.quantity > 1:
            return round(self.unit_price * self.quantity, 2)
        return None


def _amount(text: str) -> float:
    return parse_amount(re.sub(r"\s", "", text))


def _clean_name(raw: str) -> str:
    return _TRAILING_NOISE_RE.sub("", _LEADING_NOISE_RE.sub("", raw))


def _header_end(lines: Sequence[str]) -> int:
    """Index of the first line after the logo and the customer-service banner."""
    last = -1
    for index, line in enumerate(lines[:_HEADER_SCAN_LINES]):
        if _HEADER_END_RE.search(line):
            last = index
    return last + 1


def _apply_weight(line: _Line, weight_match: "re.Match[str]") -> None:
    digits = re.sub(r"[\s.,]", "", weight_match.group("weight").replace("O", "0"))
    weight = float(f"{digits[0]}.{digits[1:]}")
    line.weight = weight
    # The price per kilo is misread as often as the amount: keep it only when it
    # explains the printed amount.
    rest = weight_match.group("rest")
    per_kilo = re.search(_AMOUNT, rest)
    if per_kilo:
        price = _amount(per_kilo.group(0))
        if abs(round(weight * price, 2) - line.amount) <= 0.02:
            line.price_per_kg = price


def _parse_items(lines: Sequence[str]) -> Tuple[List[_Line], Optional[float]]:
    """Return the articles and the printed ``TOTAL ACHATS``."""
    articles: List[_Line] = []
    for text in lines[_header_end(lines):]:
        total = _TOTAL_PURCHASES_RE.match(text)
        if total:
            return articles, _amount(total.group("amount"))

        weight = _WEIGHT_RE.match(text)
        if weight:
            if articles:
                _apply_weight(articles[-1], weight)
            continue

        quantity = _QUANTITY_RE.match(text)
        if quantity:
            if articles:
                articles[-1].quantity = int(quantity.group("quantity"))
                articles[-1].unit_price = _amount(quantity.group("unit"))
            continue

        item = _ITEM_RE.match(text)
        if item:
            name = _clean_name(item.group("name"))
            if _LETTERS_RE.search(name):
                articles.append(_Line(name, _amount(item.group("amount"))))
    return articles, None


def _parse_discounts(lines: Sequence[str]) -> Tuple[float, Optional[float]]:
    """Return the sum of the discount lines and the printed ``Total remises``."""
    printed = next(
        (_amount(m.group("amount")) for m in map(_TOTAL_DISCOUNT_RE.match, lines) if m),
        None,
    )
    start = next((i for i, line in enumerate(lines) if _DISCOUNT_HEADER_RE.match(line)), None)
    if start is None:
        return 0.0, printed

    summed = 0.0
    for text in lines[start + 1:]:
        if _TOTAL_DISCOUNT_RE.match(text) or _TO_PAY_RE.match(text) or _PAYMENT_RE.match(text):
            break
        item = _ITEM_RE.match(text)
        if item:
            # The minus sign is sometimes lost by the OCR: every amount is a discount.
            summed += abs(_amount(item.group("amount")))
    return round(summed, 2), printed


def _pick_discounts(summed: float, printed: Optional[float], purchases: Optional[float],
                    paid: Optional[float]) -> float:
    printed = None if printed is None else abs(printed)
    candidates = [value for value in (printed, summed or None) if value is not None]
    if purchases is not None and paid is not None:
        for value in candidates:
            if abs(purchases - value - paid) <= _TOLERANCE:
                return value
    return candidates[0] if candidates else 0.0


def _variants(line: _Line) -> List[float]:
    """Candidate amounts of a line; the printed one comes first."""
    values = [line.amount]
    computed = line.computed()
    if computed is not None:
        values.append(computed)
    whole, _, cents = f"{line.amount:.2f}".partition(".")
    if len(whole) >= 2:
        # A stray digit is read in front of (or inside) the price: 93.95 -> 3.95.
        for position in range(len(whole)):
            values.append(float(f"{whole[:position] + whole[position + 1:]}.{cents}"))
    return list(dict.fromkeys(values))


def _reconcile(lines: Sequence[_Line], target: Optional[float]) -> List[float]:
    choices = [_variants(line) for line in lines]
    chosen = [options[0] for options in choices]
    ambiguous = [index for index, options in enumerate(choices) if len(options) > 1]
    if target is None or not ambiguous or len(ambiguous) > _MAX_AMBIGUOUS_LINES:
        return chosen

    fixed = sum(chosen[i] for i in range(len(chosen)) if i not in ambiguous)
    best: Optional[Tuple[float, int, Tuple[float, ...]]] = None
    for combination in itertools.product(*(choices[i] for i in ambiguous)):
        gap = abs(round(fixed + sum(combination) - target, 2))
        deviations = sum(v != choices[i][0] for i, v in zip(ambiguous, combination))
        if best is None or (gap, deviations) < best[:2]:
            best = (gap, deviations, combination)
    if best is not None and best[0] <= _TOLERANCE:
        for index, value in zip(ambiguous, best[2]):
            chosen[index] = value
    return chosen


def _pick_total(candidates: Sequence[Optional[float]]) -> Optional[float]:
    """Keep the figure confirmed by two sources, else the first one available."""
    values = [value for value in candidates if value is not None]
    for index, value in enumerate(values):
        if any(abs(value - other) <= _TOLERANCE for other in values[index + 1:]):
            return value
    return values[0] if values else None


def _parse_date(lines: Sequence[str]) -> Optional[str]:
    """Return the date only when day, month and year are all readable and plausible."""
    for line in lines:
        match = _DATE_RE.search(line)
        if not match:
            continue
        try:
            day = datetime(
                int(match.group("year")), int(match.group("month")), int(match.group("day"))
            )
        except ValueError:
            continue
        if not _MIN_YEAR <= day.year <= datetime.now().year + 1:
            continue
        result = f"{day:%Y-%m-%d}"
        if match.group("hour") and int(match.group("hour")) < 24 and int(match.group("minute")) < 60:
            result += f" {match.group('hour')}:{match.group('minute')}"
        return result
    return None


def _find_payment(lines: Sequence[str]) -> Optional[float]:
    amounts = [
        _amount(match.group("amount"))
        for match in map(_PAYMENT_RE.match, lines)
        if match
    ]
    return round(sum(amounts), 2) if amounts else None


def parse_casino_ticket(
    ocr_text: str,
    source_file: str = "",
    categories: Optional[Mapping[str, Sequence[str]]] = None,
) -> ParsedTicket:
    """Parse a Casino receipt into the shared ticket schema.

    Item prices are gross; ``total_remises`` holds the discounts and
    ``total_ticket`` the amount paid. ``nombre_articles_ticket`` is the number of
    units printed after ``TOTAL A REGLER`` when it is readable. The date stays
    ``None`` unless day, month and year are all legible.
    """
    lines = [line.strip() for line in ocr_text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("Le texte OCR du ticket Casino est vide")

    articles, purchases = _parse_items(lines)
    if not articles:
        raise ValueError("Aucun article Casino reconnu dans le texte OCR")

    summed, printed_discounts = _parse_discounts(lines)
    to_pay = next((m for m in map(_TO_PAY_RE.match, lines) if m), None)
    printed_to_pay = (
        _amount(to_pay.group("amount")) if to_pay and to_pay.group("amount") else None
    )
    payment = _find_payment(lines)
    discounts = _pick_discounts(summed, printed_discounts, purchases, payment or printed_to_pay)
    computed = None if purchases is None else round(purchases - discounts, 2)
    total = _pick_total([computed, payment, printed_to_pay])

    totals = _reconcile(articles, purchases)
    items: List[ParsedItem] = []
    for article, amount in zip(articles, totals):
        item = build_item(
            article.name,
            amount,
            categories,
            quantity=article.quantity,
            unit_price=article.unit_price if article.quantity > 1 else None,
        )
        if article.weight is not None:
            item["poids_kg"] = article.weight
            item["prix_kg"] = article.price_per_kg
            item["prix_unitaire"] = article.price_per_kg if article.price_per_kg else amount
        items.append(item)

    return {
        "fichier_source": source_file,
        "enseigne": "Casino",
        "date": _parse_date(lines),
        "total_ticket": total,
        "total_remises": discounts or None,
        "nombre_articles_ticket": (
            int(to_pay.group("count")) if to_pay and to_pay.group("count") else None
        ),
        "articles_detectes": len(items),
        "unites_detectees": sum(int(round(item["quantite"])) for item in items),
        "articles": items,
    }
