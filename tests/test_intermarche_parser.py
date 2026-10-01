import json
import sqlite3
import unittest

import config
from src import ticket_db
from src.intermarche_parser import parse_intermarche_ticket
from src.parser_dispatcher import parse_ticket
from src.pdf_extractor import METHODE_TEXTE, ExtractionResult
from tests.test_ticket_database import (
    LECLERC_TEXT,
    PICARD_TEXT,
    DatabaseTestCase,
)
from tests.test_carrefour_auchan_parsers import AUCHAN_TEXT, CARREFOUR_TEXT

# Text of real receipts; phone, loyalty card and barcode numbers are zeroed.
_FOOTER = """----------------------------------------
RECAPITULATIF TVA
CODE TVA MT. HT MT. TVA MT. TTC
A 5,50% 27,32 1,50 28,82
**************************************
--------------------------------------
CARTE DE FIDELITE 0000000000000000000
ANCIEN SOLDE 13,96
NOUVEAU SOLDE 13,96
.
{date}
{store} C013 O9003 T0166
Ver:8.6.8.2-981 - 1.1.12.1
TICKET A CONSERVER POUR ECHANGE
MERCI DE VOTRE VISITE
000000000000000000000000"""

SUPER_TEXT = """SAS IMJ CAGNES
59 ROUTE DE FRANCE
06800 CAGNES SUR MER
TEL : 00.00.00.00.00
OUVERT DU LUNDI AU SAMEDI
DE 8H30 A 20H30
VITTEL EAU MINE.PET 0,43 EUR A
ST YORRE EAU M.GAZ 6 0,56 EUR A
SKYR FRAISE 450G 2,69 EUR A
ALVALLE GAZPACHO 1L 3,55 EUR A
1 Vignette MOULINEX
FE GRUYERE IGP FRANC 3,60 EUR A
FE PPX BRIE PASTEURI 2,45 EUR A
LESIEUR MAYONNAISE T 0,82 EUR A
APTA EPGES GRAT. SYN 1,13 EUR B
GRESSINS ROMARIN 2,79 EUR A
MELON PIECE 2,49 EUR A
CONCOMBRE PIECE 1,89 EUR A
LA NVL AGRI.12 OEUFS 4,00 EUR A
LVZ 10 CAPSULES NAP 3,55 EUR A
2 10E=1Vignette
3 Total Vign. MOULINEX
MONTANT DU 29,95 EUR
BON DE REDUCTION 1,00 EUR
TRD UP 25,00 EUR
CB SANS CONTACT 3,95 EUR
Nombre d'articles vendus= 13
TOTAL ELIGIBLE TRD 26,03 EUR
A RENDRE 0,00 EUR
""" + _FOOTER.format(date="19:13:16 25/08/2026", store="M11669") + """
Remises immédiates
BON DE REDUCTION 1 -1,00 EUR"""

HYPER_TEXT = """SAS VILDIS
328 BVD DES ITALIENS
06270 VILLENEUVE-LOUBET
TEL 00.00.00.00.00
OUVERT DU LUNDI AU SAMEDI
DE 8H30 A 20H30
P&C BAG CONSTANCE CE 1,05 EUR A
DUCROS HERBE PROVENC 3,75 EUR A
Dont 0,04 EUR ECO-PART DEA/PMCB
SCOT ADH MULTIREPAR 7,05 EUR B
FE ITM JBN CT AC LR 1,87 EUR A
SIGN.DENT CHARBON DE 1,71 EUR B
APTA CARRE VAISSELLE 1,35 EUR B
ST L SUCRE POUDRE BV 1,82 EUR A
KIWI JAUNE PIECE
8 X 0,45 EUR 3,60 EUR A
ALSA LEVUR.CHIM.AIR 0,72 EUR A
APTA EPONGE GRAT.DCE 1,24 EUR B
PAT GATEAU DE RIZ 4X 1,57 EUR A
RAISIN CHASSELAS AOP 3,74 EUR A
COURGETTE VRAC 1,75 EUR A
3 10E=1Vignette
3 Total Vign. MOULINEX
MONTANT DU 31,22 EUR
CB SANS CONTACT 31,22 EUR
Nombre d'articles vendus= 20
TOTAL ELIGIBLE TRD 19,87 EUR
A RENDRE 0,00 EUR
""" + _FOOTER.format(date="18:40:41 29/09/2026", store="M11621") + """
MES AVANTAGES CARTE
AVANTAGES PROMOTIONNELS
RAISIN CHASSELAS AOP 0,75
TOTAL AVANTAGES CARTE CUMULES 0,75"""

