"""Parser for Marcel&Fils receipts read by OCR.

Each article is a block of three lines::

    COUSCOUS PETIT EPEAU
    4.26 € x 1 (Taux 5,50 %)
    Montant net HT : 4.26 €

Line amounts are excluding VAT while the customer pays ``Total TTC``: prices are
converted to VAT-inclusive amounts like every other retailer. The customer
name, customer code and address printed on the receipt are never read.
"""

import logging
import re
from dataclasses import dataclass
from typing import List, Mapping, Optional, Sequence

from src.ocr_common import build_item, find_numeric_date
from src.parse_ticket_enrichi import ParsedItem, ParsedTicket

logger = logging.getLogger(__name__)

_NET_LINE_RE = re.compile(r"montant\s+net\s+h\w*\s*:?\s*(?P<amount>.*)$", re.IGNORECASE)
# "4.26 € x 1 (Taux 5,50 %)": the OCR garbles the currency sign and the word "Taux"
# (faux, Taux.5,50-X), so each part is read on its own.
_PRICE_LINE_RE = re.compile(
    r"(?:(?P<unit>\d+[.,]?\d{2})\s*\S{0,2}\s*)?[xX]\s*_?\s*(?P<quantity>\d+)\s*\(",
)
_UNIT_AND_QUANTITY_RE = re.compile(
    r"(?<!\d)\d+[.,]\d{2}\s*\S{0,2}\s*[xX]\s*_?\s*\d+\s*\("
)
_RATE_RE = re.compile(r"\(\s*\w{0,6}[\s.]*(?P<rate>\d+[.,]\d{1,2})")
_TAUX_RE = re.compile(r"\(\s*taux\b", re.IGNORECASE)
_TOTAL_HT_RE = re.compile(r"^\W*total\s+h\w*\s*:?\s*(?P<amount>.*)$", re.IGNORECASE)
_TOTAL_TTC_RE = re.compile(r"^\W*total\s+t[tiIl]c\s*:?\s*(?P<amount>.*)$", re.IGNORECASE)
_PAYMENT_RE = re.compile(
    r"^\W*(?:esp[eè]ces|carte|cb|ch[eè]que)[^:]*:\s*(?P<amount>.*)$", re.IGNORECASE
)
_SUMMARY_TVA_RE = re.compile(r"^\W*tva\s+taux\s*(?P<rate>\d+[.,]\d{1,2})", re.IGNORECASE)
_AMOUNT_RE = re.compile(r"(?<!\d)(?P<whole>\d+)[.,](?P<cents>\d{2})(?!\d)")
_CENTS_ONLY_RE = re.compile(r"(?<!\d)(?P<digits>\d{3,})(?!\d)")
_LEADING_NOISE_RE = re.compile(r"^(?:[|!.\s]+|[lI1]\s+(?=[A-Z]))+")
_TRAILING_NOISE_RE = re.compile(r"[|.\s]+$")


@dataclass
class _Line:
    name: str
    net: float
    unit: Optional[float]
    quantity: int
    rate: Optional[float]


def _amount(text: str) -> Optional[float]:
    """Read ``4.26``, ``4,26 €`` or an OCR-dropped separator (``141€`` = 1.41)."""
    match = _AMOUNT_RE.search(text)
    if match:
        return float(f"{match.group('whole')}.{match.group('cents')}")
    digits = _CENTS_ONLY_RE.search(text)
    if digits:
        return int(digits.group("digits")) / 100
    return None


# Metropolitan VAT rates, used to correct a digit misread by the OCR (5,56 -> 5,5).
_VAT_RATES = (2.1, 5.5, 10.0, 20.0)
_VAT_SNAP_TOLERANCE = 0.5


def _rate(text: str) -> float:
    value = float(text.replace(",", "."))
    nearest = min(_VAT_RATES, key=lambda rate: abs(rate - value))
    return nearest if abs(nearest - value) <= _VAT_SNAP_TOLERANCE else value


def _clean_name(raw: str) -> str:
    return _TRAILING_NOISE_RE.sub("", _LEADING_NOISE_RE.sub("", raw))


def _is_price_line(line: str) -> bool:
    return bool(_TAUX_RE.search(line) or _UNIT_AND_QUANTITY_RE.search(line))


