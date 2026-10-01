import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

import config
from src.casino_parser import parse_casino_ticket
from src.parser_dispatcher import parse_ticket
from src.pdf_extractor import PDFExtractor
from tests.test_rescans_and_crop import OCR_PATCH, make_a4_scan
from tests.test_ticket_database import DatabaseTestCase

# Consistent receipt (hand-written, in the layout read by the OCR on a real Casino receipt).
CASINO_TEXT = """Casino
hyperFrais
Service gratuit + prix appel
OEUFS PPA X6 2.81€
COMTE 4/6M AOP 4.23€
0.212kg X 19.95€/kg
LAIT BK 0.89€
KIWIS 7.00€
10 x 0.70€
TOTAL ACHATS 14.93€
VOS REMISES :
x KIWI -2.00€
Total remises -2.00€
TOTAL A REGLER (13) 12.93€
CB EMV 12.93€
Date : 07/03/2026 18:22
Caissier : 12
Mag : CG829"""

# Same receipt as read on a poor scan: the logo is lost, a stray digit, a lost euro sign,
# a lost discount sign and a wrong "total to pay".
CASINO_NOISY_TEXT = (
    CASINO_TEXT.replace("Casino\n", "Fd\n")
    .replace("OEUFS PPA X6 2.81€", "OEUFS PPA X6 2,81€")
    .replace("LAIT BK 0.89€", "LAIT BK 90.89€")
    .replace("COMTE 4/6M AOP 4.23€", "COMTE 4/6M AOP 4,23E")
    .replace("x KIWI -2.00€", "x KIWI 2.00€")
    .replace("Total remises -2.00€", "Total remises ~2,00€")
    .replace("TOTAL A REGLER (13) 12.93€", "TOTAL A REGLER { 13) 0, 456")
)


class CasinoParserTests(unittest.TestCase):
    def parse(self, text, **options):
        return parse_casino_ticket(text, "casino.png", config.FOOD_CATEGORIES, **options)

    def items(self, ticket):
        return {item["article"]: item for item in ticket["articles"]}

    def test_store_and_totals(self):
        ticket = self.parse(CASINO_TEXT)

        self.assertEqual(ticket["enseigne"], "Casino")
        self.assertEqual(ticket["total_ticket"], 12.93)
        self.assertEqual(ticket["total_remises"], 2.0)
        self.assertEqual(ticket["nombre_articles_ticket"], 13)
        self.assertEqual(ticket["articles_detectes"], 4)
        self.assertEqual(ticket["unites_detectees"], 13)

    def test_date_and_time(self):
        self.assertEqual(self.parse(CASINO_TEXT)["date"], "2026-03-07 18:22")

    def test_plain_item(self):
        item = self.items(self.parse(CASINO_TEXT))["LAIT BK"]

        self.assertEqual(item["prix_total"], 0.89)
        self.assertEqual(item["quantite"], 1)

    def test_weighed_item(self):
        item = self.items(self.parse(CASINO_TEXT))["COMTE 4/6M AOP"]

        self.assertEqual(item["poids_kg"], 0.212)
        self.assertEqual(item["prix_kg"], 19.95)
        self.assertEqual(item["prix_total"], 4.23)

    def test_item_bought_several_times(self):
        item = self.items(self.parse(CASINO_TEXT))["KIWIS"]

        self.assertEqual(item["quantite"], 10)
        self.assertEqual(item["prix_unitaire"], 0.7)
        self.assertEqual(item["prix_total"], 7.0)

    def test_items_add_up_to_the_total_before_discounts(self):
        ticket = self.parse(CASINO_TEXT)

        self.assertAlmostEqual(sum(item["prix_total"] for item in ticket["articles"]), 14.93)

    def test_noisy_reading_gives_the_same_ticket(self):
        clean = self.parse(CASINO_TEXT)
        noisy = self.parse(CASINO_NOISY_TEXT)

        self.assertEqual(noisy["total_ticket"], clean["total_ticket"])
        self.assertEqual(noisy["total_remises"], clean["total_remises"])
        self.assertEqual(
            [(i["article"], i["prix_total"]) for i in noisy["articles"]],
            [(i["article"], i["prix_total"]) for i in clean["articles"]],
        )

    def test_total_to_pay_confirmed_by_the_card_payment(self):
        text = CASINO_TEXT.replace("TOTAL A REGLER (13) 12.93€", "TOTAL A REGLER (13) 0,45€")

        self.assertEqual(self.parse(text)["total_ticket"], 12.93)

    def test_weight_price_not_explaining_the_amount_is_dropped(self):
        text = CASINO_TEXT.replace("19.95€/kg", "9.95€/kg")

        item = self.items(self.parse(text))["COMTE 4/6M AOP"]

        self.assertIsNone(item.get("prix_kg"))
        self.assertEqual(item["prix_total"], 4.23)

    def test_partial_date_is_not_kept(self):
        text = CASINO_TEXT.replace("07/03/2026", "07/?/2023")

        self.assertIsNone(self.parse(text)["date"])

    def test_missing_date_is_not_kept(self):
        text = CASINO_TEXT.replace("Date : 07/03/2026 18:22\n", "")

        self.assertIsNone(self.parse(text)["date"])

    def test_implausible_year_is_not_kept(self):
        text = CASINO_TEXT.replace("07/03/2026", "07/03/1926")

        self.assertIsNone(self.parse(text)["date"])

    def test_empty_text_is_rejected(self):
        with self.assertRaises(ValueError):
            self.parse("  \n ")

    def test_text_without_item_is_rejected(self):
        with self.assertRaises(ValueError):
            self.parse("Casino\nhyperFrais\nTOTAL ACHATS 0.00€")


