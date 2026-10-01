import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import config
from src import importer, ticket_db
from src.analyzer import TicketAnalyzer
from src.pdf_extractor import METHODE_TEXTE, ExtractionResult
from src.ticket_io import export_items_csv

LECLERC_TEXT = """E. Leclerc
28 juin 2024 10:12
--- Page 1 (OCR) ---
>> EPICERIE
LAIT TGU 1/2 ECREME 1L 1,01 1
PILSNER (6X33cl)
6 X 1,19 € 7,14 3
>> FRUITS ET LEGUMES
COURGETTE LONGUE VERTE
0,828 kg X 1,76 €/kg
1,46 1
Total 8 articles 9,61
"""

PICARD_TEXT = """1067 - PICARD SURGELES SAS
V1 *Gl Amarena It 2,99 €
V1 *Epinards bio 2,90 €
V1 *Naanwich poulet 3,99 €
--------------
TOTAL (3) 9,88 €
Date
09.05.26 17:17
Nb lignes ticket: 3
"""


class FakeExtractor:
    """Returns the text mapped to the PDF content, or raises the mapped error."""

    def __init__(self, results):
        self.results = results

    def extract(self, path):
        result = self.results[Path(path).read_bytes()]
        if isinstance(result, Exception):
            raise result
        if isinstance(result, ExtractionResult):
            return result
        return ExtractionResult(result, METHODE_TEXTE)


class DatabaseTestCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.pending = root / "A_TRAITER"
        self.done = root / "TRAITE"
        self.error = root / "ERREUR"
        self.json_dir = root / "processed"
        self.pending.mkdir()
        self.connection = ticket_db.connect(root / "tickets.db")
        self.addCleanup(self.connection.close)

    def add_pdf(self, name, content):
        path = self.pending / name
        path.write_bytes(content)
        return path

    def run_import(self, results):
        return importer.import_pending(
            self.connection,
            FakeExtractor(results),
            pending_dir=self.pending,
            done_dir=self.done,
            error_dir=self.error,
            json_dir=self.json_dir,
            categories=config.FOOD_CATEGORIES,
        )

    def imports(self):
        return self.connection.execute(
            "SELECT nom_fichier, statut, message, ticket_id FROM imports ORDER BY id"
        ).fetchall()

    def names(self, directory):
        return sorted(path.name for path in directory.iterdir())


class SchemaTests(DatabaseTestCase):
    def test_creates_the_three_tables_and_enables_foreign_keys(self):
        tables = {
            row["name"]
            for row in self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

        self.assertTrue({"tickets", "ticket_items", "imports"} <= tables)
        self.assertEqual(
            self.connection.execute("PRAGMA foreign_keys").fetchone()[0], 1
        )

    def test_connect_is_idempotent(self):
        path = Path(self.connection.execute("PRAGMA database_list").fetchone()["file"])

        with closing(ticket_db.connect(path)) as second:
            count = second.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]

        self.assertEqual(count, 0)

    def test_rejects_an_unknown_import_status(self):
        with self.assertRaises(ValueError):
            ticket_db.record_import(
                self.connection, nom_fichier="a.pdf", file_hash="x", statut="???"
            )


class HashTests(unittest.TestCase):
    def test_ticket_hash_ignores_source_file_and_key_order(self):
        first = {"fichier_source": "a.pdf", "enseigne": "Picard", "articles": []}
        second = {"articles": [], "enseigne": "Picard", "fichier_source": "b.pdf"}

        self.assertEqual(ticket_db.hash_ticket(first), ticket_db.hash_ticket(second))

    def test_ticket_hash_changes_with_content(self):
        first = {"enseigne": "Picard", "total_ticket": 1.0}
        second = {"enseigne": "Picard", "total_ticket": 2.0}

        self.assertNotEqual(ticket_db.hash_ticket(first), ticket_db.hash_ticket(second))

    def test_file_hash_depends_on_bytes_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "a.pdf"
            second = Path(temp_dir) / "b.pdf"
            first.write_bytes(b"same")
            second.write_bytes(b"same")

            self.assertEqual(ticket_db.hash_file(first), ticket_db.hash_file(second))


