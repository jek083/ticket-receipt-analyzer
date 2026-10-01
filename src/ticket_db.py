"""Persist parsed tickets and import history in a SQLite database."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

STATUT_TRAITE = "TRAITE"
STATUT_ERREUR = "ERREUR"
STATUT_DOUBLON = "DOUBLON"
IMPORT_STATUTS = (STATUT_TRAITE, STATUT_ERREUR, STATUT_DOUBLON)

METHODE_TEXTE = "TEXTE"
METHODE_OCR = "OCR"
EXTRACTION_METHODES = (METHODE_TEXTE, METHODE_OCR)

_HASH_CHUNK_SIZE = 1024 * 1024

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_hash TEXT NOT NULL UNIQUE,
    enseigne TEXT NOT NULL,
    date TEXT,
    total_ticket REAL,
    total_remises REAL,
    nombre_articles_ticket INTEGER,
    articles_detectes INTEGER NOT NULL,
    unites_detectees INTEGER NOT NULL,
    fichier_source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    format_magasin TEXT
);

CREATE TABLE IF NOT EXISTS ticket_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    article TEXT NOT NULL,
    rayon TEXT,
    prix_unitaire REAL,
    quantite REAL NOT NULL,
    poids_kg REAL,
    prix_kg REAL,
    prix_total REAL NOT NULL,
    tva_code TEXT,
    categorie TEXT
);
CREATE INDEX IF NOT EXISTS idx_ticket_items_ticket_id ON ticket_items(ticket_id);

CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nom_fichier TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    statut TEXT NOT NULL CHECK (statut IN {IMPORT_STATUTS!r}),
    message TEXT,
    ticket_id INTEGER REFERENCES tickets(id) ON DELETE SET NULL,
    chemin_json TEXT,
    chemin_final TEXT,
    imported_at TEXT NOT NULL,
    methode_extraction TEXT CHECK (methode_extraction IN {EXTRACTION_METHODES!r}),
    avertissements TEXT
);
CREATE INDEX IF NOT EXISTS idx_imports_file_hash ON imports(file_hash);
"""

# Columns added after the first release; NULL means "unknown" for older imports.
_IMPORT_MIGRATION_COLUMNS = {
    "methode_extraction": (
        f"TEXT CHECK (methode_extraction IN {EXTRACTION_METHODES!r})"
    ),
    "avertissements": "TEXT",
}

_TICKET_MIGRATION_COLUMNS = {"format_magasin": "TEXT"}
# Optional keys of the ticket dict: absent for retailers that do not provide them.
_OPTIONAL_TICKET_COLUMNS = tuple(_TICKET_MIGRATION_COLUMNS)