DISCOUNT_TEXT = """SAS IMJ CAGNES
59 ROUTE DE FRANCE
06800 CAGNES SUR MER
FM CHILI CON CARNE&R 3,33 EUR A
MERCI FROMAGE BLANC 2,09 EUR A
RAISIN NOIR MUSCAT V 3,01 EUR A
BOND PUREE DUO DOUCE 3,55 EUR A
STARB.RISTRETTO SHOT 4,01 EUR A
STARBUCKS NESPRESSO 4,13 EUR A
STARB.NES.ESPRES.ROA 4,22 EUR A
P&C PAIN CAMPAGNARD 1,69 EUR A
FE GRUYERE IGP FRANC 3,60 EUR A
2+1 STARBUCKS -4,01
MONTANT DU 25,62 EUR
2 10E=1Vignette
CB SANS CONTACT 25,62 EUR
Nombre d'articles vendus= 9
TOTAL ELIGIBLE TRD 25,62 EUR
""" + _FOOTER.format(date="18:40:36 15/09/2026", store="M11669") + """
Remises immédiates
REMISES IMMEDIATES 1 -4,01 EUR"""

# Large receipt (unknown store code) with weighed articles, a cancelled weighed line
# and promotions marked with a star.
LARGE_TEXT = """SAS SADIP
22 Av. De Lattre de Tassigny
83400 HYERES
TEL : 00 00 00 00 00
SIRET:00000000000000
AOP CDR VLG RG EXP.C 13,40 EUR B
OKAY ET ADAPT X3 BLA 2,61 EUR B
REGAINBIO AMANDE LEG 1,55 EUR A
BJ.BOIS.VEG.AMANDE N 3,04 EUR A
PAT VEG BOISSON AMAN 1,91 EUR A
PAT FF NATURE 3,2%MG 1,79 EUR A
CAPRICE DES DIEUX 30 3,60 EUR A
LEERDAMMER PORTION 3 5,00 EUR A
LEERDAMMER PORTION 3 5,00 EUR A
AND.BRASSE VEGETAL P 2,96 EUR A
AND.BRASSE VEGETAL F 2,86 EUR A
PAT YRT GRECQ NAT 4X 1,29 EUR A
PAT SKYR MYRTILLE 45 2,79 EUR A
PAT YRT GRECQ NAT 4X 1,29 EUR A
PAT YRT GRECQ NAT 4X 1,29 EUR A
ENTREMONT COMTE FRUI 4,01 EUR A
PAT YRT BIO CITRON 4 1,44 EUR A
FM BLC PLT ROTI 4T 1 1,70 EUR A
FM FINES BLANC POULE 2,33 EUR A
RANOU JBON PARIS DD 4,34 EUR A
RANOU JBON PARIS DD 4,34 EUR A
RANOU JBON PARIS DD 4,34 EUR A
PATURETTE CHOCO NOIR 1,00 EUR A
DANETTE CHOCO NOIR E 1,61 EUR A
DANETTE CHOCO NOIR E 1,61 EUR A
PATURAGES BUCHE STE 2,99 EUR A
PRIMEVERE TART/CUIS 2,19 EUR B
MARTINET CAROT.RAPE 2,19 EUR A
MART.TABOULE ORIENT. 2,09 EUR A
ST ELOI CHAMP.EMINCE 1,07 EUR A
*CASSEG.AUBER.CUIS.P 5,68 EUR A
PN THON ENTIER H.OLV 3,83 EUR A
ODYSSEE THON ALB TRC 4,03 EUR A
MERCI OEUFS PLEIN AI 1,65 EUR A
VOLAE 18 OEUFS PPA M 4,50 EUR A
*CASSEG.COUR.CUIS.PR 5,68 EUR A
*CASSEG.RATAT.CUIS.P 5,68 EUR A
GINGEMBRE VRAC
0,194 kg X 5,99EURO/kg 1,16 EUR A
NECTARINE BLANCHE VR
0,620 kg X 4,29EURO/kg 2,66 EUR A
NECTARINE BLANCHE VR
0,620 kg X -4,29EURO/kg -2,66 EUR A
PECHE BLANCHE VRAC S
0,620 kg X 2,49EURO/kg 1,54 EUR A
PECHE JAUNE VRAC STA
0,490 kg X 2,49EURO/kg 1,22 EUR A
2EME A -60% LEERDAMM -3,00
25% CAS.AUBERGINE PR -1,42
25% CAS.COURGETTE PR -1,42
25% CASS.RATATOUILLE -1,42
11 10E=1Vignette
MONTANT DU 115,34 EUR
CB EMV 115,34 EUR
Nombre d'articles vendus= 40
TOTAL ELIGIBLE TRD 99,33 EUR
A RENDRE 0,00 EUR
ESPECES 0,00 EUR
----------------------------------------
RECAPITULATIF TVA
CODE TVA MT. HT MT. TVA MT. TTC
A 5,50% 92,08 5,06 97,14
B 20,00% 15,17 3,03 18,20
TOTAL TVA 107,25 8,09 115,34
Remises immédiates
REMISES IMMEDIATES 4 -7,26 EUR
--------------
CARTE FIDELITE 0000000000000000000
FIDELITE INDISPONIBLE
VOS AVANTAGES SERONT DISPONIBLES SUR
UN PROCHAIN TICKET DE CAISSE OU DANS
VOTRE MAGASIN.
18:57:39 1/07/2026
M12470 C003 O0035 T0245
Ver:8.6.8.2-981 - 1.1.12.1
000000000000000000000000"""


