"""Select the retailer-specific parser from the OCR text."""

import re
from typing import Callable, Mapping, Optional, Sequence

import config
from src.auchan_parser import parse_auchan_ticket
from src.carrefour_parser import parse_carrefour_ticket
from src.casino_parser import parse_casino_ticket
from src.intermarche_parser import parse_intermarche_ticket
from src.lidl_parser import parse_lidl_ticket
from src.marcel_fils_parser import parse_marcel_fils_ticket
from src.parse_ticket_enrichi import ParsedTicket, parse_ticket as parse_leclerc_ticket
from src.picard_parser import parse_picard_ticket

Parser = Callable[..., ParsedTicket]

# Order matters when several stores are found in the same text.
_PARSERS: Sequence[tuple[str, Parser]] = (
    ("picard", parse_picard_ticket),
    ("leclerc", parse_leclerc_ticket),
    ("carrefour", parse_carrefour_ticket),
    ("auchan", parse_auchan_ticket),
    ("intermarche", parse_intermarche_ticket),
    ("lidl", parse_lidl_ticket),
    ("marcel_fils", parse_marcel_fils_ticket),
    ("casino", parse_casino_ticket),
)
# The retailer name sits in the header; products may quote other brands.
_HEADER_LINES = 8



def _markers(*patterns: str) -> tuple["re.Pattern[str]", ...]:
    return tuple(re.compile(p, re.IGNORECASE | re.MULTILINE) for p in patterns)


# Some logos are images (Intermarché) or misread by the OCR (Lidl): those receipts are
# recognized by their layout, with patterns tolerant to OCR noise.
_INTERMARCHE_MARKERS = _markers(
    r"nombre d'articles vendus",
    r"r[ée]capitulatif tva",
    r"ticket [àa] conserver pour [ée]change",
    r"carte de fid[ée]lit[ée]",
    r"total [ée]ligible trd",
    r"^M\d{5} C\d{3} O\d{4} T\d{4}$",
)
_LIDL_MARKERS = _markers(
    r"ticket de vente",
    r"^\W*[aà] ?payer\b",
    r"total promotion",
    r"nombre de \S*gnes",
    r"mont\.?\s*ttc",
    r"lidl plus",
    r"\b[l1i]id[l1i]\.fr",
)
_MARCEL_FILS_MARKERS = _markers(
    r"montant net h\w",
    r"\(\s*taux",
    r"^total ttc",
    r"^total h\w",
    r"reglement",
)
_CASINO_MARKERS = _markers(
    r"^\W*total achats",
    r"^\W*vos remises",
    r"^\W*total [aà] r[ée]gler",
    r"^\W*cb emv",
    r"hyper\s?fra",
    r"service gratuit",
    r"total remises",
)
_LAYOUTS: Sequence[tuple[Parser, tuple["re.Pattern[str]", ...], int]] = (
    (parse_intermarche_ticket, _INTERMARCHE_MARKERS, 3),
    (parse_lidl_ticket, _LIDL_MARKERS, 3),
    (parse_marcel_fils_ticket, _MARCEL_FILS_MARKERS, 3),
    (parse_casino_ticket, _CASINO_MARKERS, 3),
)


def _matches_store(ocr_text: str, store: str) -> bool:
    text = ocr_text.casefold()
    return any(
        keyword.casefold() in text
        for keyword in config.STORES.get(store, [])
    )


def _header(ocr_text: str) -> str:
    lines = [line for line in ocr_text.splitlines() if line.strip()]
    return "\n".join(lines[:_HEADER_LINES])


def _select_by_layout(ocr_text: str) -> Optional[Parser]:
    for parser, markers, minimum in _LAYOUTS:
        if sum(1 for marker in markers if marker.search(ocr_text)) >= minimum:
            return parser
    return None


def _select_parser(ocr_text: str) -> Optional[Parser]:
    header = _header(ocr_text)
    for store, parser in _PARSERS:
        if _matches_store(header, store):
            return parser
    # Before the full-text search: a product named "PICARD ..." must not hijack the receipt.
    by_layout = _select_by_layout(ocr_text)
    if by_layout is not None:
        return by_layout
    for store, parser in _PARSERS:
        if _matches_store(ocr_text, store):
            return parser
    return None


def parse_ticket(
    ocr_text: str,
    source_file: str = "",
    categories: Optional[Mapping[str, Sequence[str]]] = None,
) -> ParsedTicket:
    """Dispatch OCR text to the parser for its retailer."""
    parser = _select_parser(ocr_text)
    if parser is None:
        raise ValueError("Aucun parseur n'est configuré pour cette enseigne")
    return parser(ocr_text, source_file=source_file, categories=categories)
