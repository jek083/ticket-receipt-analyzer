import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

import config
from src import importer, ticket_db
from src.pdf_extractor import METHODE_OCR, ExtractionResult, PDFExtractor
from src.parser_dispatcher import parse_ticket
from tests.test_carrefour_auchan_parsers import CARREFOUR_TEXT
from tests.test_lidl_marcel_parsers import LIDL_TEXT
from tests.test_ticket_database import DatabaseTestCase, FakeExtractor

OCR_PATCH = "src.pdf_extractor.pytesseract.image_to_string"

DATED_CARREFOUR_TEXT = CARREFOUR_TEXT.replace(
    "Carrefour 18\n", "Carrefour 18\n08/11/2025 10:15\n", 1
).replace("4 CREV 25/35", "4 CREV 25/3S", 1)


def make_a4_scan(receipt_left=450, receipt_right=750, mode="RGB"):
    """A white A4-like page with a narrow column of dark text lines."""
    image = Image.new(mode, (1200, 1600), "white")
    draw = ImageDraw.Draw(image)
    for top in range(100, 1500, 30):
        for left in range(receipt_left, receipt_right, 12):
            draw.rectangle((left, top, left + 8, top + 14), fill="black")
    return image


class RescanTests(DatabaseTestCase):
    def import_scans(self, *texts):
        for position, _ in enumerate(texts):
            self.add_pdf(f"scan{position}.png", f"scan{position}".encode())
        results = {f"scan{p}".encode(): text for p, text in enumerate(texts)}
        return self.run_import(results)

    def tickets(self):
        return self.connection.execute("SELECT * FROM tickets ORDER BY id").fetchall()

    def test_rescan_of_an_undated_ticket_is_a_duplicate_that_completes_its_date(self):
        summary = self.import_scans(CARREFOUR_TEXT, DATED_CARREFOUR_TEXT)

        self.assertEqual(summary.imported, ["scan0.png"])
        self.assertEqual(list(summary.duplicates), ["scan1.png"])
        self.assertEqual(list(summary.completed), ["scan1.png"])
        self.assertEqual(self.names(self.error), ["scan1.png"])
        (ticket,) = self.tickets()
        self.assertEqual(ticket["date"], "2025-11-08 10:15")

    def test_completed_ticket_hash_is_the_one_of_the_same_receipt_parsed_with_its_date(self):
        self.import_scans(CARREFOUR_TEXT, DATED_CARREFOUR_TEXT)
        (ticket,) = self.tickets()
        dated = parse_ticket(
            CARREFOUR_TEXT.replace("Carrefour 18\n", "Carrefour 18\n08/11/2025 10:15\n", 1),
            categories=config.FOOD_CATEGORIES,
        )

        self.assertEqual(ticket["content_hash"], ticket_db.hash_ticket(dated))

    def test_completed_ticket_json_is_updated_without_changing_number_types(self):
        self.import_scans(CARREFOUR_TEXT, DATED_CARREFOUR_TEXT)
        (ticket,) = self.tickets()

        json_path = ticket_db.find_import_json(self.connection, ticket["id"])
        content = json.loads(json_path.read_text(encoding="utf-8"))

        self.assertEqual(content["date"], "2025-11-08 10:15")
        self.assertIsInstance(content["articles"][0]["quantite"], int)
        self.assertEqual(ticket["content_hash"], ticket_db.hash_ticket(content))

    def test_rescan_trace_mentions_the_completed_date(self):
        self.import_scans(CARREFOUR_TEXT, DATED_CARREFOUR_TEXT)

        row = self.imports()[1]

        self.assertEqual(row["statut"], ticket_db.STATUT_DOUBLON)
        self.assertIn("2025-11-08", row["message"])

    def test_dated_ticket_is_never_overwritten_by_a_rescan(self):
        summary = self.import_scans(DATED_CARREFOUR_TEXT, CARREFOUR_TEXT)

        self.assertEqual(list(summary.duplicates), ["scan1.png"])
        self.assertEqual(summary.completed, {})
        self.assertEqual(self.tickets()[0]["date"], "2025-11-08 10:15")

    def test_same_receipt_on_another_day_is_a_new_ticket(self):
        other_day = DATED_CARREFOUR_TEXT.replace("08/11/2025", "09/11/2025")

        summary = self.import_scans(DATED_CARREFOUR_TEXT, other_day)

        self.assertEqual(len(summary.imported), 2)
        self.assertEqual(summary.duplicates, {})

    def test_another_total_is_a_new_ticket(self):
        other_total = CARREFOUR_TEXT.replace("TOTAL À PAYER 54,44", "TOTAL À PAYER 54,99")

        summary = self.import_scans(CARREFOUR_TEXT, other_total)

        self.assertEqual(len(summary.imported), 2)

    def test_total_read_a_few_cents_apart_is_a_rescan(self):
        rescan = DATED_CARREFOUR_TEXT.replace("TOTAL À PAYER 54,44", "TOTAL À PAYER 54,42")

        summary = self.import_scans(CARREFOUR_TEXT, rescan)

        self.assertEqual(list(summary.duplicates), ["scan1.png"])

    def test_ticket_without_printed_article_count_is_never_matched_by_similarity(self):
        ticket = {"enseigne": "X", "date": None, "total_ticket": 10.0, "nombre_articles_ticket": None}

        self.assertIsNone(ticket_db.find_similar_ticket(self.connection, ticket))

    def test_complete_ticket_date_refuses_an_already_dated_ticket(self):
        self.import_scans(DATED_CARREFOUR_TEXT)
        (ticket,) = self.tickets()

        with self.assertRaises(ValueError):
            ticket_db.complete_ticket_date(self.connection, ticket["id"], "2025-01-01")


