import unittest

import config
from src.auchan_parser import parse_auchan_ticket
from src.carrefour_parser import parse_carrefour_ticket
from src.parser_dispatcher import parse_ticket
from src.pdf_extractor import METHODE_OCR, ExtractionResult
from tests.test_ticket_database import (
    LECLERC_TEXT,
    PICARD_TEXT,
    DatabaseTestCase,
)

# OCR text of real receipts, with phone numbers and cashier/ticket ids removed.
CARREFOUR_TEXT = """Bienvenue chez
Carrefour 18
TEL 00 00 00 00 00
Du lundi au samedi de 8h00 à 21h00
Le dimanche de 8h00 à 12h30

DESIGNATION P.U x QTE MONTANTE

4 BQ P SAUM AP FOC 6,59

4 CAROTTE 1 5KG FAC 2,29

4 CREV 25/35 4,99

4 KIWI VERT PIECE 0,694 2,36
Remise Immédiate 0,47-

4 NOIX SECH. RDF ikG 8,15

4 750G PURE PATAT. DO 2,99
4 BAGUETINE CAMP FQC 0,85
4 MANGUE PIECE 1,99
Total Alimentaire 29,74
6 RTE MCHRS ECOPLANE 1,65
6 A00ML INSECTIC.ANT 2,15
Total Entretien Hys-BeautÜ 3,80
6 BOSCH BEG ADAPT EN 20,90
Total Non Alimentaire 20,90
14 ARTICLES | TOTAL AVANT REHISES 54,91
TOTAL REMISE IMMEDIATE 0,47-
TOTAL DES AVANTAGES DU JOUR 0,47-
14 ARTICLES TOTAL À PAYER 54,44

€ Cartes Bancaires 54,44
TVA 4: 6,50% € 28,19 1,55
TVA 6:20,00% € 20,58 4,12
zss= TOTAL TVA é 5,67"""

AUCHAN_TEXT = """FREJUS
Téléphone: 00 00 00 00 00
www. auchan, fr
Vous avez été accueilli par SCO
Le 14 juin 2024 à 18:37:58
Caisse : 000 Ticket : 0000
Hote(sse) : 0000
#K&LAMATA HUILE OLI.. 12,73
#POUCE VINAIGRE ALC.. 0,48
COSMI4 BRUMISATEUR ., 1,36
RAINETT VAISELLE MA. 2,10
*AUCHAN PAELLA 3506 4,79
*AUCHAN GAZPACHO 1L 2,99
+BROUSSE DAT MEDJOO. . 9,49
NINKASI BIÈRE BLOND. . 4,79
*AUCHAN TORTILLA OI.. 3,79
‘TROPIC OLIV VERT 0... 2,84
LINDOR NOIR 70% 1506 2,83
*FRATSE 500û 3,99
Total 52,18 €
TVAS TVA Net Brut
5,50 2,15 38,95 41,140
20,00 1,85 9,23 11,08
Brut 4,00 40,18 52,16
Reçu CARTE BANCAIRE 52,18
TOT. ARTICLES ELIGIBLES TR 41,10
TOT. PATEMENTS PAR TR 0,00
12 Articles"""

# Same receipt as read by a worse OCR pass: two prices are misread.
AUCHAN_NOISY_TEXT = AUCHAN_TEXT.replace("0... 2,84", "0... 2,64").replace(
    "1506 2,83", "1506 2,84"
)


