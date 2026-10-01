import json
import unittest

import config
from src.lidl_parser import parse_lidl_ticket
from src.marcel_fils_parser import parse_marcel_fils_ticket
from src.parser_dispatcher import parse_ticket

# Consistent receipt (hand-written, in the layout read by the OCR on a real Lidl receipt).
LIDL_TEXT = """LIDL
66 Avenue de Verdun
Ticket de vente
Article P.U.EUR Qté Montant EUR
Mélange de graines 3,59 2 7,18 A
Rem fruits secs -1,79
Kiwi 0,59 6 3,54 A
Rem Kiwi vert pièce -0,59
Spécialités lait lat 1,98 1 1,98 A
Tubes qui piquent 1,43 1 1,43 B
Chocolat noir 85% ca 1,87 2 3,74 A
Banane Bio Fairtrade 1,52 A
0,726 kg x 2,09 EUR/kg
Nombre de lignes: 6
A payer 17,01
Carte 17,01
TVA Taux MONT.TTC MONT.TVA TOTAL HT
A 5,5% 15,58 0,81 14,77
B 20% 1,43 0,24 1,19
Total Promotion : 2,38
Enregistrez-vous sur Lidl Plus
1401 08/04/09/01 11.09.26 18:49:08
Disponibles sur lidl.fr"""

# Same receipt as read in poor conditions: accents, glyphs and separators are wrong.
LIDL_NOISY_TEXT = LIDL_TEXT.replace("LIDL\n", "LéDL |\n").replace(
    "Mélange de graines 3,59 2 7,18 A", "Mélange de graines 3,59 2 BAT"
).replace("Kiwi 0,59 6 3,54 A", "Kiwi 0,59 6 .,3,54 À").replace(
    "A payer 17,01", "À payer 17,01."
).replace("Nombre de lignes: 6", "Nombre de dignes: 6 À")

# Amounts are excluding VAT; the customer name and code must never be kept.
MARCEL_TEXT = """marcel&fils
02/05/2026 10:32
1 rue Exemple
06000 NICE
Code Caisse : 02
Ticket : 20441258
Date vente : 02/05/2026
Code client : 2110200034903
Mme Dupont Exemple
COUSCOUS PETIT EPEAU
4.26 € x 1 (Taux 5,50 %)
Montant net HT : 4.26 €
FL MYRTILLE BARQUETT
2.45 € x 1 (Taux 5,50 %)
Montant net HT : 2.45 €
THE VERT JASMIN'T 16
7.54 € x 1 (Taux 5,50 %)
Montant net HT : 7.54 €
FL MANGUE AMELIE
1.41 € x 1 (Taux 5,50 %)
Montant net HT : 1.41 €
Total HT : 15.66 €
TVA Taux 5,50 % : 0.86 €
Total TTC : 16.52 €
Reglement(s)
Espèces : 16.52 €"""


class LidlParserTests(unittest.TestCase):
    def setUp(self):
        self.ticket = parse_lidl_ticket(LIDL_TEXT, "lidl.png", config.FOOD_CATEGORIES)

    def test_reads_header_and_totals(self):
        self.assertEqual(self.ticket["enseigne"], "Lidl")
        self.assertEqual(self.ticket["date"], "2026-09-11 18:49")
        self.assertEqual(self.ticket["total_ticket"], 17.01)
        self.assertEqual(self.ticket["total_remises"], 2.38)

    def test_printed_line_count_is_the_number_of_lines_not_units(self):
        self.assertEqual(self.ticket["nombre_articles_ticket"], 6)
        self.assertEqual(self.ticket["articles_detectes"], 6)
        self.assertEqual(self.ticket["unites_detectees"], 13)

    def test_items_minus_discounts_add_up_to_the_total(self):
        items_total = sum(item["prix_total"] for item in self.ticket["articles"])

        self.assertAlmostEqual(items_total - self.ticket["total_remises"], 17.01)

    def test_quantities_and_vat_codes(self):
        kiwi = next(i for i in self.ticket["articles"] if i["article"] == "Kiwi")

        self.assertEqual((kiwi["quantite"], kiwi["prix_total"], kiwi["tva_code"]), (6, 3.54, "A"))

    def test_weighed_item_keeps_weight_and_price_per_kilo(self):
        banana = next(i for i in self.ticket["articles"] if "Banane" in i["article"])

        self.assertEqual(
            (banana["poids_kg"], banana["prix_kg"], banana["prix_total"]),
            (0.726, 2.09, 1.52),
        )

    def test_discount_lines_are_not_articles(self):
        names = [item["article"] for item in self.ticket["articles"]]

        self.assertFalse(any(name.startswith("Rem") for name in names))

    def test_noisy_ocr_gives_the_same_amounts(self):
        noisy = parse_lidl_ticket(LIDL_NOISY_TEXT, "lidl.png", config.FOOD_CATEGORIES)

        self.assertEqual(noisy["total_ticket"], 17.01)
        self.assertEqual(noisy["nombre_articles_ticket"], 6)
        self.assertEqual(
            [item["prix_total"] for item in noisy["articles"]],
            [item["prix_total"] for item in self.ticket["articles"]],
        )

    def test_unreadable_line_total_is_rebuilt_from_unit_price_and_quantity(self):
        noisy = parse_lidl_ticket(LIDL_NOISY_TEXT, "lidl.png", config.FOOD_CATEGORIES)

        self.assertEqual(noisy["articles"][0]["prix_total"], 7.18)
        self.assertEqual(noisy["articles"][0]["quantite"], 2)

    def test_empty_text_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "vide"):
            parse_lidl_ticket(" \n ")

    def test_text_without_article_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Aucun article"):
            parse_lidl_ticket("LIDL\nTicket de vente\nA payer 1,00")