class ConsistencyTests(unittest.TestCase):
    def ticket(self, printed, lines, units):
        return {
            "nombre_articles_ticket": printed,
            "articles_detectes": lines,
            "unites_detectees": units,
            "date": "2025-01-01",
            "total_ticket": 1.0,
            "total_remises": None,
            "articles": [{"prix_total": 1.0}],
        }

    def test_printed_count_may_be_the_number_of_lines(self):
        self.assertEqual(importer._consistency_warnings(self.ticket(6, 6, 13)), [])

    def test_printed_count_may_be_the_number_of_units(self):
        self.assertEqual(importer._consistency_warnings(self.ticket(13, 6, 13)), [])

    def test_other_count_is_reported(self):
        (warning,) = importer._consistency_warnings(self.ticket(9, 6, 13))

        self.assertIn("Nombre d'articles", warning)


class RetryExtractor:
    """First reading from the mapping, then the given alternatives."""

    def __init__(self, first, alternatives):
        self.first = first
        self.alternatives = alternatives
        self.tried = 0

    def extract(self, path):
        return ExtractionResult(self.first, METHODE_OCR)

    def extract_alternatives(self, path):
        for text in self.alternatives:
            self.tried += 1
            yield ExtractionResult(text, METHODE_OCR)


class ReadingRetryTests(DatabaseTestCase):
    BAD_TEXT = LIDL_TEXT.replace("A payer 17,01", "A payer 18,01")

    def run_with(self, extractor):
        self.add_pdf("lidl.png", b"lidl")
        return importer.import_pending(
            self.connection,
            extractor,
            pending_dir=self.pending,
            done_dir=self.done,
            error_dir=self.error,
            json_dir=self.json_dir,
            categories=config.FOOD_CATEGORIES,
        )

    def test_inconsistent_reading_is_replaced_by_a_consistent_one(self):
        extractor = RetryExtractor(self.BAD_TEXT, [self.BAD_TEXT, LIDL_TEXT])

        summary = self.run_with(extractor)

        self.assertEqual(summary.warnings, {})
        self.assertEqual(self.tickets_totals(), [17.01])

    def test_alternatives_are_not_read_when_the_first_reading_is_consistent(self):
        extractor = RetryExtractor(LIDL_TEXT, [LIDL_TEXT])

        self.run_with(extractor)

        self.assertEqual(extractor.tried, 0)

    def test_first_reading_wins_when_no_alternative_does_better(self):
        extractor = RetryExtractor(self.BAD_TEXT, [self.BAD_TEXT.replace("Kiwi", "Kiwis")])

        summary = self.run_with(extractor)

        self.assertIn("lidl.png", summary.warnings)
        self.assertEqual(self.tickets_totals(), [18.01])

    def test_unparsable_first_reading_can_be_rescued_by_another(self):
        extractor = RetryExtractor("bruit illisible", [LIDL_TEXT])

        summary = self.run_with(extractor)

        self.assertEqual(summary.imported, ["lidl.png"])

    def test_every_reading_unparsable_is_an_error_keeping_the_method(self):
        extractor = RetryExtractor("bruit illisible", ["autre bruit"])

        summary = self.run_with(extractor)

        self.assertEqual(list(summary.failures), ["lidl.png"])
        self.assertEqual(self.imports()[0]["statut"], ticket_db.STATUT_ERREUR)
        row = self.connection.execute("SELECT methode_extraction FROM imports").fetchone()
        self.assertEqual(row["methode_extraction"], METHODE_OCR)

    def tickets_totals(self):
        return [
            row["total_ticket"]
            for row in self.connection.execute("SELECT total_ticket FROM tickets")
        ]


class ImageCropTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)

    def prepared(self, image, **options):
        return PDFExtractor(**options)._prepare_image(image)

    def test_a4_scan_is_cropped_around_the_receipt(self):
        result = self.prepared(make_a4_scan(), crop_to_ticket=True)

        self.assertLess(result.width, 450)
        self.assertGreater(result.width, 300)
        self.assertLess(result.height, 1600)

    def test_no_crop_by_default(self):
        result = self.prepared(make_a4_scan())

        self.assertEqual(result.size, (1200, 1600))

    def test_photo_filling_the_frame_is_not_cropped(self):
        result = self.prepared(make_a4_scan(40, 1160), crop_to_ticket=True)

        self.assertEqual(result.size, (1200, 1600))

    def test_transparent_background_is_flattened_to_white_before_cropping(self):
        scan = make_a4_scan(mode="RGBA")
        scan.putalpha(Image.new("L", scan.size, 255))
        transparent = Image.new("RGBA", scan.size, (0, 0, 0, 0))
        transparent.alpha_composite(scan.crop((450, 100, 760, 1500)), (450, 100))

        result = self.prepared(transparent, crop_to_ticket=True)

        self.assertLess(result.width, 450)
        self.assertGreater(result.getpixel((1, 1)), 200)

    def test_retry_scales_resize_the_image_read_again(self):
        path = self.folder / "scan.png"
        Image.new("RGB", (1000, 1000), "white").save(path)
        widths = []

        def read(image, **kwargs):
            widths.append(image.width)
            return "texte"

        extractor = PDFExtractor(image_min_width=0, ocr_retry_scales=(0.5, 1.5))
        with patch(OCR_PATCH, side_effect=read):
            readings = list(extractor.extract_alternatives(path))

        self.assertEqual(widths, [500, 1500])
        self.assertEqual([r.text for r in readings], ["texte", "texte"])

    def test_pdf_has_no_alternative_reading(self):
        extractor = PDFExtractor(ocr_retry_scales=(0.5,))

        self.assertEqual(list(extractor.extract_alternatives(self.folder / "a.pdf")), [])

    def test_empty_alternative_reading_is_skipped(self):
        path = self.folder / "scan.png"
        Image.new("RGB", (400, 400), "white").save(path)
        extractor = PDFExtractor(image_min_width=0, ocr_retry_scales=(0.5, 0.8))

        with patch(OCR_PATCH, side_effect=["  ", "texte"]):
            readings = list(extractor.extract_alternatives(path))

        self.assertEqual([r.text for r in readings], ["texte"])
