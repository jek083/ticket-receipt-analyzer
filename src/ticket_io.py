"""Read and write canonical ticket JSON and flattened item CSV files."""

import csv
import json
from pathlib import Path
from typing import Any, Dict, List

from src.parse_ticket_enrichi import ParsedTicket

CSV_FIELDS = [
    "article",
    "date",
    "rayon",
    "prix_unitaire",
    "quantite",
    "poids_kg",
    "prix_kg",
    "prix_total",
    "enseigne",
    "tva_code",
    "categorie",
    "fichier_source",
    "total_ticket",
    "total_remises",
    "nombre_articles_ticket",
    "unites_detectees",
]


def write_tickets_json(tickets: List[ParsedTicket], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output:
        json.dump(tickets, output, ensure_ascii=False, indent=2, allow_nan=False)
        output.write("\n")


def write_ticket_json(ticket: ParsedTicket, path: Path) -> None:
    """Write a single ticket as a JSON object, for traceability."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output:
        json.dump(ticket, output, ensure_ascii=False, indent=2, allow_nan=False)
        output.write("\n")


def read_ticket_json(path: Path) -> Dict[str, Any]:
    """Read a single ticket written by ``write_ticket_json``."""
    with Path(path).open("r", encoding="utf-8") as source:
        ticket = json.load(source)
    if not isinstance(ticket, dict):
        raise ValueError("Le JSON du ticket doit contenir un objet")
    return ticket


def load_tickets_json(path: Path) -> List[Dict[str, Any]]:
    with Path(path).open("r", encoding="utf-8") as source:
        tickets = json.load(source)

    if not isinstance(tickets, list):
        raise ValueError("Le JSON des tickets doit contenir une liste")
    for index, ticket in enumerate(tickets):
        if not isinstance(ticket, dict) or not isinstance(ticket.get("articles"), list):
            raise ValueError(
                f"Enregistrement de ticket invalide à la position {index}"
            )

    return tickets


def _format_number(value: Any, precision: int) -> Any:
    if value is None:
        return None
    if float(value).is_integer():
        return int(value)
    return "{:.{}f}".format(value, precision)


def export_items_csv(tickets: List[Dict[str, Any]], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, delimiter=";")
        writer.writeheader()

        for ticket in tickets:
            for item in ticket["articles"]:
                writer.writerow(
                    {
                        "article": item.get("article"),
                        "date": ticket.get("date"),
                        "rayon": item.get("rayon"),
                        "prix_unitaire": _format_number(
                            item.get("prix_unitaire"), 2
                        ),
                        "quantite": _format_number(item.get("quantite"), 3),
                        "poids_kg": _format_number(item.get("poids_kg"), 3),
                        "prix_kg": _format_number(item.get("prix_kg"), 2),
                        "prix_total": _format_number(item.get("prix_total"), 2),
                        "enseigne": ticket.get("enseigne"),
                        "tva_code": item.get("tva_code"),
                        "categorie": item.get("categorie"),
                        "fichier_source": ticket.get("fichier_source"),
                        "total_ticket": _format_number(
                            ticket.get("total_ticket"), 2
                        ),
                        "total_remises": _format_number(
                            ticket.get("total_remises"), 2
                        ),
                        "nombre_articles_ticket": ticket.get(
                            "nombre_articles_ticket"
                        ),
                        "unites_detectees": ticket.get("unites_detectees"),
                    }
                )
