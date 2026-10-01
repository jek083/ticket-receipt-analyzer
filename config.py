"""
Configuration du projet d'analyse de tickets
"""
from pathlib import Path

# Chemins
PROJECT_ROOT = Path(__file__).parent
DATA_DIR = PROJECT_ROOT / "data"
DIR_A_TRAITER = DATA_DIR / "A_TRAITER"  # nouveaux PDF à importer
DIR_TRAITE = DATA_DIR / "TRAITE"  # PDF importés avec succès
DIR_ERREUR = DATA_DIR / "ERREUR"  # PDF en échec ou doublons
DATA_PROCESSED = DATA_DIR / "processed"  # un JSON par ticket (traçabilité)
OUTPUT_DIR = PROJECT_ROOT / "output"

# Créer les dossiers s'ils n'existent pas
for folder in [DIR_A_TRAITER, DIR_TRAITE, DIR_ERREUR, DATA_PROCESSED, OUTPUT_DIR]:
    folder.mkdir(parents=True, exist_ok=True)

# Configuration OCR
OCR_ENABLED = True
OCR_LANGUAGE = 'fra+eng'  # Français et Anglais
OCR_RESOLUTION = 300
OCR_THRESHOLD = 150  # Seuil de binarisation pour les images
OCR_IMAGE_MIN_WIDTH = 1000  # Largeur (px) sous laquelle une image est agrandie avant OCR
OCR_CROP_TO_TICKET = True  # Recadre les scans A4 sur le ticket avant OCR
# Échelles (relatives à la 1re lecture) retentées tant qu'un ticket image reste incohérent
OCR_RETRY_SCALES = (0.9, 0.8, 0.7, 1.15)
# Puis, pour les impressions pâles, les mêmes relectures avec des traits épaissis
OCR_RETRY_THICKEN_SCALES = (0.86, 0.7)
OCR_IMAGE_MAX_WIDTH = 2000  # Largeur (px) au-dessus de laquelle une image est réduite avant OCR (photos/scans haute résolution)

# Fichiers générés
DB_PATH = DATA_DIR / "tickets.db"
ITEMS_CSV = OUTPUT_DIR / "ticket_items.csv"
ANALYSIS_EXCEL = OUTPUT_DIR / "analyse_tickets.xlsx"

# Patterns de parsing (à adapter selon vos tickets)
# Format: montant, date, catégorie, enseigne
PRICE_PATTERNS = [
    r'(\d+[.,]\d{2})\s*€',  # Format: 12,34 €
    r'€\s*(\d+[.,]\d{2})',  # Format: € 12,34
]

DATE_PATTERNS = [
    r'(\d{2}[/-]\d{2}[/-]\d{4})',  # DD/MM/YYYY
    r'(\d{4}[/-]\d{2}[/-]\d{2})',  # YYYY/MM/DD
]

# Enseignes connues
STORES = {
    'carrefour': ['carrefour', 'carrefour express', 'carrefour market'],
    'leclerc': ['leclerc', 'e.leclerc', 'auredis'],
    'auchan': ['auchan'],
    'intermarche': ['intermarché', 'intermarche'],
    'lidl': ['lidl'],
    'aldi': ['aldi'],
    'monoprix': ['monoprix'],
    'franprix': ['franprix'],
    'casino': ['casino'],
    'picard': ['picard', 'picard surgelés', 'picard surgeles'],
    'marcel_fils': ['marcel&fils', 'marcel & fils'],
    'casino': ['casino'],
}

# Format des magasins Intermarché d'après le code magasin imprimé sur le ticket
INTERMARCHE_FORMATS = {
    'M11621': 'Hyper',  # SAS VILDIS, Villeneuve-Loubet
    'M11669': 'Super',  # SAS IMJ, Cagnes-sur-Mer
    'M12470': 'Super',  # SAS SADIP, Hyères
}
INTERMARCHE_UNKNOWN_FORMAT = 'Inconnu'

# Catégories alimentaires
FOOD_CATEGORIES = {
    'fruits_legumes': ['fruit', 'légume', 'pomme', 'orange', 'carotte', 'tomate','epinards','puree','salade','courgette','poire','banane','kiwi','fraise','framboise'],
    'viandes': ['viande', 'poulet', 'boeuf', 'porc', 'jambon', 'steak'],
    'glaces': ['glace', 'amarena', 'nocciola', 'sorbet', 'gl'],
    'produits_laitiers': ['lait', 'yaourt', 'fromage', 'beurre', 'crème'],
    'boulangerie': ['pain', 'baguette', 'croissant', 'pâtisserie'],
    'boissons': ['eau', 'jus', 'soda', 'vin', 'bière', 'café'],
    'surgelés': ['surgelé', 'congelé', 'pizza', 'plat cuisiné'],
    'epicerie': ['pâtes', 'riz', 'huile', 'sel', 'sucre', 'farine'],
}

# Logging
LOG_LEVEL = 'INFO'