def items_by_name(ticket):
    return {item["article"]: item for item in ticket["articles"]}


class SuperTicketTests(unittest.TestCase):
    def setUp(self):
        self.ticket = parse_intermarche_ticket(
            SUPER_TEXT, "super.pdf", config.FOOD_CATEGORIES
        )

    def test_reads_header_and_totals(self):
        ticket = self.ticket
        self.assertEqual(ticket["fichier_source"], "super.pdf")
        self.assertEqual(ticket["enseigne"], "Intermarché")
        self.assertEqual(ticket["format_magasin"], "Super")
        self.assertEqual(ticket["date"], "2026-08-25 19:13")
        self.assertEqual(ticket["total_ticket"], 29.95)
        self.assertEqual(ticket["nombre_articles_ticket"], 13)

    def test_reads_every_article_and_ignores_vouchers_and_vignettes(self):
        self.assertEqual(self.ticket["articles_detectes"], 13)
        self.assertEqual(self.ticket["unites_detectees"], 13)
        total = sum(item["prix_total"] for item in self.ticket["articles"])
        self.assertAlmostEqual(total, 29.95)

    def test_a_discount_voucher_paid_at_the_till_is_not_a_discount(self):
        self.assertIsNone(self.ticket["total_remises"])

    def test_vat_letter_is_kept(self):
        items = items_by_name(self.ticket)
        self.assertEqual(items["MELON PIECE"]["tva_code"], "A")
        self.assertEqual(items["APTA EPGES GRAT. SYN"]["tva_code"], "B")

    def test_articles_are_categorized_and_have_no_rayon(self):
        item = items_by_name(self.ticket)["VITTEL EAU MINE.PET"]
        self.assertEqual(item["categorie"], "boissons")
        self.assertIsNone(item["rayon"])


class HyperTicketTests(unittest.TestCase):
    def setUp(self):
        self.ticket = parse_intermarche_ticket(HYPER_TEXT, categories=config.FOOD_CATEGORIES)

    def test_reads_format_date_and_totals(self):
        self.assertEqual(self.ticket["format_magasin"], "Hyper")
        self.assertEqual(self.ticket["date"], "2026-09-29 18:40")
        self.assertEqual(self.ticket["total_ticket"], 31.22)

    def test_quantity_line_gives_quantity_and_unit_price(self):
        kiwi = items_by_name(self.ticket)["KIWI JAUNE PIECE"]
        self.assertEqual(kiwi["quantite"], 8)
        self.assertEqual(kiwi["prix_unitaire"], 0.45)
        self.assertEqual(kiwi["prix_total"], 3.60)
        self.assertEqual(kiwi["tva_code"], "A")

    def test_units_match_the_printed_article_count(self):
        self.assertEqual(self.ticket["articles_detectes"], 13)
        self.assertEqual(self.ticket["unites_detectees"], 20)
        self.assertEqual(self.ticket["nombre_articles_ticket"], 20)

    def test_eco_participation_and_card_advantages_are_ignored(self):
        names = items_by_name(self.ticket)
        self.assertEqual(names["DUCROS HERBE PROVENC"]["prix_total"], 3.75)
        self.assertNotIn("RAISIN CHASSELAS AOP 0,75", names)
        total = sum(item["prix_total"] for item in self.ticket["articles"])
        self.assertAlmostEqual(total, 31.22)