class ImportTests(DatabaseTestCase):
    def test_imports_a_ticket_and_moves_the_pdf_to_traite(self):
        self.add_pdf("leclerc.pdf", b"leclerc")

        summary = self.run_import({b"leclerc": LECLERC_TEXT})

        self.assertEqual(summary.imported, ["leclerc.pdf"])
        self.assertEqual(self.names(self.done), ["leclerc.pdf"])
        self.assertEqual(list(self.pending.iterdir()), [])
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM tickets").fetchone()[0], 1
        )
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM ticket_items").fetchone()[0],
            3,
        )
        (row,) = self.imports()
        self.assertEqual((row["statut"], row["nom_fichier"]), ("TRAITE", "leclerc.pdf"))
        self.assertIsNotNone(row["ticket_id"])

    def test_writes_one_json_per_ticket(self):
        self.add_pdf("leclerc.pdf", b"leclerc")

        self.run_import({b"leclerc": LECLERC_TEXT})

        data = json.loads((self.json_dir / "leclerc.json").read_text(encoding="utf-8"))
        self.assertEqual(data["fichier_source"], "leclerc.pdf")
        self.assertEqual(len(data["articles"]), 3)

    def test_same_file_is_a_duplicate_and_goes_to_erreur(self):
        self.add_pdf("first.pdf", b"leclerc")
        self.run_import({b"leclerc": LECLERC_TEXT})
        self.add_pdf("copy.pdf", b"leclerc")

        summary = self.run_import({})

        self.assertIn("copy.pdf", summary.duplicates)
        self.assertEqual(summary.failures, {})
        self.assertEqual(self.names(self.error), ["copy.pdf"])
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM tickets").fetchone()[0], 1
        )
        self.assertEqual(self.imports()[-1]["statut"], "DOUBLON")

    def test_same_ticket_content_in_another_file_is_a_duplicate(self):
        self.add_pdf("first.pdf", b"one")
        self.add_pdf("second.pdf", b"two")

        summary = self.run_import({b"one": LECLERC_TEXT, b"two": LECLERC_TEXT})

        self.assertEqual(summary.imported, ["first.pdf"])
        self.assertIn("second.pdf", summary.duplicates)
        self.assertEqual(self.names(self.done), ["first.pdf"])
        self.assertEqual(self.names(self.error), ["second.pdf"])
        self.assertEqual(
            [row["statut"] for row in self.imports()], ["TRAITE", "DOUBLON"]
        )

    def test_extraction_error_goes_to_erreur_and_batch_continues(self):
        self.add_pdf("bad.pdf", b"bad")
        self.add_pdf("good.pdf", b"good")

        summary = self.run_import(
            {b"bad": ValueError("PDF illisible"), b"good": PICARD_TEXT}
        )

        self.assertEqual(summary.failures, {"bad.pdf": "PDF illisible"})
        self.assertEqual(summary.imported, ["good.pdf"])
        self.assertEqual(self.names(self.error), ["bad.pdf"])
        self.assertEqual(self.names(self.done), ["good.pdf"])
        statuts = {row["nom_fichier"]: row["statut"] for row in self.imports()}
        self.assertEqual(statuts, {"bad.pdf": "ERREUR", "good.pdf": "TRAITE"})

    def test_unparseable_text_goes_to_erreur(self):
        self.add_pdf("unknown.pdf", b"unknown")

        summary = self.run_import({b"unknown": "Boulangerie inconnue"})

        self.assertIn("unknown.pdf", summary.failures)
        self.assertEqual(self.names(self.error), ["unknown.pdf"])
        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM tickets").fetchone()[0], 0
        )

    def test_failed_file_can_be_retried_after_a_fix(self):
        self.add_pdf("retry.pdf", b"retry")
        self.run_import({b"retry": ValueError("boom")})
        (self.error / "retry.pdf").rename(self.pending / "retry.pdf")

        summary = self.run_import({b"retry": PICARD_TEXT})

        self.assertEqual(summary.imported, ["retry.pdf"])

    def test_name_collisions_never_overwrite(self):
        self.done.mkdir()
        (self.done / "same.pdf").write_bytes(b"old")
        self.add_pdf("same.pdf", b"leclerc")

        self.run_import({b"leclerc": LECLERC_TEXT})

        self.assertEqual(self.names(self.done), ["same.pdf", "same_1.pdf"])
        self.assertEqual((self.done / "same.pdf").read_bytes(), b"old")

    def test_ignores_non_pdf_files(self):
        (self.pending / "notes.txt").write_text("x")

        summary = self.run_import({})

        self.assertEqual(summary.total, 0)
        self.assertEqual(self.names(self.pending), ["notes.txt"])

    def test_move_failure_rolls_back_database_and_json(self):
        self.add_pdf("leclerc.pdf", b"leclerc")

        with patch.object(importer.shutil, "move", side_effect=OSError("disque plein")):
            summary = self.run_import({b"leclerc": LECLERC_TEXT})

        self.assertEqual(summary.failures, {"leclerc.pdf": "disque plein"})
        self.assertEqual(self.names(self.pending), ["leclerc.pdf"])
        for table in ("tickets", "ticket_items", "imports"):
            count = self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            self.assertEqual(count, 0, table)
        self.assertEqual(list(self.json_dir.glob("*.json")), [])

    def test_missing_tesseract_aborts_and_leaves_the_pdf_in_place(self):
        self.add_pdf("scan.pdf", b"scan")
        error = importer.pytesseract.TesseractNotFoundError()

        with self.assertRaises(importer.pytesseract.TesseractNotFoundError):
            self.run_import({b"scan": error})

        self.assertEqual(self.names(self.pending), ["scan.pdf"])
        self.assertEqual(self.imports(), [])


