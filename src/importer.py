"""Import PDF receipts from a pending folder into the ticket database."""

from __future__ import annotations

import itertools
import logging
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Protocol, Sequence

import pytesseract
from PIL import Image
from pdfplumber.utils.exceptions import PdfminerException
from pypdf.errors import PdfReadError

from src import ticket_db
from src.pdf_extractor import SUPPORTED_EXTENSIONS, ExtractionResult
from src.parse_ticket_enrichi import ParsedTicket
from src.parser_dispatcher import parse_ticket
from src.ticket_io import read_ticket_json, write_ticket_json

logger = logging.getLogger(__name__)

# Per-file failures: the PDF goes to the error folder and the batch continues.
_FILE_ERRORS = (
    OSError,
    ValueError,
    PdfReadError,
    PdfminerException,
    Image.DecompressionBombError,
    pytesseract.TesseractError,
)


class TextExtractor(Protocol):
    def extract(self, path: Path) -> ExtractionResult: ...

    # Optional: other readings of the same file (e.g. OCR at other scales), tried
    # only when the first one gives an inconsistent ticket.
    # def extract_alternatives(self, path: Path) -> Iterable[ExtractionResult]: ...


class _UnreadableTicket(ValueError):
    """No reading of the file gave a ticket; keeps the extraction method for the trace."""

    def __init__(self, message: str, methode: str) -> None:
        super().__init__(message)
        self.methode = methode


@dataclass
class _Reading:
    extraction: ExtractionResult
    ticket: ParsedTicket
    warnings: List[str]


@dataclass
class ImportSummary:
    imported: List[str] = field(default_factory=list)
    duplicates: Dict[str, str] = field(default_factory=dict)
    failures: Dict[str, str] = field(default_factory=dict)
    ocr_imported: List[str] = field(default_factory=list)
    warnings: Dict[str, str] = field(default_factory=dict)
    completed: Dict[str, str] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.imported) + len(self.duplicates) + len(self.failures)


def unique_path(directory: Path, name: str) -> Path:
    """Return a path in ``directory`` that does not exist yet, keeping the extension."""
    candidate = directory / name
    stem, suffix = candidate.stem, candidate.suffix
    counter = 1
    while candidate.exists():
        candidate = directory / f"{stem}_{counter}{suffix}"
        counter += 1
    return candidate


