# Analyseur de Tickets de Caisse

Outil Python pour analyser les tickets de caisse en PDF, extraire les données d'achat et générer des tendances de prix par enseigne et catégorie alimentaire.

## 🎯 Fonctionnalités

- ✅ Extraction de texte depuis PDF (texte natif ou OCR à 300 DPI) et depuis des images scannées ou photographiées (PNG, JPG, JPEG)
- ✅ Parsing automatique des données (date, montant, catégorie, enseigne)
- ✅ Détail des articles, quantités, poids, rayons et codes TVA
- ✅ Agrégation et statistiques par enseigne, catégorie, période
- ✅ Visualisation des tendances de prix
- ✅ Persistance SQLite (`tickets.db`) avec détection des doublons par hash
- ✅ Dossiers de traitement `A_TRAITER`, `TRAITE` et `ERREUR`
- ✅ JSON par ticket (traçabilité), CSV détaillé et rapports Excel

## 📋 Prérequis

- Python 3.8+
- Tesseract OCR avec les données `fra` et `eng` (pour les PDF et images scannés)

### Installation de Tesseract

**Windows :**
```bash
# Télécharger depuis : https://github.com/UB-Mannheim/tesseract/wiki
# Ou via chocolatey
choco install tesseract
```

**macOS :**
```bash
brew install tesseract
```

**Linux :**
```bash
sudo apt-get install tesseract-ocr tesseract-ocr-fra
```

## 🚀 Installation

```bash
git clone https://github.com/jek083/ticket-receipt-analyzer.git
cd ticket-receipt-analyzer
pip install -r requirements.txt
```

## 📁 Structure du Projet

```
ticket-receipt-analyzer/
├── data/
│   ├── A_TRAITER/        # PDF/PNG/JPG à importer (dépôt des nouveaux tickets)
│   ├── TRAITE/           # Fichiers importés avec succès
│   ├── ERREUR/           # Fichiers en échec ou doublons
│   ├── processed/        # Un JSON par ticket importé
│   └── tickets.db        # Base SQLite (tickets, articles, imports)
├── src/
│   ├── pdf_extractor.py  # Extraction texte/OCR (PDF et images)
│   ├── parse_ticket_enrichi.py # Parser détaillé E.Leclerc
│   ├── picard_parser.py  # Parsing dédié aux tickets Picard
│   ├── carrefour_parser.py # Parsing dédié aux tickets Carrefour
│   ├── auchan_parser.py  # Parsing dédié aux tickets Auchan
│   ├── intermarche_parser.py # Parsing dédié aux tickets Intermarché (Hyper/Super)
│   ├── lidl_parser.py    # Parsing dédié aux tickets Lidl (OCR)
│   ├── marcel_fils_parser.py # Parsing dédié aux tickets Marcel&Fils (OCR, prix HT → TTC)
│   ├── casino_parser.py  # Parsing dédié aux tickets Casino (OCR, ticket sans date possible)
│   ├── image_prep.py     # Recadrage sur le ticket et normalisation du fond des scans
│   ├── ocr_common.py     # Helpers communs aux parseurs Carrefour/Auchan
│   ├── parser_dispatcher.py # Routage vers le parseur de l'enseigne
│   ├── ticket_db.py      # Base SQLite, hash anti-doublon
│   ├── importer.py       # Import des PDFs de A_TRAITER vers TRAITE/ERREUR
│   ├── ticket_io.py      # Lecture/écriture JSON et CSV
│   ├── analyzer.py       # Analyse statistique
│   └── visualizer.py     # Graphiques
├── output/               # Résultats (Excel, CSV, graphiques)
├── tests/                # Tests unitaires
├── config.py             # Configuration
├── requirements.txt      # Dépendances
└── main.py               # Point d'entrée
```

## 💻 Utilisation

```bash
# Déposer les nouveaux tickets (PDF, PNG, JPG, JPEG) dans data/A_TRAITER/
python main.py

# Les résultats s'affichent dans output/
```

À chaque exécution, chaque fichier de `data/A_TRAITER/` est extrait, parsé puis
stocké dans `data/tickets.db`, et déplacé :

- dans `data/TRAITE/` s'il a été importé ;
- dans `data/ERREUR/` si l'extraction/OCR ou le parsing échoue (le motif est
  tracé dans la table `imports`) ou s'il s'agit d'un doublon (statut `DOUBLON`).

Le traitement continue pour les autres fichiers. Un fichier `ERREUR` corrigé ou
remis dans `A_TRAITER` sera réimporté, sauf s'il a déjà été importé avec succès.

