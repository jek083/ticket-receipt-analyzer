#!/usr/bin/env python3
"""
Script principal: Analyse complète des tickets de caisse
"""
import logging

# Configuration logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Import des modules
import config
from contextlib import closing

import pytesseract

from src import ticket_db
from src.analyzer import TicketAnalyzer
from src.importer import import_pending
from src.pdf_extractor import PDFExtractor
from src.ticket_io import export_items_csv
from src.visualizer import TicketVisualizer


def main() -> int:
    """Fonction principale"""

    logger.info("=" * 60)
    logger.info("ANALYSEUR DE TICKETS DE CAISSE")
    logger.info("=" * 60)

    with closing(ticket_db.connect(config.DB_PATH)) as connection:
        # 1. Import des nouveaux PDFs (extraction, parsing, base de données)
        logger.info("\n[1/4] Import des tickets de %s...", config.DIR_A_TRAITER)
        extractor = PDFExtractor(
            ocr_enabled=config.OCR_ENABLED,
            ocr_language=config.OCR_LANGUAGE,
            ocr_resolution=config.OCR_RESOLUTION,
            image_min_width=config.OCR_IMAGE_MIN_WIDTH,
            image_max_width=config.OCR_IMAGE_MAX_WIDTH,
            crop_to_ticket=config.OCR_CROP_TO_TICKET,
            ocr_retry_scales=config.OCR_RETRY_SCALES,
            ocr_thicken_scales=config.OCR_RETRY_THICKEN_SCALES,
        )
        try:
            summary = import_pending(
                connection,
                extractor,
                pending_dir=config.DIR_A_TRAITER,
                done_dir=config.DIR_TRAITE,
                error_dir=config.DIR_ERREUR,
                json_dir=config.DATA_PROCESSED,
                categories=config.FOOD_CATEGORIES,
            )
        except pytesseract.TesseractNotFoundError:
            logger.error(
                "Tesseract OCR est introuvable : import interrompu, "
                "les PDF restent dans %s.",
                config.DIR_A_TRAITER,
            )
            return 1
        if summary.total:
            logger.info(
                "✓ %s ticket(s) importé(s), %s doublon(s), %s en échec",
                len(summary.imported),
                len(summary.duplicates),
                len(summary.failures),
            )
        else:
            logger.info("Aucun fichier à traiter dans %s", config.DIR_A_TRAITER)
        if summary.ocr_imported:
            logger.warning(
                "%s ticket(s) lu(s) par OCR, à vérifier si besoin: %s",
                len(summary.ocr_imported),
                ", ".join(summary.ocr_imported),
            )
        for filename, message in summary.completed.items():
            logger.info("Ticket existant complété par %s: %s", filename, message)
        for filename, warning in summary.warnings.items():
            logger.warning("Contrôle à revoir pour %s: %s", filename, warning)

        # Les analyses portent sur l'historique complet de la base.
        canonical_tickets = ticket_db.load_tickets(connection)

    failure_count = len(summary.failures)
    if not canonical_tickets:
        logger.warning("Aucun ticket en base.")
        logger.info(
            "Placez vos tickets (PDF, PNG, JPG) dans %s et relancez.", config.DIR_A_TRAITER
        )
        return 1 if failure_count else 0
    logger.info("✓ %s ticket(s) en base", len(canonical_tickets))

    # 2. Analyse
    logger.info("\n[2/4] Analyse statistique...")
    analyzer = TicketAnalyzer(canonical_tickets)
    logger.info("✓ Analyse complète")

    # 3. Visualisations
    logger.info("\n[3/4] Génération des graphiques...")
    visualizer = TicketVisualizer(analyzer, output_dir=str(config.OUTPUT_DIR))
    visualizer.generate_all_visualizations()
    logger.info("✓ Graphiques générés")

    # 4. Export Excel
    logger.info("\n[4/4] Export des données...")
    export_items_csv(canonical_tickets, config.ITEMS_CSV)
    analyzer.export_to_excel(str(config.ANALYSIS_EXCEL))
    logger.info("✓ CSV détaillé créé: %s", config.ITEMS_CSV)
    logger.info("✓ Excel créé: %s", config.ANALYSIS_EXCEL)

    # Afficher le résumé
    analyzer.print_summary()

    logger.info("\n" + "=" * 60)
    logger.info("RÉSULTATS DISPONIBLES DANS :")
    logger.info(f"  - Dossier: {config.OUTPUT_DIR}")
    logger.info(f"  - Base de données: {config.DB_PATH}")
    logger.info(f"  - JSON par ticket: {config.DATA_PROCESSED}")
    logger.info(f"  - CSV détaillé: {config.ITEMS_CSV}")
    logger.info(f"  - Excel: {config.ANALYSIS_EXCEL}")
    logger.info("  - Graphiques interactifs (.html)")
    logger.info("=" * 60 + "\n")
    if summary.duplicates:
        logger.warning(
            "%s doublon(s) déplacé(s) dans %s.",
            len(summary.duplicates),
            config.DIR_ERREUR,
        )
    if failure_count:
        logger.warning(
            "%s fichier(s) en échec (voir %s); "
            "consultez les messages ci-dessus.",
            failure_count,
            config.DIR_ERREUR,
        )
    return 1 if failure_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