_TICKET_COLUMNS = (
    "fichier_source",
    "enseigne",
    "date",
    "total_ticket",
    "total_remises",
    "nombre_articles_ticket",
    "articles_detectes",
    "unites_detectees",
)
_ITEM_COLUMNS = (
    "article",
    "rayon",
    "prix_unitaire",
    "quantite",
    "poids_kg",
    "prix_kg",
    "prix_total",
    "tva_code",
    "categorie",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _migrate(connection: sqlite3.Connection) -> None:
    """Add the columns missing from a database created by an earlier version."""
    for table, columns in (
        ("imports", _IMPORT_MIGRATION_COLUMNS),
        ("tickets", _TICKET_MIGRATION_COLUMNS),
    ):
        existing = {
            row["name"] for row in connection.execute(f"PRAGMA table_info({table})")
        }
        for column, definition in columns.items():
            if column not in existing:
                connection.execute(
                    f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
                )
    connection.commit()


def connect(db_path: Path) -> sqlite3.Connection:
    """Open the database, enable foreign keys and create the schema if needed."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(_SCHEMA)
    _migrate(connection)
    return connection


def hash_file(path: Path) -> str:
    """Return the SHA-256 of the file bytes."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(_HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_ticket(ticket: Mapping[str, Any]) -> str:
    """Return a SHA-256 of the ticket content, independent of its source file."""
    # Empty optional keys are left out so that hashes computed before they existed stay valid.
    content = {
        key: value
        for key, value in ticket.items()
        if key != "fichier_source"
        and not (key in _OPTIONAL_TICKET_COLUMNS and value is None)
    }
    canonical = json.dumps(
        content,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def find_processed_import(
    connection: sqlite3.Connection, file_hash: str
) -> Optional[sqlite3.Row]:
    """Return the successful import of a file with this hash, if any."""
    return connection.execute(
        "SELECT * FROM imports WHERE file_hash = ? AND statut = ? "
        "ORDER BY id LIMIT 1",
        (file_hash, STATUT_TRAITE),
    ).fetchone()


def find_ticket_by_hash(
    connection: sqlite3.Connection, content_hash: str
) -> Optional[sqlite3.Row]:
    return connection.execute(
        "SELECT * FROM tickets WHERE content_hash = ?", (content_hash,)
    ).fetchone()


# Totals read by the OCR on two scans of the same receipt may differ by a few cents.
_SIMILAR_TOTAL_TOLERANCE = 0.05
_DATE_LENGTH = len("YYYY-MM-DD")


def find_similar_ticket(
    connection: sqlite3.Connection, ticket: Mapping[str, Any]
) -> Optional[sqlite3.Row]:
    """Return a stored ticket that is probably the same receipt, scanned again.

    Same retailer, same printed article count, total within a few cents and the
    same day (or a day missing on either side). Receipts that do not print their
    article count are never matched: too risky.
    """
    count = ticket.get("nombre_articles_ticket")
    total = ticket.get("total_ticket")
    if count is None or total is None:
        return None
    day = ticket["date"][:_DATE_LENGTH] if ticket.get("date") else None
    return connection.execute(
        "SELECT * FROM tickets WHERE enseigne = ? AND nombre_articles_ticket = ? "
        "AND ABS(total_ticket - ?) <= ? "
        "AND (date IS NULL OR ? IS NULL OR substr(date, 1, ?) = ?) "
        "ORDER BY id LIMIT 1",
        (
            ticket["enseigne"],
            count,
            total,
            _SIMILAR_TOTAL_TOLERANCE + 1e-9,
            day,
            _DATE_LENGTH,
            day,
        ),
    ).fetchone()


def load_ticket(connection: sqlite3.Connection, ticket_id: int) -> Dict[str, Any]:
    """Return one stored ticket in the canonical nested-dict format."""
    row = connection.execute(
        f"SELECT {', '.join((*_TICKET_COLUMNS, *_OPTIONAL_TICKET_COLUMNS))} "
        "FROM tickets WHERE id = ?",
        (ticket_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"Ticket n°{ticket_id} introuvable")
    ticket = {column: row[column] for column in _TICKET_COLUMNS}
    for column in _OPTIONAL_TICKET_COLUMNS:
        if row[column] is not None:
            ticket[column] = row[column]
    ticket["articles"] = [
        {column: item[column] for column in _ITEM_COLUMNS}
        for item in connection.execute(
            f"SELECT {', '.join(_ITEM_COLUMNS)} FROM ticket_items "
            "WHERE ticket_id = ? ORDER BY position",
            (ticket_id,),
        )
    ]
    return ticket


def complete_ticket_date(
    connection: sqlite3.Connection,
    ticket_id: int,
    date: str,
    original: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Set the missing date of a ticket and refresh its content hash.

    ``original`` is the ticket as first parsed (its JSON trace): the hash is only
    reproducible from it, since SQLite gives back ``2.0`` where the parser had ``2``.
    It is ignored unless it still matches the stored hash. The transaction is not
    committed. Returns the updated ticket, to rewrite its JSON.
    """
    stored = connection.execute(
        "SELECT date, content_hash FROM tickets WHERE id = ?", (ticket_id,)
    ).fetchone()
    if stored is None:
        raise KeyError(f"Ticket n°{ticket_id} introuvable")
    if stored["date"] is not None:
        raise ValueError(f"Le ticket n°{ticket_id} a déjà une date")
    if original is not None and hash_ticket(original) == stored["content_hash"]:
        ticket = dict(original)
    else:
        ticket = load_ticket(connection, ticket_id)
    ticket["date"] = date
    connection.execute(
        "UPDATE tickets SET date = ?, content_hash = ? WHERE id = ?",
        (date, hash_ticket(ticket), ticket_id),
    )
    return ticket


def find_import_json(connection: sqlite3.Connection, ticket_id: int) -> Optional[Path]:
    row = connection.execute(
        "SELECT chemin_json FROM imports WHERE ticket_id = ? AND chemin_json IS NOT NULL "
        "ORDER BY id LIMIT 1",
        (ticket_id,),
    ).fetchone()
    return Path(row["chemin_json"]) if row else None


def insert_ticket(
    connection: sqlite3.Connection,
    ticket: Mapping[str, Any],
    content_hash: str,
) -> int:
    """Insert a ticket and its items without committing the transaction.

    Raises sqlite3.IntegrityError if the content hash already exists.
    """
    columns = ", ".join((*_TICKET_COLUMNS, *_OPTIONAL_TICKET_COLUMNS))
    placeholders = ", ".join("?" for _ in (*_TICKET_COLUMNS, *_OPTIONAL_TICKET_COLUMNS))
    cursor = connection.execute(
        f"INSERT INTO tickets (content_hash, created_at, {columns}) "
        f"VALUES (?, ?, {placeholders})",
        (
            content_hash,
            _now(),
            *(ticket[column] for column in _TICKET_COLUMNS),
            *(ticket.get(column) for column in _OPTIONAL_TICKET_COLUMNS),
        ),
    )
    ticket_id = cursor.lastrowid

    item_columns = ", ".join(_ITEM_COLUMNS)
    item_placeholders = ", ".join("?" for _ in _ITEM_COLUMNS)
    connection.executemany(
        f"INSERT INTO ticket_items (ticket_id, position, {item_columns}) "
        f"VALUES (?, ?, {item_placeholders})",
        [
            (
                ticket_id,
                position,
                *(item.get(column) for column in _ITEM_COLUMNS),
            )
            for position, item in enumerate(ticket["articles"])
        ],
    )
    return ticket_id


def record_import(
    connection: sqlite3.Connection,
    *,
    nom_fichier: str,
    file_hash: str,
    statut: str,
    message: Optional[str] = None,
    ticket_id: Optional[int] = None,
    chemin_json: Optional[Path] = None,
    chemin_final: Optional[Path] = None,
    methode_extraction: Optional[str] = None,
    avertissements: Optional[str] = None,
) -> int:
    """Trace an import attempt without committing the transaction."""
    if statut not in IMPORT_STATUTS:
        raise ValueError(f"Statut d'import invalide : {statut!r}")
    if methode_extraction is not None and methode_extraction not in EXTRACTION_METHODES:
        raise ValueError(f"Méthode d'extraction invalide : {methode_extraction!r}")
    cursor = connection.execute(
        "INSERT INTO imports (nom_fichier, file_hash, statut, message, ticket_id, "
        "chemin_json, chemin_final, imported_at, methode_extraction, avertissements) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            nom_fichier,
            file_hash,
            statut,
            message,
            ticket_id,
            str(chemin_json) if chemin_json else None,
            str(chemin_final) if chemin_final else None,
            _now(),
            methode_extraction,
            avertissements,
        ),
    )
    return cursor.lastrowid


def load_tickets(connection: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Return every stored ticket in the canonical nested-dict format."""
    items_by_ticket: Dict[int, List[Dict[str, Any]]] = {}
    item_rows = connection.execute(
        f"SELECT ticket_id, {', '.join(_ITEM_COLUMNS)} FROM ticket_items "
        "ORDER BY ticket_id, position"
    )
    for row in item_rows:
        items_by_ticket.setdefault(row["ticket_id"], []).append(
            {column: row[column] for column in _ITEM_COLUMNS}
        )

    tickets = []
    ticket_rows = connection.execute(
        f"SELECT id, {', '.join((*_TICKET_COLUMNS, *_OPTIONAL_TICKET_COLUMNS))} "
        "FROM tickets "
        "ORDER BY date IS NULL, date, id"
    )
    for row in ticket_rows:
        ticket = {column: row[column] for column in _TICKET_COLUMNS}
        for column in _OPTIONAL_TICKET_COLUMNS:
            if row[column] is not None:
                ticket[column] = row[column]
        ticket["articles"] = items_by_ticket.get(row["id"], [])
        tickets.append(ticket)
    return tickets