Le JSON de chaque ticket est conservé dans `data/processed/<nom_pdf>.json` pour
la traçabilité et les contrôles intermédiaires. Les analyses et exports portent
sur **tous les tickets de la base** : `output/ticket_items.csv` contient une
ligne par article, `output/analyse_tickets.xlsx` conserve les feuilles de
synthèse et les graphiques sont générés dans `output/`. Le code retour est 1 si
au moins un PDF est en échec ; les doublons ne sont pas des échecs.
L’OCR est partagé; `parser_dispatcher.py` choisit ensuite un parseur par
enseigne à partir du texte extrait (d'abord dans les premières lignes du
ticket, puis dans tout le texte). Les parseurs dédiés disponibles sont ceux de
Picard, d’E.Leclerc (l’alias AUREDIS utilise ce parseur), de Carrefour,
d'Auchan, d'Intermarché, de Lidl, de Marcel&Fils et de Casino. Pour ajouter une enseigne, déclarer ses alias dans `config.py`
(`STORES`), créer son parseur dédié et ajouter son routage dans
`src/parser_dispatcher.py`.

Formats reconnus pour Carrefour, Auchan, Intermarché, Lidl, Marcel&Fils et Casino :

| Enseigne | Ligne article | Particularités |
|----------|---------------|----------------|
| Carrefour | `4 KIWI VERT PIECE 0,59x4 2,36` | Le chiffre initial est le code TVA. Le rayon est celui du `Total <rayon>` qui suit. La quantité est lue dans `P.U x QTE`. Les articles sont à prix brut et `Remise Immédiate` alimente `total_remises`. Un ticket sans date est importé avec une date vide. |
| Auchan | `*KALAMATA HUILE OLI.. 12,73` | Le `*` (éligible titres-restaurant, pas un code TVA) et les `..` de troncature sont retirés du nom. Date lue dans `Le 14 juin 2024 à 18:37:58`. Quantité 1, sans rayon ni code TVA. |
| Intermarché | `KIWI JAUNE PIECE` puis `8 X 0,45 EUR 3,60 EUR A` ; `VITTEL EAU MINE.PET 0,43 EUR A` | Le logo est une image : le nom n'est pas dans le texte, le ticket est reconnu par sa mise en page (`Nombre d'articles vendus=`, `RECAPITULATIF TVA`, code magasin `M11669 C013 O9003 T0166`…). La lettre finale (A, B) est le code TVA. Une remise imprimée entre les articles (`2+1 STARBUCKS -4,01`) alimente `total_remises` (articles à prix brut) ; un `BON DE REDUCTION` payé en caisse n'est pas une remise. Les articles au poids (`0,194 kg X 5,99EURO/kg 1,16 EUR A`) renseignent `poids_kg` et `prix_kg` ; une ligne au poids négative annule la ligne correspondante ; l'étoile de promotion (`*CASSEG…`) est retirée du nom. Vignettes, `Dont … ECO-PART` et cagnotte fidélité sont ignorées. |
| Lidl | `Mélange de graines 3,59 2 7,18 A` (désignation, prix unitaire, quantité, total, lettre TVA A 5,5 % / B 20 %) ; `Rem fruits secs -1,79` ; `0,726 kg x 2,09 EUR/kg` sous l'article pesé | Ticket sur papier peu contrasté, lu à partir du scan recadré. Les lignes `Rem` alimentent `total_remises` (`Total Promotion` prioritaire), `A payer` donne le total, `Nombre de lignes` donne le nombre d'articles (en lignes). Un total de ligne mal lu est recalculé (prix unitaire × quantité) de façon à retrouver `A payer`. **La date du pied de ticket est petite : à vérifier.** |
| Marcel&Fils | `NOM` / `4.26 € x 1 (Taux 5,50 %)` / `Montant net HT : 4.26 €` | Les prix sont imprimés **hors taxes** : ils sont convertis en TTC (`tva_code` = `5.5%`), l'arrondi étant ajusté au centime près pour que la somme égale `Total TTC`, si les lignes correspondent bien à `Total HT`. Nom, code client et adresse du client imprimés sur le ticket ne sont jamais conservés. |
| Casino | `OEUFS PPA X6 2.81€` (désignation, prix avec `€`) ; `0.212kg X 19.95€/kg` sous l'article pesé ; `10 x 0.70€` sous un article multiple ; bloc `VOS REMISES` ; `TOTAL ACHATS` ; `CB EMV` | Scan A4 de faible qualité (logo parfois illisible : routage par mise en page). Articles au prix brut, `total_remises` séparé (`Total remises` prioritaire). Les montants mal lus sont corrigés par recoupement avec `TOTAL ACHATS` ; `total_ticket` = `TOTAL ACHATS` − remises, confirmé par le règlement (`TOTAL A REGLER`, illisible à l'OCR, n'est qu'un indice) ; `nombre_articles_ticket` = nombre d'unités entre parenthèses s'il est lu. **Date : `NULL` si elle est coupée, effacée ou incomplète (jamais devinée) → avertissement « Date du ticket non détectée ».** |

**Hyper / Super Intermarché** : les deux formats partagent le même parseur ; le format est déduit du code
magasin imprimé sur le ticket grâce à la table `INTERMARCHE_FORMATS` de `config.py`
(`M11621` → Hyper, `M11669` et `M12470` → Super) et enregistré dans `tickets.format_magasin` et dans le
JSON. Un magasin dont le code est absent de la table est marqué `Inconnu` : ajoutez son code
à la table. L'enseigne reste `Intermarché` dans toutes les analyses.

Ces formats ont été établis à partir de quelques tickets par enseigne : multi-quantités,
poids et remises Auchan, ou autres libellés de rayon Carrefour ne sont pas
gérés. Un ticket qui les contient est importé avec des avertissements, ou
rejeté dans `ERREUR`.

### Tickets scannés et photos

Les PDF « image pure » et les fichiers `.png`, `.jpg` et `.jpeg` (extension
insensible à la casse) sont lus par OCR (Tesseract, `fra+eng`). Les photos sont
remises à l'endroit selon leur orientation EXIF, converties en niveaux de gris,
contrastées, et agrandies si elles font moins de `OCR_IMAGE_MIN_WIDTH` pixels de
large et réduites au‑delà de `OCR_IMAGE_MAX_WIDTH` (`config.py`) : Tesseract
lit mieux une photo haute résolution ramenée à environ 2000 pixels. Les autres
formats sont ignorés.

Un scan A4 où le ticket n'occupe qu'une bande étroite est **recadré
automatiquement** sur le ticket (`OCR_CROP_TO_TICKET`), avec un fond normalisé (papier
gris ou teinté, PNG transparent). Un ticket image dont les contrôles de cohérence
échouent est **relu par OCR à d'autres échelles** (`OCR_RETRY_SCALES`, puis
avec des traits épaissis : `OCR_RETRY_THICKEN_SCALES`, utile aux scans très pâles) et la
meilleure lecture est conservée : les relectures ne concernent que les tickets
problématiques.

Chaque import est tracé dans `imports` avec sa méthode d'extraction
(`methode_extraction` : `TEXTE` ou `OCR`) et les éventuels contrôles à revoir
(`avertissements` : date ou total non détecté, somme des articles différente du total,
nombre d'articles différent). Un ticket lu par OCR est **importé même s'il est
incohérent** : `main.py` liste les tickets OCR et leurs avertissements pour que
vous puissiez les vérifier, par exemple avec :

```sql
SELECT nom_fichier, avertissements FROM imports
WHERE methode_extraction = 'OCR' AND avertissements IS NOT NULL;
```

Conseils pour de bons résultats : scan ou photo bien droit, éclairé et net
(idéalement 200 à 300 DPI), ticket entier sur une seule image. Un PDF dont
l'image intégrée est de très faible résolution (moins de 150 DPI environ) peut
être mal lu. Les bases créées avant cette évolution sont migrées
automatiquement (`methode_extraction` vaut alors `NULL`).

### Base de données `tickets.db`

| Table | Contenu |
|-------|---------|
| `tickets` | Un ticket : enseigne, format du magasin (`format_magasin`, Intermarché), date, totaux, hash du contenu (unique) |
| `ticket_items` | Articles d'un ticket (suppression en cascade avec le ticket) |
| `imports` | Trace de chaque fichier : nom, hash SHA-256, statut (`TRAITE`, `ERREUR`, `DOUBLON`), message, chemins, méthode d'extraction (`TEXTE`/`OCR`), avertissements |

Un fichier est un doublon si son hash SHA-256 correspond à un import réussi, ou si
le contenu parsé du ticket (hors nom de fichier) existe déjà. Un nouveau scan
d'un même ticket dont l'OCR diffère est reconnu comme doublon (statut `DOUBLON`,
fichier déplacé dans `ERREUR`) lorsque l'enseigne, le nombre d'articles imprimé et le jour
sont les mêmes et que le total est à 0,05 € près (un ticket qui n'imprime pas son nombre
d'articles n'est pas concerné). Si le ticket déjà en base n'avait pas de date et que le
nouveau scan en donne une, la date est ajoutée au ticket existant.

L’export Excel comporte six feuilles, avec des libellés et catégories en
français : « Par enseigne », « Par catégorie », « Tendance temporelle »,
« Évolution par enseigne », « Prix par catégorie et enseigne » et
« Top 20 des articles ». La tendance temporelle utilise la date extraite des
tickets.

## 📊 Exemple de Sortie

- Tendances de prix par catégorie
- Comparaison des enseignes
- Évolution des dépenses selon la date des tickets
- Export Excel avec six feuilles de synthèse en français
- JSON par ticket, base SQLite et CSV détaillé par article

## 🔧 Configuration

Éditer `config.py` pour :
- Ajouter/modifier les enseignes dans `STORES`
- Ajouter/modifier les catégories dans `FOOD_CATEGORIES`
- Adapter les patterns de parsing `PRICE_PATTERNS` et `DATE_PATTERNS`

## 📝 Licence

MIT

## 👤 Auteur

jek083