class CarrefourParserTests(unittest.TestCase):
    def setUp(self):
        self.ticket = parse_carrefour_ticket(
            CARREFOUR_TEXT, "carrefour.png", config.FOOD_CATEGORIES
        )
        self.items = {item["article"]: item for item in self.ticket["articles"]}

    def test_reads_ticket_header_and_totals(self):
        ticket = self.ticket
        self.assertEqual(ticket["fichier_source"], "carrefour.png")
        self.assertEqual(ticket["enseigne"], "Carrefour")
        self.assertIsNone(ticket["date"])
        self.assertEqual(ticket["total_ticket"], 54.44)
        self.assertEqual(ticket["total_remises"], 0.47)
        self.assertEqual(ticket["nombre_articles_ticket"], 14)

    def test_counts_lines_and_units(self):
        self.assertEqual(self.ticket["articles_detectes"], 11)
        self.assertEqual(self.ticket["unites_detectees"], 14)

    def test_gross_prices_minus_discount_equal_the_total(self):
        gross = sum(item["prix_total"] for item in self.ticket["articles"])
        self.assertAlmostEqual(
            gross - self.ticket["total_remises"], self.ticket["total_ticket"]
        )

    def test_quantity_is_read_from_the_unit_price_field(self):
        kiwi = self.items["KIWI VERT PIECE"]
        self.assertEqual(kiwi["quantite"], 4)
        self.assertEqual(kiwi["prix_total"], 2.36)
        self.assertEqual(kiwi["prix_unitaire"], 0.59)

    def test_vat_code_is_the_leading_digit(self):
        self.assertEqual(self.items["CREV 25/35"]["tva_code"], "4")
        self.assertEqual(self.items["BOSCH BEG ADAPT EN"]["tva_code"], "6")

    def test_rayon_comes_from_the_following_section_total(self):
        self.assertEqual(self.items["MANGUE PIECE"]["rayon"], "Alimentaire")
        self.assertEqual(
            self.items["A00ML INSECTIC.ANT"]["rayon"], "Entretien Hys-BeautÜ"
        )
        self.assertEqual(
            self.items["BOSCH BEG ADAPT EN"]["rayon"], "Non Alimentaire"
        )

    def test_item_name_may_start_with_digits(self):
        self.assertEqual(self.items["750G PURE PATAT. DO"]["prix_total"], 2.99)

    def test_items_are_categorized(self):
        self.assertEqual(self.items["CAROTTE 1 5KG FAC"]["categorie"], "fruits_legumes")

    def test_discount_falls_back_to_the_difference_of_totals(self):
        text = "\n".join(
            line for line in CARREFOUR_TEXT.splitlines() if "AVANTAGES" not in line
        )

        ticket = parse_carrefour_ticket(text)

        self.assertEqual(ticket["total_remises"], 0.47)

    def test_discount_falls_back_to_the_discount_lines(self):
        text = "\n".join(
            line
            for line in CARREFOUR_TEXT.splitlines()
            if "AVANTAGES" not in line and "AVANT REHISES" not in line
        )

        ticket = parse_carrefour_ticket(text)

        self.assertEqual(ticket["total_remises"], 0.47)

    def test_empty_text_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "vide"):
            parse_carrefour_ticket(" \n ")

    def test_text_without_article_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Aucun article"):
            parse_carrefour_ticket("Carrefour\nTOTAL À PAYER 1,00")


class AuchanParserTests(unittest.TestCase):
    def setUp(self):
        self.ticket = parse_auchan_ticket(
            AUCHAN_TEXT, "auchan.png", config.FOOD_CATEGORIES
        )
        self.names = [item["article"] for item in self.ticket["articles"]]

    def test_reads_ticket_header_and_totals(self):
        ticket = self.ticket
        self.assertEqual(ticket["enseigne"], "Auchan")
        self.assertEqual(ticket["date"], "2024-06-14 18:37")
        self.assertEqual(ticket["total_ticket"], 52.18)
        self.assertEqual(ticket["nombre_articles_ticket"], 12)
        self.assertIsNone(ticket["total_remises"])

    def test_reads_every_article_between_header_and_total(self):
        self.assertEqual(self.ticket["articles_detectes"], 12)
        self.assertEqual(self.ticket["unites_detectees"], 12)
        self.assertNotIn("TVAS TVA Net Brut", self.names)

    def test_article_prices_add_up_to_the_total(self):
        total = sum(item["prix_total"] for item in self.ticket["articles"])
        self.assertAlmostEqual(total, 52.18)

    def test_names_lose_star_noise_prefix_and_truncation_dots(self):
        self.assertIn("K&LAMATA HUILE OLI", self.names)
        self.assertIn("COSMI4 BRUMISATEUR", self.names)
        self.assertIn("BROUSSE DAT MEDJOO", self.names)
        self.assertIn("TROPIC OLIV VERT 0", self.names)
        self.assertIn("AUCHAN PAELLA 3506", self.names)

    def test_star_is_not_a_vat_code(self):
        self.assertTrue(all(item["tva_code"] is None for item in self.ticket["articles"]))
        self.assertTrue(all(item["rayon"] is None for item in self.ticket["articles"]))

    def test_negative_amounts_are_not_read_as_articles(self):
        text = AUCHAN_TEXT.replace("Total 52,18", "REMISE CARTE -1,00\nTotal 52,18")

        ticket = parse_auchan_ticket(text)

        self.assertEqual(ticket["articles_detectes"], 12)

    def test_ticket_without_date_keeps_a_null_date(self):
        text = "\n".join(
            line for line in AUCHAN_TEXT.splitlines() if not line.startswith("Le 14")
        )

        self.assertIsNone(parse_auchan_ticket(text)["date"])

    def test_empty_text_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "vide"):
            parse_auchan_ticket("")

    def test_text_without_article_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Aucun article"):
            parse_auchan_ticket("www. auchan, fr\nTotal 1,00 €")