class MarcelFilsParserTests(unittest.TestCase):
    def setUp(self):
        self.ticket = parse_marcel_fils_ticket(
            MARCEL_TEXT, "marcel.png", config.FOOD_CATEGORIES
        )

    def test_reads_header_and_totals(self):
        self.assertEqual(self.ticket["enseigne"], "Marcel&Fils")
        self.assertEqual(self.ticket["date"], "2026-05-02 10:32")
        self.assertEqual(self.ticket["total_ticket"], 16.52)
        self.assertIsNone(self.ticket["nombre_articles_ticket"])

    def test_prices_are_converted_to_vat_inclusive_amounts_adding_up_to_the_total(self):
        totals = [item["prix_total"] for item in self.ticket["articles"]]

        self.assertEqual(totals, [4.49, 2.59, 7.95, 1.49])
        self.assertAlmostEqual(sum(totals), 16.52)

    def test_unit_price_and_vat_code(self):
        item = self.ticket["articles"][0]

        self.assertEqual((item["prix_unitaire"], item["tva_code"]), (4.49, "5.5%"))

    def test_no_rounding_adjustment_when_net_lines_do_not_match_total_ht(self):
        text = MARCEL_TEXT.replace("Total HT : 15.66", "Total HT : 99.00")

        ticket = parse_marcel_fils_ticket(text)

        self.assertEqual(
            [item["prix_total"] for item in ticket["articles"]], [4.49, 2.58, 7.95, 1.49]
        )

    def test_quantity_and_other_vat_rate(self):
        text = """Marcel&Fils
Date vente : 02/05/2026
Piles
3.00 € x 2 (Taux 20,00 %)
Montant net HT : 6.00 €
Total HT : 6.00 €
Total TTC : 7.20 €"""

        item = parse_marcel_fils_ticket(text)["articles"][0]

        self.assertEqual((item["quantite"], item["prix_unitaire"], item["prix_total"]), (2, 3.6, 7.2))

    def test_noisy_ocr_is_tolerated(self):
        text = (
            MARCEL_TEXT.replace("4.26 € x 1 (Taux 5,50 %)", "4.26@x _ 1 (Taux.5,50-X)")
            .replace("Montant net HT : 1.41 €", "Montant net HI : 141€")
            .replace("FL MYRTILLE BARQUETT", "l FL MYRTILLE BARQUETT")
            .replace("(Taux 5,50 %)", "(Taux 5,56 %)")
        )

        ticket = parse_marcel_fils_ticket(text)

        self.assertEqual(
            [item["prix_total"] for item in ticket["articles"]], [4.49, 2.59, 7.95, 1.49]
        )
        self.assertEqual(ticket["articles"][1]["article"], "FL MYRTILLE BARQUETT")

    def test_garbled_price_line_is_not_taken_for_the_article_name(self):
        text = MARCEL_TEXT.replace("2.45 € x 1 (Taux 5,50 %)", "2.45 @ x 1 (faux 5,5 %)")

        ticket = parse_marcel_fils_ticket(text)

        self.assertEqual(
            [item["article"] for item in ticket["articles"]],
            ["COUSCOUS PETIT EPEAU", "FL MYRTILLE BARQUETT", "THE VERT JASMIN'T 16", "FL MANGUE AMELIE"],
        )
        self.assertEqual(ticket["articles"][1]["prix_total"], 2.59)

    def test_personal_data_is_never_kept(self):
        serialized = json.dumps(self.ticket, ensure_ascii=False)

        for private in ("Dupont", "2110200034903", "rue Exemple"):
            self.assertNotIn(private, serialized)

    def test_empty_text_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "vide"):
            parse_marcel_fils_ticket(" \n ")

    def test_text_without_article_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Aucun article"):
            parse_marcel_fils_ticket("marcel&fils\nTotal TTC : 1.00 €")


class DispatcherTests(unittest.TestCase):
    def test_routes_lidl_from_its_layout_when_the_logo_is_unreadable(self):
        self.assertEqual(parse_ticket(LIDL_NOISY_TEXT)["enseigne"], "Lidl")

    def test_routes_lidl_without_any_brand_word(self):
        text = "\n".join(
            line for line in LIDL_TEXT.splitlines() if "lidl" not in line.casefold()
        )

        self.assertEqual(parse_ticket(text)["enseigne"], "Lidl")

    def test_routes_marcel_fils_from_its_header(self):
        self.assertEqual(parse_ticket(MARCEL_TEXT)["enseigne"], "Marcel&Fils")

    def test_routes_marcel_fils_from_its_layout_without_the_name(self):
        text = MARCEL_TEXT.replace("marcel&fils", "m4rc3l")

        self.assertEqual(parse_ticket(text)["enseigne"], "Marcel&Fils")
