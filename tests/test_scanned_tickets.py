import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw, ImageFont

import config
from src import importer, ticket_db
from src.parse_ticket_enrichi import parse_ticket as parse_leclerc
from src.parser_dispatcher import parse_ticket
from src.pdf_extractor import (
    METHODE_OCR,
    METHODE_TEXTE,
    SUPPORTED_EXTENSIONS,
    ExtractionResult,
    PDFExtractor,
)
from tests.test_ticket_database import (
    LECLERC_TEXT,
    PICARD_TEXT,
    DatabaseTestCase,
)

OCR_PATCH = "src.pdf_extractor.pytesseract.image_to_string"


def save_image(path: Path, size=(400, 800), **save_options) -> Path:
    Image.new("RGB", size, "white").save(path, **save_options)
    return path


class ImageExtractionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)

    def test_supported_extensions_cover_pdf_and_images(self):
        self.assertEqual(
            SUPPORTED_EXTENSIONS, {".pdf", ".png", ".jpg", ".jpeg"}
        )

    def test_reads_png_jpg_and_jpeg_with_ocr_whatever_the_case(self):
        for name in ("a.png", "b.jpg", "c.jpeg", "D.PNG", "E.JPG"):
            with self.subTest(name=name):
                path = save_image(self.folder / name)

                with patch(OCR_PATCH, return_value="  Ticket OCR \n") as ocr:
                    result = PDFExtractor(
                        ocr_language="fra", image_min_width=0
                    ).extract(path)

                self.assertEqual(result.text, "Ticket OCR")
                self.assertEqual(result.methode, METHODE_OCR)
                self.assertEqual(ocr.call_args.kwargs["lang"], "fra")

    def test_extract_from_file_returns_the_text_of_an_image(self):
        path = save_image(self.folder / "scan.png")

        with patch(OCR_PATCH, return_value="Ticket OCR"):
            self.assertEqual(PDFExtractor().extract_from_file(path), "Ticket OCR")

    def test_rejects_unsupported_formats(self):
        path = self.folder / "notes.txt"
        path.write_text("x")

        with self.assertRaisesRegex(ValueError, "non supporte"):
            PDFExtractor().extract(path)

    def test_rejects_a_missing_file(self):
        with self.assertRaises(FileNotFoundError):
            PDFExtractor().extract(self.folder / "absent.png")

    def test_image_requires_ocr_to_be_enabled(self):
        path = save_image(self.folder / "scan.png")

        with patch(OCR_PATCH) as ocr:
            with self.assertRaisesRegex(ValueError, "OCR desactive"):
                PDFExtractor(ocr_enabled=False).extract(path)

        ocr.assert_not_called()

    def test_corrupted_image_raises_an_os_error(self):
        path = self.folder / "broken.png"
        path.write_bytes(b"not an image")

        with self.assertRaises(OSError):
            PDFExtractor().extract(path)

    def test_image_without_text_is_an_error(self):
        path = save_image(self.folder / "blank.png")

        with patch(OCR_PATCH, return_value="  \n"):
            with self.assertRaisesRegex(ValueError, "Aucun texte"):
                PDFExtractor().extract(path)

    def test_small_images_are_upscaled_to_the_minimum_width(self):
        path = save_image(self.folder / "small.png", size=(300, 900))

        with patch(OCR_PATCH, return_value="Ticket") as ocr:
            PDFExtractor(image_min_width=600).extract(path)

        image = ocr.call_args.args[0]
        self.assertEqual(image.size, (600, 1800))
        self.assertEqual(image.mode, "L")

    def test_large_images_are_not_resized(self):
        path = save_image(self.folder / "large.png", size=(1200, 900))

        with patch(OCR_PATCH, return_value="Ticket") as ocr:
            PDFExtractor(image_min_width=600).extract(path)

        self.assertEqual(ocr.call_args.args[0].size, (1200, 900))

    def test_oversized_images_are_downscaled_to_the_maximum_width(self):
        path = save_image(self.folder / "huge.png", size=(2400, 1200))

        with patch(OCR_PATCH, return_value="Ticket") as ocr:
            PDFExtractor(image_min_width=600, image_max_width=1200).extract(path)

        self.assertEqual(ocr.call_args.args[0].size, (1200, 600))

    def test_maximum_width_is_disabled_by_default(self):
        path = save_image(self.folder / "huge.png", size=(2400, 1200))

        with patch(OCR_PATCH, return_value="Ticket") as ocr:
            PDFExtractor().extract(path)

        self.assertEqual(ocr.call_args.args[0].size, (2400, 1200))

    def test_exif_orientation_is_applied_before_ocr(self):
        exif = Image.Exif()
        exif[0x0112] = 6  # rotated 90° when displayed
        path = save_image(self.folder / "photo.jpg", size=(200, 100), exif=exif)

        with patch(OCR_PATCH, return_value="Ticket") as ocr:
            PDFExtractor(image_min_width=0).extract(path)

        self.assertEqual(ocr.call_args.args[0].size, (100, 200))