class DispatcherTests(unittest.TestCase):
    def test_routes_new_retailers_from_their_header(self):
        self.assertEqual(parse_ticket(CARREFOUR_TEXT)["enseigne"], "Carrefour")
        self.assertEqual(parse_ticket(AUCHAN_TEXT)["enseigne"], "Auchan")

    def test_header_wins_over_a_brand_quoted_in_the_products(self):
        # "AUCHAN PAELLA" is a product; a Carrefour header must still win.
        text = CARREFOUR_TEXT.replace("BAGUETINE CAMP FQC", "AUCHAN PAELLA")

        self.assertEqual(parse_ticket(text)["enseigne"], "Carrefour")

    def test_auchan_products_do_not_hide_the_auchan_header(self):
        text = AUCHAN_TEXT.replace("NINKASI BIÈRE BLOND. .", "CARREFOUR BIO")

        self.assertEqual(parse_ticket(text)["enseigne"], "Auchan")

    def test_picard_and_leclerc_are_still_routed(self):
        self.assertEqual(parse_ticket(PICARD_TEXT)["enseigne"], "Picard")
        self.assertIn("Leclerc", parse_ticket(LECLERC_TEXT)["enseigne"])

    def test_unknown_retailer_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Aucun parseur"):
            parse_ticket("Boulangerie\nBaguette 1,00")


class ImportTests(DatabaseTestCase):
    def test_imports_carrefour_and_auchan_scans_with_warnings(self):
        self.add_pdf("carrefour.png", b"carrefour")
        self.add_pdf("auchan.png", b"auchan")

        summary = self.run_import(
            {
                b"carrefour": ExtractionResult(CARREFOUR_TEXT, METHODE_OCR),
                b"auchan": ExtractionResult(AUCHAN_NOISY_TEXT, METHODE_OCR),
            }
        )

        self.assertEqual(sorted(summary.imported), ["auchan.png", "carrefour.png"])
        self.assertEqual(self.names(self.done), ["auchan.png", "carrefour.png"])
        self.assertEqual(
            summary.warnings["carrefour.png"], "Date du ticket non détectée"
        )
        self.assertIn(
            "Somme des articles différente du total", summary.warnings["auchan.png"]
        )
        stores = self.connection.execute(
            "SELECT enseigne, date FROM tickets ORDER BY enseigne"
        ).fetchall()
        self.assertEqual(
            [tuple(row) for row in stores],
            [("Auchan", "2024-06-14 18:37"), ("Carrefour", None)],
        )

    def test_missing_date_warning_is_persisted_in_the_import_trace(self):
        self.add_pdf("carrefour.png", b"carrefour")

        self.run_import({b"carrefour": ExtractionResult(CARREFOUR_TEXT, METHODE_OCR)})

        row = self.connection.execute("SELECT avertissements FROM imports").fetchone()
        self.assertIn("Date du ticket non détectée", row[0])


if __name__ == "__main__":
    unittest.main()