class DiscountTicketTests(unittest.TestCase):
    def test_discount_line_feeds_total_discounts_with_gross_prices(self):
        ticket = parse_intermarche_ticket(DISCOUNT_TEXT)

        gross = sum(item["prix_total"] for item in ticket["articles"])
        self.assertEqual(ticket["articles_detectes"], 9)
        self.assertEqual(ticket["total_remises"], 4.01)
        self.assertAlmostEqual(gross - ticket["total_remises"], ticket["total_ticket"])
        self.assertEqual(ticket["total_ticket"], 25.62)


class LargeTicketTests(unittest.TestCase):
    def setUp(self):
        self.ticket = parse_intermarche_ticket(LARGE_TEXT, categories=config.FOOD_CATEGORIES)
        self.items = items_by_name(self.ticket)

    def test_totals_are_consistent_with_the_printed_ones(self):
        ticket = self.ticket
        gross = sum(item["prix_total"] for item in ticket["articles"])
        self.assertEqual(ticket["total_ticket"], 115.34)
        self.assertEqual(ticket["total_remises"], 7.26)
        self.assertAlmostEqual(gross - ticket["total_remises"], 115.34)
        self.assertEqual(ticket["articles_detectes"], 40)
        self.assertEqual(ticket["unites_detectees"], ticket["nombre_articles_ticket"])

    def test_weighed_line_gives_weight_and_price_per_kg(self):
        ginger = self.items["GINGEMBRE VRAC"]
        self.assertEqual(ginger["poids_kg"], 0.194)
        self.assertEqual(ginger["prix_kg"], 5.99)
        self.assertEqual(ginger["prix_total"], 1.16)
        self.assertEqual(ginger["quantite"], 1)

    def test_negative_weighed_line_cancels_the_earlier_one(self):
        self.assertNotIn("NECTARINE BLANCHE VR", self.items)
        self.assertNotIn("0,620 kg X -4,29EURO/kg", self.items)

    def test_weighed_lines_are_not_named_after_their_weight(self):
        self.assertFalse(any("EURO/kg" in name for name in self.items))

    def test_promotion_star_is_removed_from_the_name(self):
        self.assertIn("CASSEG.AUBER.CUIS.P", self.items)
        self.assertFalse(any(name.startswith("*") for name in self.items))

    def test_repeated_articles_are_kept_as_separate_lines(self):
        names = [item["article"] for item in self.ticket["articles"]]
        self.assertEqual(names.count("RANOU JBON PARIS DD"), 3)

    def test_store_code_gives_super_and_single_digit_day_is_read(self):
        self.assertEqual(self.ticket["format_magasin"], "Super")
        self.assertEqual(self.ticket["date"], "2026-07-01 18:57")

    def test_cancellation_without_a_matching_line_is_ignored(self):
        text = LARGE_TEXT.replace(
            "NECTARINE BLANCHE VR\n0,620 kg X 4,29EURO/kg 2,66 EUR A\n", "", 1
        )
        self.assertNotEqual(text, LARGE_TEXT)

        self.assertEqual(parse_intermarche_ticket(text)["articles_detectes"], 40)


class ParserEdgeCaseTests(unittest.TestCase):
    def test_unknown_store_code_gives_an_unknown_format(self):
        text = SUPER_TEXT.replace("M11669", "M99999")

        self.assertEqual(
            parse_intermarche_ticket(text)["format_magasin"],
            config.INTERMARCHE_UNKNOWN_FORMAT,
        )

    def test_missing_date_and_totals_are_none(self):
        text = "\n".join(
            line
            for line in SUPER_TEXT.splitlines()
            if "25/08/2026" not in line
            and not line.startswith(("MONTANT DU", "Nombre d'articles"))
        )

        ticket = parse_intermarche_ticket(text)

        self.assertIsNone(ticket["date"])
        self.assertIsNone(ticket["total_ticket"])
        self.assertIsNone(ticket["nombre_articles_ticket"])

    def test_empty_text_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "vide"):
            parse_intermarche_ticket("  \n")

    def test_text_without_article_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Aucun article"):
            parse_intermarche_ticket("SAS IMJ CAGNES\nMONTANT DU 1,00 EUR")