class LoadTicketsTests(DatabaseTestCase):
    def test_reloaded_tickets_feed_analyzer_and_csv(self):
        self.add_pdf("leclerc.pdf", b"leclerc")
        self.add_pdf("picard.pdf", b"picard")
        self.run_import({b"leclerc": LECLERC_TEXT, b"picard": PICARD_TEXT})

        tickets = ticket_db.load_tickets(self.connection)

        self.assertEqual(len(tickets), 2)
        leclerc = next(t for t in tickets if t["fichier_source"] == "leclerc.pdf")
        self.assertEqual(leclerc["enseigne"], "E.Leclerc")
        self.assertEqual(
            [item["article"] for item in leclerc["articles"]][0],
            "LAIT TGU 1/2 ECREME 1L",
        )
        self.assertEqual(TicketAnalyzer(tickets).df["filename"].nunique(), 2)
        csv_path = Path(self.json_dir) / "items.csv"
        export_items_csv(tickets, csv_path)
        self.assertEqual(len(csv_path.read_text(encoding="utf-8-sig").splitlines()), 7)

    def test_deleting_a_ticket_cascades_to_items_and_keeps_the_import(self):
        self.add_pdf("leclerc.pdf", b"leclerc")
        self.run_import({b"leclerc": LECLERC_TEXT})

        with self.connection:
            self.connection.execute("DELETE FROM tickets")

        self.assertEqual(
            self.connection.execute("SELECT COUNT(*) FROM ticket_items").fetchone()[0],
            0,
        )
        (row,) = self.imports()
        self.assertIsNone(row["ticket_id"])

    def test_duplicate_content_hash_is_rejected_by_the_database(self):
        ticket = {
            "fichier_source": "a.pdf",
            "enseigne": "Picard",
            "date": None,
            "total_ticket": None,
            "total_remises": None,
            "nombre_articles_ticket": None,
            "articles_detectes": 0,
            "unites_detectees": 0,
            "articles": [],
        }
        with self.connection:
            ticket_db.insert_ticket(self.connection, ticket, "hash")

        with self.assertRaises(sqlite3.IntegrityError):
            ticket_db.insert_ticket(self.connection, ticket, "hash")


if __name__ == "__main__":
    unittest.main()