def _move(source: Path, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    destination = unique_path(directory, source.name)
    shutil.move(str(source), str(destination))
    return destination


def _consistency_warnings(ticket: ParsedTicket) -> List[str]:
    """Return the inconsistencies between the ticket and its items."""
    warnings = []
    count = ticket["nombre_articles_ticket"]
    # The printed count is either the number of units or the number of lines.
    if count is not None and count not in (
        ticket["unites_detectees"],
        ticket["articles_detectes"],
    ):
        warnings.append(
            f"Nombre d'articles différent (détecté: {ticket['unites_detectees']}, "
            f"ticket: {count})"
        )
    if ticket["date"] is None:
        warnings.append("Date du ticket non détectée")
    if ticket["total_ticket"] is None:
        warnings.append("Total du ticket non détecté")
    else:
        items_total = sum(item["prix_total"] for item in ticket["articles"])
        discounts = ticket["total_remises"] or 0
        if abs(items_total - discounts - ticket["total_ticket"]) > 0.01:
            warnings.append(
                "Somme des articles différente du total "
                f"(articles: {items_total - discounts:.2f}, remises: {discounts:.2f}, "
                f"ticket: {ticket['total_ticket']:.2f})"
            )
    return warnings


def _best_reading(
    pdf: Path,
    extractor: TextExtractor,
    categories: Optional[Mapping[str, Sequence[str]]],
) -> _Reading:
    """Parse the file, retrying other readings while the ticket is inconsistent.

    The first reading is authoritative on ties. Raises the first parsing error
    when no reading gives a ticket.
    """
    first = extractor.extract(pdf)
    alternatives = getattr(extractor, "extract_alternatives", None)
    readings = itertools.chain([first], _lazy(alternatives, pdf))

    best: Optional[_Reading] = None
    first_error: Optional[ValueError] = None
    for index, extraction in enumerate(readings):
        try:
            ticket = parse_ticket(extraction.text, source_file=pdf.name, categories=categories)
        except ValueError as error:
            first_error = first_error or error
            continue
        warnings = _consistency_warnings(ticket)
        if best is None or len(warnings) < len(best.warnings):
            best = _Reading(extraction, ticket, warnings)
            if index:
                logger.info("Lecture n°%s retenue pour %s", index + 1, pdf.name)
        if not warnings:
            break
    if best is None:
        raise _UnreadableTicket(
            str(first_error) if first_error else "Aucune lecture exploitable", first.methode
        ) from first_error
    return best


def _lazy(alternatives, pdf: Path):
    return alternatives(pdf) if alternatives is not None else ()


def _complete_existing_date(
    connection: sqlite3.Connection, existing: sqlite3.Row, ticket: ParsedTicket
) -> Optional[str]:
    """Give its date to an undated stored ticket when its rescan shows one."""
    if existing["date"] is not None or ticket["date"] is None:
        return None
    json_path = ticket_db.find_import_json(connection, existing["id"])
    original = None
    if json_path is not None and json_path.exists():
        try:
            original = read_ticket_json(json_path)
        except ValueError:
            logger.warning("JSON illisible, non mis à jour: %s", json_path)
    with connection:
        updated = ticket_db.complete_ticket_date(
            connection, existing["id"], ticket["date"], original
        )
        if original is not None:
            write_ticket_json(updated, json_path)
    return ticket["date"]


def _reject(
    connection: sqlite3.Connection,
    pdf: Path,
    file_hash: str,
    statut: str,
    message: str,
    error_dir: Path,
    methode: Optional[str] = None,
) -> None:
    """Move the file to the error folder and trace the reason in ``imports``."""
    destination: Optional[Path] = None
    try:
        with connection:
            destination = _move(pdf, error_dir)
            ticket_db.record_import(
                connection,
                nom_fichier=pdf.name,
                file_hash=file_hash,
                statut=statut,
                message=message,
                chemin_final=destination,
                methode_extraction=methode,
            )
    except (OSError, sqlite3.Error):
        if destination is not None and destination.exists() and not pdf.exists():
            shutil.move(str(destination), str(pdf))
        raise


def _store(
    connection: sqlite3.Connection,
    pdf: Path,
    file_hash: str,
    ticket: ParsedTicket,
    content_hash: str,
    done_dir: Path,
    json_dir: Path,
    methode: str,
    warnings: List[str],
) -> None:
    """Persist the ticket, then move the file; undo everything if either fails."""
    json_path = unique_path(json_dir, f"{pdf.stem}.json")
    destination: Optional[Path] = None
    write_ticket_json(ticket, json_path)
    try:
        with connection:
            ticket_id = ticket_db.insert_ticket(connection, ticket, content_hash)
            destination = _move(pdf, done_dir)
            ticket_db.record_import(
                connection,
                nom_fichier=pdf.name,
                file_hash=file_hash,
                statut=ticket_db.STATUT_TRAITE,
                ticket_id=ticket_id,
                chemin_json=json_path,
                chemin_final=destination,
                methode_extraction=methode,
                avertissements="\n".join(warnings) or None,
            )
    except (OSError, sqlite3.Error):
        if destination is not None and destination.exists() and not pdf.exists():
            shutil.move(str(destination), str(pdf))
        json_path.unlink(missing_ok=True)
        raise


def _import_file(
    connection: sqlite3.Connection,
    pdf: Path,
    extractor: TextExtractor,
    categories: Optional[Mapping[str, Sequence[str]]],
    done_dir: Path,
    error_dir: Path,
    json_dir: Path,
    summary: ImportSummary,
) -> None:
    file_hash = ticket_db.hash_file(pdf)

    previous = ticket_db.find_processed_import(connection, file_hash)
    if previous is not None:
        message = f"Fichier déjà importé (import n°{previous['id']}, {previous['nom_fichier']})"
        _reject(connection, pdf, file_hash, ticket_db.STATUT_DOUBLON, message, error_dir)
        summary.duplicates[pdf.name] = message
        logger.warning("Doublon %s: %s", pdf.name, message)
        return

    methode = None
    try:
        reading = _best_reading(pdf, extractor, categories)
        ticket = reading.ticket
        methode = reading.extraction.methode
        content_hash = ticket_db.hash_ticket(ticket)
    except pytesseract.TesseractNotFoundError:
        raise
    except _FILE_ERRORS as error:
        methode = getattr(error, "methode", methode)
        message = str(error) or type(error).__name__
        _reject(
            connection,
            pdf,
            file_hash,
            ticket_db.STATUT_ERREUR,
            message,
            error_dir,
            methode,
        )
        summary.failures[pdf.name] = message
        logger.error("Impossible de traiter %s: %s", pdf.name, message)
        return

    existing = ticket_db.find_ticket_by_hash(connection, content_hash)
    if existing is not None:
        message = (
            f"Ticket déjà présent (ticket n°{existing['id']}, "
            f"source {existing['fichier_source']})"
        )
        _reject(
            connection,
            pdf,
            file_hash,
            ticket_db.STATUT_DOUBLON,
            message,
            error_dir,
            methode,
        )
        summary.duplicates[pdf.name] = message
        logger.warning("Doublon %s: %s", pdf.name, message)
        return

    similar = ticket_db.find_similar_ticket(connection, ticket)
    if similar is not None:
        message = (
            f"Même ticket déjà présent (ticket n°{similar['id']}, "
            f"source {similar['fichier_source']}) : nouveau scan"
        )
        new_date = _complete_existing_date(connection, similar, ticket)
        if new_date is not None:
            message += f"; date {new_date} ajoutée au ticket n°{similar['id']}"
            summary.completed[pdf.name] = message
        _reject(
            connection,
            pdf,
            file_hash,
            ticket_db.STATUT_DOUBLON,
            message,
            error_dir,
            methode,
        )
        summary.duplicates[pdf.name] = message
        logger.warning("Doublon %s: %s", pdf.name, message)
        return

    warnings = reading.warnings
    for warning in warnings:
        logger.warning("%s pour %s", warning, pdf.name)
    _store(
        connection,
        pdf,
        file_hash,
        ticket,
        content_hash,
        done_dir,
        json_dir,
        methode,
        warnings,
    )
    summary.imported.append(pdf.name)
    if methode == ticket_db.METHODE_OCR:
        summary.ocr_imported.append(pdf.name)
    if warnings:
        summary.warnings[pdf.name] = "; ".join(warnings)
    logger.info("Ticket importé (%s): %s", methode, pdf.name)


def import_pending(
    connection: sqlite3.Connection,
    extractor: TextExtractor,
    *,
    pending_dir: Path,
    done_dir: Path,
    error_dir: Path,
    json_dir: Path,
    categories: Optional[Mapping[str, Sequence[str]]] = None,
) -> ImportSummary:
    """Import every PDF or image in ``pending_dir`` into the done/error folders.

    Duplicates (same file or same ticket content) are moved to ``error_dir`` and
    traced with the ``DOUBLON`` status. A file that cannot be moved or stored
    stays in ``pending_dir`` and is reported as a failure. A missing Tesseract
    installation aborts the run, leaving the remaining files untouched.
    """
    summary = ImportSummary()
    pdfs = sorted(
        path
        for path in Path(pending_dir).iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    for pdf in pdfs:
        try:
            _import_file(
                connection,
                pdf,
                extractor,
                categories,
                Path(done_dir),
                Path(error_dir),
                Path(json_dir),
                summary,
            )
        except pytesseract.TesseractNotFoundError:
            raise
        except (OSError, sqlite3.Error) as error:
            summary.failures[pdf.name] = str(error)
            logger.error("Import impossible pour %s: %s", pdf.name, error)
    return summary
