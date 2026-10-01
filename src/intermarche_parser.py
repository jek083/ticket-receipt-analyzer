"""Parser for Intermarché receipts (Hyper and Super formats).

The logo is an image, so the retailer name is absent from the text: routing is
done by ``parser_dispatcher`` from the layout of the receipt. Both formats share
the same layout; the store code (``M11669``) tells Hyper and Super apart.
"""

import logging
import re
from typing import List, Mapping, Optional, Sequence, Tuple

import config
from src.ocr_common import build_item, parse_amount
from src.parse_ticket_enrichi import ParsedItem, ParsedTicket

logger = logging.getLogger(__name__)

_AMOUNT = r"\d+,\d{2}"
_ITEM_LINE_RE = re.compile(
    rf"^(?P<name>.+?)\s+(?P<price>{_AMOUNT})\s+EUR\s+(?P<tva>[A-Z])$"
)
_QUANTITY_LINE_RE = re.compile(
    rf"^(?P<quantity>\d+)\s*[xX]\s*(?P<unit>{_AMOUNT})\s+EUR\s+"
    rf"(?P<total>{_AMOUNT})\s+EUR\s+(?P<tva>[A-Z])$"
)
_WEIGHT_LINE_RE = re.compile(
    rf"^(?P<weight>\d+,\d{{3}})\s*kg\s*[xX]\s*(?P<unit>-?{_AMOUNT})\s*EURO/kg\s+"
    rf"(?P<total>-?{_AMOUNT})\s+EUR\s+(?P<tva>[A-Z])$",
    re.IGNORECASE,
)
_DISCOUNT_LINE_RE = re.compile(rf"^(?P<name>.+?)\s+-(?P<amount>{_AMOUNT})(?:\s+EUR)?$")
_TOTAL_RE = re.compile(rf"^MONTANT\s+DU\s+(?P<amount>{_AMOUNT})\s+EUR$", re.IGNORECASE)
_ARTICLE_COUNT_RE = re.compile(
    r"^Nombre\s+d'articles\s+vendus\s*=\s*(?P<count>\d+)$", re.IGNORECASE
)
_DATE_RE = re.compile(
    r"^(?P<time>\d{2}:\d{2}):\d{2}\s+(?P<day>\d{1,2})/(?P<month>\d{2})/(?P<year>\d{4})$"
)
_STORE_CODE_RE = re.compile(r"^(?P<code>M\d{5})\s+C\d{3}\s+O\d{4}\s+T\d{4}$")
# Lines printed between articles that are not articles (vignettes, eco-participation).
_IGNORED_RE = re.compile(
    r"^(?:\d+\s+.*vign|dont\b.*eco-?part)", re.IGNORECASE
)


def _add_weighed_item(
    items: List[ParsedItem],
    name: str,
    match: "re.Match[str]",
    categories: Optional[Mapping[str, Sequence[str]]],
) -> None:
    total = parse_amount(match.group("total"))
    if total < 0:
        # A negative weighed line cancels the earlier line of the same article.
        for index in range(len(items) - 1, -1, -1):
            if items[index]["article"] == name and items[index]["prix_total"] == -total:
                del items[index]
                return
        logger.warning("Annulation Intermarché sans article correspondant: %s", name)
        return

    item = build_item(name, total, categories, tva_code=match.group("tva"))
    item["poids_kg"] = parse_amount(match.group("weight"))
    item["prix_kg"] = parse_amount(match.group("unit"))
    item["prix_unitaire"] = item["prix_kg"]
    items.append(item)


def _parse_items(
    lines: Sequence[str],
    categories: Optional[Mapping[str, Sequence[str]]],
) -> Tuple[List[ParsedItem], float]:
    items: List[ParsedItem] = []
    discounts = 0.0
    pending_name: Optional[str] = None

    for line in lines:
        if _TOTAL_RE.match(line):
            break
        if _IGNORED_RE.match(line):
            continue

        weight_match = _WEIGHT_LINE_RE.match(line)
        if weight_match and pending_name:
            _add_weighed_item(items, pending_name, weight_match, categories)
            pending_name = None
            continue

        quantity_match = _QUANTITY_LINE_RE.match(line)
        if quantity_match and pending_name:
            quantity = int(quantity_match.group("quantity"))
            items.append(
                build_item(
                    pending_name,
                    parse_amount(quantity_match.group("total")),
                    categories,
                    tva_code=quantity_match.group("tva"),
                    quantity=quantity,
                    unit_price=parse_amount(quantity_match.group("unit")),
                )
            )
            pending_name = None
            continue

        item_match = _ITEM_LINE_RE.match(line)
        if item_match:
            items.append(
                build_item(
                    _clean_name(item_match.group("name")),
                    parse_amount(item_match.group("price")),
                    categories,
                    tva_code=item_match.group("tva"),
                )
            )
            pending_name = None
            continue

        discount_match = _DISCOUNT_LINE_RE.match(line)
        if discount_match and items:
            discounts += parse_amount(discount_match.group("amount"))
            pending_name = None
            continue

        # A name alone on its line announces a "N X unit price" line.
        pending_name = _clean_name(line) if _looks_like_article_name(line) else None

    return items, round(discounts, 2)


def _clean_name(name: str) -> str:
    # A leading star flags a promotion; the discount is on a following line.
    return name.lstrip("*").strip()


def _looks_like_article_name(line: str) -> bool:
    return bool(re.search(r"[A-Za-z]{3}", line)) and not re.search(r"\d+,\d{2}", line)


def _find(lines: Sequence[str], pattern: "re.Pattern[str]") -> Optional["re.Match[str]"]:
    return next((match for match in map(pattern.match, lines) if match), None)


def _parse_date(lines: Sequence[str]) -> Optional[str]:
    match = _find(lines, _DATE_RE)
    if not match:
        return None
    return (
        f"{match.group('year')}-{match.group('month')}-{int(match.group('day')):02d} "
        f"{match.group('time')}"
    )


def _store_format(lines: Sequence[str]) -> str:
    match = _find(lines, _STORE_CODE_RE)
    code = match.group("code") if match else None
    return config.INTERMARCHE_FORMATS.get(code, config.INTERMARCHE_UNKNOWN_FORMAT)


def parse_intermarche_ticket(
    ocr_text: str,
    source_file: str = "",
    categories: Optional[Mapping[str, Sequence[str]]] = None,
) -> ParsedTicket:
    """Parse an Intermarché receipt into the shared ticket schema.

    Item prices are gross prices: discounts printed among the articles feed
    ``total_remises``. A discount voucher listed among the payments is not one.
    """
    lines = [line.strip() for line in ocr_text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("Le texte du ticket Intermarché est vide")

    items, discounts = _parse_items(lines, categories)
    if not items:
        raise ValueError("Aucun article Intermarché reconnu dans le texte")

    total = _find(lines, _TOTAL_RE)
    count = _find(lines, _ARTICLE_COUNT_RE)
    return {
        "fichier_source": source_file,
        "enseigne": "Intermarché",
        "date": _parse_date(lines),
        "total_ticket": parse_amount(total.group("amount")) if total else None,
        "total_remises": discounts or None,
        "nombre_articles_ticket": int(count.group("count")) if count else None,
        "articles_detectes": len(items),
        "unites_detectees": sum(int(round(item["quantite"])) for item in items),
        "articles": items,
        "format_magasin": _store_format(lines),
    }