class DispatcherTests(unittest.TestCase):
    def test_routes_by_layout_although_the_name_is_not_printed(self):
        for text in (SUPER_TEXT, HYPER_TEXT, DISCOUNT_TEXT):
            self.assertNotIn("intermarch", text.casefold())
            self.assertEqual(parse_ticket(text)["enseigne"], "Intermarché")

    def test_a_product_named_after_another_store_does_not_hijack_the_ticket(self):
        text = SUPER_TEXT.replace("MELON PIECE", "PICARD PIZZA")

        self.assertEqual(parse_ticket(text)["enseigne"], "Intermarché")

    def test_other_retailers_are_not_taken_for_intermarche(self):
        self.assertEqual(parse_ticket(PICARD_TEXT)["enseigne"], "Picard")
        self.assertEqual(parse_ticket(CARREFOUR_TEXT)["enseigne"], "Carrefour")
        self.assertEqual(parse_ticket(AUCHAN_TEXT)["enseigne"], "Auchan")
        self.assertIn("Leclerc", parse_ticket(LECLERC_TEXT)["enseigne"])

    def test_other_retailers_have_no_store_format(self):
        self.assertNotIn("format_magasin", parse_ticket(PICARD_TEXT))
        self.assertNotIn("format_magasin", parse_ticket(AUCHAN_TEXT))


class StoreFormatPersistenceTests(DatabaseTestCase):
    def test_hash_ignores_an_empty_store_format(self):
        ticket = parse_ticket(PICARD_TEXT, "a.pdf")

        self.assertEqual(
            ticket_db.hash_ticket(ticket),
            ticket_db.hash_ticket({**ticket, "format_magasin": None}),
        )

    def test_hash_depends_on_the_store_format(self):
        ticket = parse_ticket(SUPER_TEXT, "a.pdf")

        self.assertNotEqual(
            ticket_db.hash_ticket(ticket),
            ticket_db.hash_ticket({**ticket, "format_magasin": "Hyper"}),
        )

    def test_import_stores_format_in_database_and_json(self):
        self.add_pdf("hyper.pdf", b"hyper")
        self.add_pdf("picard.pdf", b"picard")

        summary = self.run_import({b"hyper": HYPER_TEXT, b"picard": PICARD_TEXT})

        self.assertEqual(sorted(summary.imported), ["hyper.pdf", "picard.pdf"])
        rows = dict(
            self.connection.execute(
                "SELECT enseigne, format_magasin FROM tickets"
            ).fetchall()
        )
        self.assertEqual(rows["Intermarché"], "Hyper")
        self.assertIsNone(rows["Picard"])
        saved = json.loads((self.json_dir / "hyper.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["format_magasin"], "Hyper")
        self.assertIsNone(summary.warnings.get("hyper.pdf"))

    def test_load_tickets_returns_the_format_only_when_known(self):
        self.add_pdf("super.pdf", b"super")
        self.add_pdf("picard.pdf", b"picard")
        self.run_import({b"super": SUPER_TEXT, b"picard": PICARD_TEXT})

        tickets = {t["enseigne"]: t for t in ticket_db.load_tickets(self.connection)}

        self.assertEqual(tickets["Intermarché"]["format_magasin"], "Super")
        self.assertNotIn("format_magasin", tickets["Picard"])

    def test_same_ticket_is_still_detected_as_a_duplicate(self):
        self.add_pdf("a.pdf", b"a")
        self.add_pdf("b.pdf", b"b")

        summary = self.run_import({b"a": DISCOUNT_TEXT, b"b": DISCOUNT_TEXT})

        self.assertEqual(len(summary.imported), 1)
        self.assertEqual(len(summary.duplicates), 1)

    def test_migrates_a_database_without_the_store_format_column(self):
        path = self.pending.parent / "old.db"
        old = sqlite3.connect(path)
        old.executescript(
            "CREATE TABLE tickets (id INTEGER PRIMARY KEY, content_hash TEXT NOT NULL "
            "UNIQUE, enseigne TEXT NOT NULL, date TEXT, total_ticket REAL, "
            "total_remises REAL, nombre_articles_ticket INTEGER, articles_detectes "
            "INTEGER NOT NULL, unites_detectees INTEGER NOT NULL, fichier_source TEXT "
            "NOT NULL, created_at TEXT NOT NULL);"
            "INSERT INTO tickets VALUES (1,'h','Picard',NULL,1,NULL,1,1,1,'a','now');"
        )
        old.commit()
        old.close()

        for _ in range(2):  # idempotent
            connection = ticket_db.connect(path)
            self.addCleanup(connection.close)
            row = connection.execute("SELECT format_magasin FROM tickets").fetchone()
            self.assertIsNone(row[0])


if __name__ == "__main__":
    unittest.main()