class ExtractionMethodTests(unittest.TestCase):
    def _pdf_context(self, page_texts):
        pages = []
        for text in page_texts:
            page = Mock()
            page.extract_text.return_value = text
            page.to_image.return_value.original = object()
            pages.append(page)
        pdf = Mock(pages=pages)
        context = Mock()
        context.__enter__ = Mock(return_value=pdf)
        context.__exit__ = Mock(return_value=False)
        return context

    def _extract(self, page_texts):
        pypdf_reader = Mock(pages=[Mock(extract_text=Mock(return_value=""))
                                   for _ in page_texts])
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "ticket.pdf"
            pdf_path.touch()
            with patch(
                "src.pdf_extractor.pdfplumber.open",
                return_value=self._pdf_context(page_texts),
            ), patch("src.pdf_extractor.PdfReader", return_value=pypdf_reader), patch(
                OCR_PATCH, return_value="Texte OCR"
            ):
                return PDFExtractor().extract(pdf_path)

    def test_native_text_pdf_is_reported_as_texte(self):
        result = self._extract(["Texte natif"])

        self.assertEqual((result.text, result.methode), ("Texte natif", METHODE_TEXTE))

    def test_image_only_pdf_is_reported_as_ocr(self):
        result = self._extract([None])

        self.assertEqual((result.text, result.methode), ("Texte OCR", METHODE_OCR))

    def test_pdf_with_one_scanned_page_is_reported_as_ocr(self):
        result = self._extract(["Texte natif", None])

        self.assertEqual(result.methode, METHODE_OCR)
        self.assertEqual(result.text, "Texte natif\nTexte OCR")

    def test_method_names_match_the_database_constants(self):
        self.assertEqual(METHODE_TEXTE, ticket_db.METHODE_TEXTE)
        self.assertEqual(METHODE_OCR, ticket_db.METHODE_OCR)


class OcrNoiseParserTests(unittest.TestCase):
    def test_picard_item_prefix_misread_by_ocr_is_accepted(self):
        text = PICARD_TEXT.replace("V1 *Epinards", "Vil *Epinards").replace(
            "V1 *Naanwich", "VI *Naanwich"
        )

        ticket = parse_ticket(text, categories=config.FOOD_CATEGORIES)

        self.assertEqual(
            [item["article"] for item in ticket["articles"]],
            ["Gl Amarena It", "Epinards bio", "Naanwich poulet"],
        )

    def test_leclerc_amount_and_vat_code_noise_are_repaired(self):
        text = LECLERC_TEXT.replace(
            "LAIT TGU 1/2 ECREME 1L 1,01 1", "LAIT TGU 1/2 ECREME 1L 1:01 1!"
        )

        ticket = parse_leclerc(text, categories=config.FOOD_CATEGORIES)

        first = ticket["articles"][0]
        self.assertEqual((first["prix_total"], first["tva_code"]), (1.01, "1"))

    def test_leclerc_header_times_are_not_rewritten(self):
        ticket = parse_leclerc(LECLERC_TEXT, categories=config.FOOD_CATEGORIES)

        self.assertEqual(ticket["date"], "2024-06-28 10:12")