def _parse_lines(lines: Sequence[str]) -> List[_Line]:
    start = next((index for index, line in enumerate(lines) if _is_price_line(line)), None)
    if start is None:
        return []

    parsed: List[_Line] = []
    name: Optional[str] = None
    unit: Optional[float] = None
    quantity = 1
    rate: Optional[float] = None

    for line in lines[max(start - 1, 0):]:
        if _TOTAL_HT_RE.match(line) or _TOTAL_TTC_RE.match(line):
            break

        net_match = _NET_LINE_RE.search(line)
        if net_match:
            net = _amount(net_match.group("amount"))
            if name and net is not None:
                parsed.append(_Line(name, net, unit, quantity, rate))
            name, unit, quantity, rate = None, None, 1, None
            continue

        if _is_price_line(line):
            price = _PRICE_LINE_RE.search(line)
            if price:
                unit = _amount(price.group("unit") or "")
                quantity = int(price.group("quantity"))
            rate_match = _RATE_RE.search(line)
            if rate_match:
                rate = _rate(rate_match.group("rate"))
            continue

        cleaned = _clean_name(line)
        if re.search(r"[A-Za-zÀ-ÿ]{2}", cleaned):
            name = cleaned

    return parsed


def _find(lines: Sequence[str], pattern: "re.Pattern[str]") -> Optional[float]:
    for line in lines:
        match = pattern.match(line)
        if match:
            value = _amount(match.group("amount"))
            if value is not None:
                return value
    return None


def _to_gross(
    lines: Sequence[_Line],
    default_rate: Optional[float],
    total_ht: Optional[float],
    total_ttc: Optional[float],
) -> List[float]:
    """Convert net amounts to VAT-inclusive ones, spreading the rounding to hit Total TTC."""
    exact = []
    for line in lines:
        rate = line.rate if line.rate is not None else default_rate
        exact.append(line.net * (1 + (rate or 0) / 100))
    gross = [round(value, 2) for value in exact]

    # Only trust the printed Total TTC when the net lines add up to the printed Total HT.
    net_matches = total_ht is not None and abs(sum(l.net for l in lines) - total_ht) <= 0.01
    if total_ttc is None or not net_matches:
        return gross

    missing_cents = round((total_ttc - sum(gross)) * 100)
    if missing_cents == 0 or abs(missing_cents) > len(gross):
        return gross

    step = 1 if missing_cents > 0 else -1
    order = sorted(
        range(len(gross)),
        key=lambda index: (exact[index] - gross[index]) * step,
        reverse=True,
    )
    for index in order[: abs(missing_cents)]:
        gross[index] = round(gross[index] + step / 100, 2)
    return gross


def parse_marcel_fils_ticket(
    ocr_text: str,
    source_file: str = "",
    categories: Optional[Mapping[str, Sequence[str]]] = None,
) -> ParsedTicket:
    """Parse a Marcel&Fils receipt into the shared ticket schema (prices incl. VAT)."""
    lines = [line.strip() for line in ocr_text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("Le texte OCR du ticket Marcel&Fils est vide")

    articles = _parse_lines(lines)
    if not articles:
        raise ValueError("Aucun article Marcel&Fils reconnu dans le texte OCR")

    total_ht = _find(lines, _TOTAL_HT_RE)
    total_ttc = _find(lines, _TOTAL_TTC_RE)
    if total_ttc is None:
        total_ttc = _find(lines, _PAYMENT_RE)
    summary_rate = next(
        (_rate(m.group("rate")) for m in map(_SUMMARY_TVA_RE.match, lines) if m), None
    )

    totals = _to_gross(articles, summary_rate, total_ht, total_ttc)
    items: List[ParsedItem] = []
    for article, gross in zip(articles, totals):
        rate = article.rate if article.rate is not None else summary_rate
        items.append(
            build_item(
                article.name,
                gross,
                categories,
                tva_code=None if rate is None else f"{rate:g}%",
                quantity=article.quantity,
                unit_price=round(gross / article.quantity, 2),
            )
        )

    return {
        "fichier_source": source_file,
        "enseigne": "Marcel&Fils",
        "date": find_numeric_date(lines),
        "total_ticket": total_ttc,
        "total_remises": None,
        "nombre_articles_ticket": None,
        "articles_detectes": len(items),
        "unites_detectees": sum(item["quantite"] for item in items),
        "articles": items,
    }