class CasinoRoutingTests(unittest.TestCase):
    def test_store_name_routes_to_the_casino_parser(self):
        ticket = parse_ticket(CASINO_TEXT, "a.png", config.FOOD_CATEGORIES)

        self.assertEqual(ticket["enseigne"], "Casino")

    def test_layout_routes_a_scan_whose_logo_is_unreadable(self):
        ticket = parse_ticket(CASINO_NOISY_TEXT, "a.png", config.FOOD_CATEGORIES)

        self.assertEqual(ticket["enseigne"], "Casino")
        self.assertEqual(ticket["total_ticket"], 12.93)


class CasinoImportTests(DatabaseTestCase):
    def test_undated_casino_scan_is_imported_with_a_warning(self):
        text = CASINO_TEXT.replace("Date : 07/03/2026 18:22\n", "")
        self.add_pdf("casino.png", b"casino")

        summary = self.run_import({b"casino": text})

        self.assertEqual(summary.imported, ["casino.png"])
        self.assertIn("Date du ticket non détectée", summary.warnings["casino.png"])
        (row,) = self.connection.execute("SELECT date, total_ticket FROM tickets").fetchall()
        self.assertIsNone(row["date"])
        self.assertEqual(row["total_ticket"], 12.93)


class ScannerEdgeTests(unittest.TestCase):
    def prepared(self, image):
        return PDFExtractor(crop_to_ticket=True)._prepare_image(image)

    def test_black_lines_along_the_page_border_do_not_prevent_the_crop(self):
        image = make_a4_scan()
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 1199, 10), fill="black")
        draw.rectangle((0, 0, 10, 1599), fill="black")
        draw.rectangle((0, 400, 1199, 405), fill="black")

        result = self.prepared(image)

        self.assertLess(result.width, 450)
        self.assertGreater(result.width, 300)

    def test_scanner_edge_lines_are_ignored_in_the_crop_size(self):
        image = make_a4_scan()
        ImageDraw.Draw(image).rectangle((1189, 0, 1199, 1599), fill="black")

        result = self.prepared(image)

        self.assertLess(result.width, 450)


class ThickenedReadingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "scan.png"
        image = Image.new("L", (400, 400), "white")
        ImageDraw.Draw(image).rectangle((100, 100, 103, 300), fill="black")
        image.save(self.path)

    def test_thickened_readings_come_after_the_plain_retries(self):
        widths = []

        def read(image, **kwargs):
            widths.append(image.width)
            return "texte"

        extractor = PDFExtractor(
            image_min_width=0, ocr_retry_scales=(0.5,), ocr_thicken_scales=(0.8, 1.2)
        )
        with patch(OCR_PATCH, side_effect=read):
            readings = list(extractor.extract_alternatives(self.path))

        self.assertEqual(widths, [200, 320, 480])
        self.assertEqual(len(readings), 3)

    def test_thickening_makes_the_strokes_wider(self):
        extractor = PDFExtractor(image_min_width=0)

        plain = extractor._prepare_image(Image.open(self.path))
        thick = extractor._prepare_image(Image.open(self.path), thicken=True)

        def dark_pixels(image):
            return sum(image.convert("L").histogram()[:128])

        self.assertGreater(dark_pixels(thick), dark_pixels(plain))


if __name__ == "__main__":
    unittest.main()