class ScannedImportTests(DatabaseTestCase):
    def test_imports_an_image_marked_as_ocr(self):
        self.add_pdf("scan.PNG", b"scan")
        summary = self.run_import(
            {b"scan": ExtractionResult(PICARD_TEXT, METHODE_OCR)}
        )

        self.assertEqual(summary.imported, ["scan.PNG"])
        self.assertEqual(summary.ocr_imported, ["scan.PNG"])
        self.assertEqual(self.names(self.done), ["scan.PNG"])
        row = self.connection.execute(
            "SELECT methode_extraction, avertissements FROM imports"
        ).fetchone()
        self.assertEqual(row["methode_extraction"], "OCR")

    def test_inconsistent_ocr_ticket_is_imported_with_warnings(self):
        self.add_pdf("scan.jpg", b"scan")
        text = PICARD_TEXT.replace("TOTAL (3) 9,88 €", "TOTAL (4) 19,88 €")

        summary = self.run_import({b"scan": ExtractionResult(text, METHODE_OCR)})

        self.assertEqual(summary.imported, ["scan.jpg"])
        self.assertIn("scan.jpg", summary.warnings)
        row = self.connection.execute(
            "SELECT avertissements FROM imports"
        ).fetchone()
        self.assertIn("Somme des articles différente du total", row["avertissements"])
        self.assertIn("Nombre d'articles différent", row["avertissements"])

    def test_missing_total_is_reported_as_a_warning(self):
        self.add_pdf("scan.png", b"scan")
        text = "\n".join(
            line for line in PICARD_TEXT.splitlines() if not line.startswith("TOTAL")
        )

        summary = self.run_import({b"scan": ExtractionResult(text, METHODE_OCR)})

        self.assertEqual(summary.imported, ["scan.png"])
        self.assertIn("Total du ticket non détecté", summary.warnings["scan.png"])

    def test_native_pdf_is_marked_texte_without_warning(self):
        self.add_pdf("native.pdf", b"native")

        summary = self.run_import({b"native": PICARD_TEXT})

        row = self.connection.execute(
            "SELECT methode_extraction, avertissements FROM imports"
        ).fetchone()
        self.assertEqual(row["methode_extraction"], "TEXTE")
        self.assertIsNone(row["avertissements"])
        self.assertEqual((summary.ocr_imported, summary.warnings), ([], {}))

    def test_corrupted_image_goes_to_erreur(self):
        self.add_pdf("broken.png", b"broken")

        summary = self.run_import({b"broken": OSError("cannot identify image file")})

        self.assertIn("broken.png", summary.failures)
        self.assertEqual(self.names(self.error), ["broken.png"])

    def test_parse_failure_after_ocr_keeps_the_method_in_the_trace(self):
        self.add_pdf("scan.png", b"scan")

        self.run_import({b"scan": ExtractionResult("texte illisible", METHODE_OCR)})

        row = self.connection.execute(
            "SELECT statut, methode_extraction FROM imports"
        ).fetchone()
        self.assertEqual((row["statut"], row["methode_extraction"]), ("ERREUR", "OCR"))

    def test_ignores_unsupported_extensions(self):
        self.add_pdf("scan.gif", b"gif")
        self.add_pdf("scan.tiff", b"tiff")

        summary = self.run_import({})

        self.assertEqual(summary.total, 0)
        self.assertEqual(self.names(self.pending), ["scan.gif", "scan.tiff"])

    def test_rejects_an_unknown_extraction_method(self):
        with self.assertRaises(ValueError):
            ticket_db.record_import(
                self.connection,
                nom_fichier="a.png",
                file_hash="x",
                statut="TRAITE",
                methode_extraction="MAGIE",
            )


class MigrationTests(unittest.TestCase):
    OLD_IMPORTS_SCHEMA = """
        CREATE TABLE imports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nom_fichier TEXT NOT NULL,
            file_hash TEXT NOT NULL,
            statut TEXT NOT NULL,
            message TEXT,
            ticket_id INTEGER,
            chemin_json TEXT,
            chemin_final TEXT,
            imported_at TEXT NOT NULL
        );
    """

    def test_old_database_is_migrated_and_keeps_its_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "tickets.db"
            old = sqlite3.connect(db_path)
            old.executescript(self.OLD_IMPORTS_SCHEMA)
            old.execute(
                "INSERT INTO imports (nom_fichier, file_hash, statut, imported_at) "
                "VALUES ('old.pdf', 'h', 'TRAITE', '2026-01-01')"
            )
            old.commit()
            old.close()

            first = ticket_db.connect(db_path)
            first.close()
            second = ticket_db.connect(db_path)
            columns = {r["name"] for r in second.execute("PRAGMA table_info(imports)")}
            row = second.execute("SELECT * FROM imports").fetchone()
            ticket_db.record_import(
                second,
                nom_fichier="new.png",
                file_hash="h2",
                statut="TRAITE",
                methode_extraction="OCR",
                avertissements="attention",
            )
            second.commit()
            count = second.execute("SELECT COUNT(*) FROM imports").fetchone()[0]
            second.close()

        self.assertTrue({"methode_extraction", "avertissements"} <= columns)
        self.assertEqual(row["nom_fichier"], "old.pdf")
        self.assertIsNone(row["methode_extraction"])
        self.assertEqual(count, 2)


@unittest.skipUnless(
    shutil.which("tesseract"), "Tesseract n'est pas installé"
)
class RealOcrTests(unittest.TestCase):
    def test_reads_words_from_a_rendered_png(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "ticket.png"
            image = Image.new("RGB", (900, 200), "white")
            ImageDraw.Draw(image).text(
                (20, 60), "PICARD SURGELES TOTAL", fill="black",
                font=ImageFont.load_default(size=40),
            )
            image.save(path)

            result = PDFExtractor(ocr_language="eng").extract(path)

        self.assertEqual(result.methode, METHODE_OCR)
        self.assertIn("PICARD", result.text.upper())
        self.assertIn("TOTAL", result.text.upper())


if __name__ == "__main__":
    unittest.main()
