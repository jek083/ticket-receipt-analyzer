# Évolution 1 — Sous-répertoires de traitement + persistance SQLite

## Contexte
Aujourd'hui `main.py` lit tous les PDF de `data/raw/`, les parse, écrase `data/processed/tickets.json`
puis génère graphiques/CSV/Excel à partir de ce JSON. Aucun état n'est conservé : chaque run retraite tout.

## Décisions validées
- **SQLite** (stdlib `sqlite3`, aucune nouvelle dépendance) ; DuckDB écarté (surdimensionné).
- **Doublon** (même hash fichier OU même hash contenu ticket) → PDF déplacé dans `ERREUR/`,
  tracé dans `imports` avec statut `DOUBLON`. Ce n'est pas un échec : le code retour reste 0.
- **Analyses** (graphiques, CSV, Excel) : basées sur **tous les tickets de `tickets.db`** (historique complet).
- **JSON de traçabilité** : un fichier par ticket, `data/processed/<nom_pdf>.json`, conservé.
- **Migration** : `data/raw` remplacé par `A_TRAITER` ; les PDF de `data/` (9) et `data/raw/` (1) sont déplacés dans `A_TRAITER`.

## Arborescence cible
```
data/
├── A_TRAITER/     # dépôt des nouveaux PDF
├── TRAITE/        # PDF importés avec succès
├── ERREUR/        # PDF en échec (extraction/parsing) ou doublons
├── processed/     # <nom_pdf>.json (traçabilité)
└── tickets.db
```

## Schéma `tickets.db`
- `tickets` : id, `content_hash` UNIQUE, enseigne, date, total_ticket, total_remises,
  nombre_articles_ticket, articles_detectes, unites_detectees, fichier_source, created_at
- `ticket_items` : id, ticket_id → tickets(id) ON DELETE CASCADE, position, article, rayon,
  prix_unitaire, quantite, poids_kg, prix_kg, prix_total, tva_code, categorie
- `imports` : id, nom_fichier, `file_hash` (SHA-256 des octets du PDF, indexé), statut
  (`TRAITE` | `ERREUR` | `DOUBLON`), message, ticket_id (nullable), chemin_json, chemin_final, imported_at
- `PRAGMA foreign_keys=ON`, requêtes paramétrées uniquement.

## Hash anti-doublon
- `file_hash` : SHA-256 du PDF → détecte le même fichier avant même l'extraction/OCR (économie de temps).
- `content_hash` : SHA-256 du JSON canonique du ticket (clés triées, **sans** `fichier_source`) →
  détecte le même ticket issu d'un fichier différent. Limite : un re-scan dont l'OCR diffère produira un hash différent.

## Flux par fichier (dans `A_TRAITER`)
1. Hash fichier → si déjà importé (statut `TRAITE`) : `DOUBLON` → `ERREUR/`.
2. Extraction (`PDFExtractor.extract_from_file`) puis `parse_ticket` + avertissements de cohérence existants.
3. Hash contenu → si présent dans `tickets` : `DOUBLON` → `ERREUR/`.
4. Écriture du JSON, puis transaction : INSERT `tickets` + `ticket_items` + `imports(TRAITE)`,
   déplacement du PDF vers `TRAITE/`, commit (rollback si le déplacement échoue).
5. Toute exception attendue (extraction/OCR/parsing/valeur) → `imports(ERREUR, message)` + déplacement vers `ERREUR/`.
6. Collision de nom à la destination → suffixe (`_1`, `_2`…), jamais d'écrasement.
Ensuite `main.py` recharge tous les tickets depuis la base au format dict canonique existant
(`articles`, `fichier_source`…) → `TicketAnalyzer`, visualiseur, `export_items_csv` inchangés.
Code retour 1 s'il y a des échecs d'extraction/parsing ; 0 pour les doublons. Si `A_TRAITER` est vide mais la base
contient des tickets, les analyses sont quand même régénérées ; si tout est vide, message d'aide et code 0.

## Todos
1. **config** : `DATA_DIR`, `DIR_A_TRAITER/TRAITE/ERREUR`, `DB_PATH`, création des dossiers ; retirer `DATA_RAW` et `TICKETS_JSON` (conserver `DATA_PROCESSED`).
2. **`src/ticket_db.py`** : connexion, création du schéma, hash fichier/contenu, `insert_ticket`, `record_import`,
   `find_import_by_file_hash`, `ticket_exists`, `load_tickets` (→ liste de dicts canoniques).
3. **`src/importer.py`** : orchestration par fichier (flux ci-dessus), déplacement sûr sans écrasement, résumé du run (importés / erreurs / doublons).
4. **`src/ticket_io.py`** : ajout d'un `write_ticket_json` (un ticket) ; `load_tickets_json`/`export_items_csv` conservés.
5. **`main.py`** : brancher importer + chargement depuis la base ; étapes/logs mis à jour.
6. **Tests** (`unittest`, répertoires temporaires, extracteur mocké) : schéma, hash stable, insertion + FK cascade,
   doublon fichier, doublon contenu, erreur d'extraction → ERREUR, collision de noms, aller-retour DB → dicts → `TicketAnalyzer`/CSV, rollback si déplacement échoue.
7. **Migration données locales** : déplacer les 10 PDF existants vers `data/A_TRAITER/` (non versionnés) ; l'ancien `data/processed/tickets.json` est laissé en place (obsolète).
8. **Docs/ignore** : README (arborescence, flux, schéma), `.gitignore` (`data/tickets.db*`).
9. **Validation** : `python -m unittest` avec le `.venv`, puis un run réel de `main.py` sur les PDF migrés et un second run pour vérifier l'idempotence (aucun réimport).

## Points d'attention
- Aucun pytest/ruff installés dans le `.venv` : la suite existante est `unittest`, on la conserve.
- Le run réel nécessite Tesseract seulement pour les PDF sans texte natif.
- La date de l'unique ticket JSON existant (`2027-01-31`) semble incohérente ; hors périmètre.

---

# Évolution 2 — Tickets scannés (PDF image pur, PNG, JPG/JPEG)

## Contexte et constats (mesurés sur le dépôt)
- `PDFExtractor` gère déjà un PDF image pur via le repli OCR page par page (pdfplumber → pypdf → Tesseract).
  `ticket-de-caisse.pdf` (racine, 3 pages, 0 caractère natif) est un vrai scan Auredis/Leclerc : OCR en ~3 s,
  23 articles sur 27 reconnus, bruit typique (`5:26`, `3:35`, `1!`, prix manquants).
- **Les fichiers PNG/JPG ne sont pas pris en charge** : `importer.import_pending` ne lit que `*.pdf` et
  `PDFExtractor` n'ouvre que des PDF.
- Le vrai risque est la **qualité de lecture**, pas l'ouverture du fichier : un ticket Picard rendu en image (654 px de large)
  donne 1 à 3 articles sur 4 selon l'échelle/psm, car le parseur Picard exige `V1 *Article prix` alors que l'OCR sort `Vil *…`.
- Tesseract 5 avec `fra`+`eng` est installé ; Pillow est déjà une dépendance → **aucune nouvelle dépendance**.

## Décisions validées
- Formats acceptés dans `A_TRAITER` : `.pdf`, `.png`, `.jpg`, `.jpeg` (insensible à la casse).
- Ticket OCR incohérent : **importé quand même avec avertissement**, et l'import est **marqué comme issu de l'OCR**.

## Approche
1. **Config** : `SUPPORTED_EXTENSIONS`, paramètres OCR image (largeur cible minimale, psm).
2. **Extraction** (`pdf_extractor.py`) : `extract_from_image` (EXIF transpose, niveaux de gris, autocontraste, agrandissement si petite image),
   routage par extension, nouveau `extract(path) -> ExtractionResult(text, methode)` (`TEXTE` | `OCR`), `extract_from_file` conservé.
   Prétraitement réglé par mesure (psm 4/6, échelles ×1/×2/×3) sur les scans disponibles.
3. **Parseurs** : tolérance au bruit OCR (Picard `V1`/`Vil`/`Vl`, Leclerc `5:26` → `5.26`, `1!` → `1`), sans régression sur le texte natif.
4. **Import** (`importer.py`) : extensions supportées, `extract()`, erreurs Pillow → `ERREUR`, stockage méthode + avertissements.
5. **Base** (`ticket_db.py`) : colonnes `imports.methode_extraction` et `imports.avertissements` + migration idempotente (`PRAGMA table_info` / `ALTER TABLE`) du `tickets.db` existant.
6. **Tests** (`unittest`) : routage images, image corrompue, OCR désactivé, EXIF, `extract` TEXTE/OCR, import PNG marqué OCR, migration, bruit OCR des parseurs, intégration OCR réelle conditionnelle.
7. **Docs** : README (formats, marquage OCR, conseils de scan ≥ 300 DPI).

## Todos (suivis en SQL)
config-images → extractor-images → importer-images (+ db-migration) → tests-scans (+ parser-ocr-noise) → docs-scans → validate-scans

## Validation prévue
Suite complète ; run réel sur une **base temporaire** avec `ticket-de-caisse.pdf`, un PNG et un JPG générés depuis des tickets existants ;
contrôle de la migration de l'ancien `tickets.db` ; comparaison des 10 JSON natifs avant/après.

## Points d'attention
- L'OCR reste imparfait ; d'où le marquage et la conservation des avertissements.
- Enseignes sans parseur dédié : rejetées (`ERREUR`) même en image.
- Seule l'orientation EXIF est corrigée (pas de redressement de perspective) ; TIFF multi-pages et HEIC hors périmètre.


# Évolution 3 — Parseurs Carrefour et Auchan

## Contexte et constats (fichiers `ticket_Auchan.png` et `ticket_Carrefour.png` dans `data/ERREUR/`)
- Les 2 fichiers sont bien lus par OCR mais rejetés par `parser_dispatcher.parse_ticket` (« Aucun parseur… »).
  `config.STORES` contient déjà `carrefour` et `auchan` ; il manque les parseurs et le routage.
- **Carrefour** (PNG 915×1862, OCR propre) :
  - Ligne article : `4 BQ P SAUM AP FOC 6,59` → le chiffre initial est le **code TVA** (`TVA 4 : 5,50 %`, `TVA 6 : 20,00 %`).
  - Ligne quantité intégrée : `4 KIWI VERT PIECE 0,694 2,36` (source réelle : `0,59x4`, l'OCR lit mal `x`/`5`).
  - Remise : ligne `Remise Immédiate 0,47-` sous l'article concerné.
  - Rayons donnés **après** leurs articles : `Total Alimentaire 29,74`, `Total Entretien Hyg-Beauté 3,80`, `Total Non Alimentaire 20,90`.
  - Synthèse : `14 ARTICLES TOTAL AVANT REMISES 54,91`, `TOTAL DES AVANTAGES DU JOUR 0,47-`, `14 ARTICLES TOTAL À PAYER 54,44`.
  - Vérifié à la main : 11 lignes = 14 unités (kiwi ×4), 30,21 − 0,47 = 29,74, puis + 3,80 + 20,90 = 54,44. **Pas de date** sur ce ticket (image rognée).
- **Auchan** (PNG 2550×3501, ticket dans la partie gauche + trait parasite) :
  - Ligne article : `*KALAMATA HUILE OLI.. 12,73` (nom tronqué avec `..`) ; l'étoile = article éligible titres-restaurant
    (les articles `*` totalisent exactement `TOT. ARTICLES ELIGIBLES TR 41,10`), **ce n'est pas un code TVA**.
  - Date : `Le 14 juin 2024 à 18:37:58` ; total : `Total 52,18 €` ; nombre d'articles : `12 Articles` ; tableau TVA à ignorer.
  - Bruit OCR constaté : `#`/`+`/`‘` pour `*`, `3506` pour `350G`, `FRATSE`, et des chiffres faux (`Total 52,16` au lieu de 52,18).
- Balayage OCR (échelle × psm 4/6) : la lecture de ces images varie beaucoup avec l'échelle (image Auchan très large : 4/8 mots-clés
  corrects à l'échelle 1, 8/8 à 0,5). À trancher **par mesure avec les parseurs** (nb d'articles, somme = total), pas au jugé.
- Le routage actuel cherche l'enseigne dans **tout** le texte : un nom de produit (« … CARREFOUR BIO ») pourrait détourner un ticket.

## Décisions de conception
- `enseigne` fixe : `"Carrefour"` / `"Auchan"` (comme `"Picard"`), pour regrouper par enseigne dans les analyses.
- Même schéma `ParsedTicket` ; articles à prix **bruts**, `total_remises` renseigné (comme Picard) → le contrôle de cohérence existant fonctionne.
- Carrefour : `tva_code` = chiffre initial, `rayon` = libellé du `Total <rayon>` qui suit, quantité/prix unitaire depuis le champ `PU x QTE`
  (quantité = chiffres après `x` ; prix unitaire = total ÷ quantité si le prix lu est incohérent), `categorie` via `categorize_item`
  (les produits non alimentaires restent `autre`, comme dans le parseur Leclerc).
- Auchan : `rayon`/`tva_code` = `None`, `*` et `..` de fin retirés du nom, quantité 1 (aucun format multi-quantité ni poids observé → non inventé).
- Tickets incomplets/incohérents : importés avec avertissements (déjà en place) ; ajout d'un avertissement **« Date du ticket non détectée »**.
- Aucune nouvelle dépendance.

## Approche
1. **`src/ocr_common.py`** (nouveau, petit) : helpers partagés par les 2 nouveaux parseurs — montant FR → float, date française
   (`14 juin 2024 à 18:37:58` → `2024-06-14 18:37`), construction d'un `ParsedItem`. Les parseurs Picard/Leclerc ne sont pas refactorés.
2. **`src/carrefour_parser.py`** et **`src/auchan_parser.py`** : `parse_carrefour_ticket` / `parse_auchan_ticket`, mêmes signatures que
   `parse_picard_ticket` ; `ValueError` explicite si texte vide ou aucun article.
3. **`src/parser_dispatcher.py`** : routage vers les 2 parseurs ; l'enseigne est cherchée **d'abord dans l'en-tête** (premières lignes),
   puis dans tout le texte comme aujourd'hui (ordre Picard, Leclerc, Carrefour, Auchan) → aucun changement pour les tickets existants.
4. **Réglage OCR des images** (`pdf_extractor.py` / `config.py`) : uniquement si la mesure sur ces 2 tickets **et** les scans existants
   (Picard PNG 330/460/654 px, scan Auredis) montre un gain sans régression — ex. réduction des images très larges (`OCR_IMAGE_MAX_WIDTH`).
5. **`src/importer.py`** : avertissement « Date du ticket non détectée » (+ test).
6. **Tests** (`unittest`) : fixtures OCR réelles anonymisées (téléphone/hôtesse retirés) en version propre **et** bruitée ; cas : articles, TVA, rayons,
   remise, quantité kiwi, totaux, comptes, date (Auchan) / absence de date (Carrefour), textes vides, routage par en-tête vs produit
   « CARREFOUR »/« AUCHAN » dans un autre ticket, non-régression Picard/Leclerc, import complet via `import_pending`.
7. **Docs** : README (enseignes prises en charge : Picard, E.Leclerc/Auredis, Carrefour, Auchan ; formats de lignes reconnus, limites),
   nouvelle section « Évolution 3 » dans `docs/PLAN_EVOLUTION_TICKETS.md`.
8. **Validation** : suite complète ; comparaison des 10 JSON natifs avant/après ; remise des 2 fichiers de `data/ERREUR/` dans `data/A_TRAITER/`
   puis `python main.py` (les anciennes lignes `ERREUR` restent dans `imports` pour l'historique ; les nouvelles passent en `TRAITE`) ;
   contrôle des tickets en base, des avertissements et des graphiques/Excel avec 4 enseignes.

## Todos (SQL)
ocr-common → carrefour-parser, auchan-parser → dispatcher → importer-date-warning → ocr-tuning (mesuré) → tests-new-parsers → docs-new-parsers → validate-new-parsers

## Points d'attention
- Un seul exemple par enseigne : les formats non observés (multi-quantités et poids Auchan, remises Auchan, autres libellés de rayon Carrefour) ne sont pas devinés ;
  un ticket qui les contient sera importé avec avertissements ou rejeté en `ERREUR`, à compléter avec de nouveaux exemples.
- L'OCR peut fausser des chiffres (Auchan : total 52,16 lu au lieu de 52,18) ; le ticket est importé, marqué `OCR`, avec l'avertissement de somme différente.
- Le ticket Carrefour n'a pas de date → absent des tendances temporelles tant que la date est `NULL`.
- Les 2 fichiers seront déplacés de `data/ERREUR/` vers `data/A_TRAITER/` puis traités (modification des données locales, comme pour la migration précédente).

## Résultat de la mesure OCR (réglage des images)
Image Auchan (2550 px) avec les parseurs : sans réduction, somme des articles 51,99 € pour un total de 52,18 € ; réduite à 2000 px, 12 articles et somme = 52,18 €.
Les largeurs voisines donnent des résultats variables (2200 px : 51,60 € ; 1800 px : total lu 52,16), le gain est donc réel mais bruité.
Retenu : `OCR_IMAGE_MAX_WIDTH = 2000` (désactivé par défaut dans `PDFExtractor`). Carrefour (agrandi à 1000 px) et les scans PDF existants ne changent pas.


# Évolution 4 — Enseigne Intermarché (Hyper / Super)

## Constats (3 PDF texte de `data/A_TRAITER/`, extraction native, aucun OCR)
- Le logo « Intermarché » est une **image** : le mot n'existe **pas** dans le texte. L'en-tête ne contient que l'exploitant
  (`SAS IMJ CAGNES` ×2, `SAS VILDIS` ×1). `config.STORES['intermarche'] = ['intermarché']` ne peut donc pas router ces tickets.
- Répartition confirmée par vous : **Cagnes (M11669) = Super**, **Villeneuve-Loubet / VILDIS (M11621) = Hyper**.
- Mise en page commune : `NOM PRIX EUR <code TVA A|B>` ; `MONTANT DU xx,xx EUR` ; `Nombre d'articles vendus= N` ;
  `RECAPITULATIF TVA` ; date/heure `19:13:16 25/08/2026` ; ligne `M11669 C013 O9003 T0166` (M = code magasin).
- Vérifié à la main : somme des articles = `MONTANT DU` pour les 3 tickets, et nombre d'unités = `Nombre d'articles vendus` (13 / 20 / 9).
- Particularités observées :
  - Hyper (VILDIS) : quantité sur 2 lignes (`KIWI JAUNE PIECE` puis `8 X 0,45 EUR 3,60 EUR A`), `Dont 0,04 EUR ECO-PART …` (à ignorer),
    « AVANTAGES CARTE » (cagnotte fidélité, hors prix → ignorée).
  - Super (IMJ) : remise dans les articles `2+1 STARBUCKS -4,01` (réelle : 29,63 − 4,01 = 25,62) ;
    `BON DE REDUCTION 1,00 EUR` = **moyen de paiement** (MONTANT DU = somme brute), donc **pas** une remise.
  - Lignes parasites à ignorer : `1 Vignette MOULINEX`, `2 10E=1Vignette`, `3 Total Vign. MOULINEX`.
- Les numéros de carte de fidélité et de code-barres sont des données personnelles : **anonymisés dans les fixtures de test**.

## Décisions validées
- Enseigne unique **« Intermarché »** (regroupement dans les analyses) ; parseur commun tolérant aux 2 formats.
- **Nouvelle colonne `tickets.format_magasin`** (`Hyper` | `Super` | `Inconnu`) + clé dans le JSON, avec migration idempotente de la base.
- Format déterminé par le **code magasin** du ticket via une table dans `config.py` (`INTERMARCHE_FORMATS = {"M11621": "Hyper", "M11669": "Super"}`) ;
  code inconnu → `Inconnu`, ticket importé quand même.

## Approche
1. **`config.py`** : `INTERMARCHE_FORMATS` ; mots-clés `intermarche`/`intermarché` conservés (ticket qui écrirait le nom).
2. **`src/intermarche_parser.py`** : `parse_intermarche_ticket(ocr_text, source_file="", categories=None)` (même signature que les autres) :
   - ligne article `NOM  12,34 EUR A` → prix, `tva_code` = lettre A/B, `rayon` None, catégorie via `categorize_item` ;
   - ligne nom seul suivie de `N X 0,45 EUR 3,60 EUR A` → quantité N, prix unitaire, total ;
   - ligne de remise `… -4,01` (dans la zone articles, avant `MONTANT DU`) → cumulée dans `total_remises` ; articles à prix **bruts** ;
   - fin des articles à `MONTANT DU` ; `total_ticket` = `MONTANT DU` ; `nombre_articles_ticket` = `Nombre d'articles vendus` ;
   - date `HH:MM:SS JJ/MM/AAAA` → `AAAA-MM-JJ HH:MM` ; code magasin `M\d{5}` → `format_magasin` ;
   - ignore vignettes, `Dont … ECO-PART`, récapitulatif TVA, cagnotte fidélité, bons de réduction (paiements) ;
   - `ValueError` si texte vide ou aucun article.
3. **`src/parser_dispatcher.py`** : détection **par signature** (l'enseigne n'est pas dans le texte) : au moins 3 marqueurs parmi
   `Nombre d'articles vendus`, `RECAPITULATIF TVA`, `TICKET A CONSERVER POUR ECHANGE`, `CARTE DE FIDELITE`, `TOTAL ELIGIBLE TRD`, `M\d{5} C\d{3} O\d{4} T\d{4}`.
   Testée après la recherche par en-tête et **avant** la recherche dans tout le texte, pour qu'un produit nommé « PICARD… » ne détourne pas le ticket.
   Aucun changement pour Picard/Leclerc/Carrefour/Auchan.
4. **Schéma** : `ParsedTicket` reçoit la clé optionnelle `format_magasin` (absente pour les autres enseignes) ;
   `ticket_db.py` : colonne `tickets.format_magasin` (CREATE + `_migrate` idempotent), `insert_ticket`, `load_tickets` ;
   `hash_ticket` **ignore la clé quand elle est vide** → les hashes des 13 tickets existants restent identiques (pas de faux non-doublons).
   `ticket_io.write_ticket_json` : écrit la clé quand elle existe.
5. **Tests** (`unittest`, `tests/test_intermarche_parser.py`) : fixtures des 3 tickets (anonymisées) ; articles, TVA, quantité kiwi (8 × 0,45),
   remise 2+1 (4,01), bon de réduction non compté en remise, ECO-PART/vignettes/cagnotte ignorés, totaux, comptes, date, format Hyper/Super/Inconnu,
   textes vides/sans article, routage par signature (et non-régression Picard/Leclerc/Carrefour/Auchan, produit « PICARD » dans un ticket Intermarché),
   migration de la base (ancienne base sans `format_magasin`), hash inchangé pour un ticket sans la clé, import complet via `import_pending`.
6. **Docs** : README (enseignes, formats, table `INTERMARCHE_FORMATS`, colonne `format_magasin`, limites) + section « Évolution 4 » dans `docs/PLAN_EVOLUTION_TICKETS.md`.
7. **Validation** : suite complète ; JSON des 11 tickets existants identiques ; copie de sauvegarde de `tickets.db` puis `python main.py` sur les 3 PDF
   (attendu : 3 `TRAITE`, Hyper/Super corrects, aucun avertissement) ; contrôle de la migration sur la vraie base ; relance → aucun doublon ; nettoyage des fichiers temporaires.

## Todos (SQL)
config-formats → intermarche-parser → dispatcher-signature → db-format-column (+ json, hash) → tests-intermarche → docs-intermarche → validate-intermarche

## Points d'attention
- **Un seul ticket Hyper et deux Super** : la distinction repose sur le code magasin, pas sur une différence de mise en page prouvée.
  Un autre magasin Intermarché sera `Inconnu` tant que son code n'est pas ajouté à `INTERMARCHE_FORMATS`.
- Formats non observés (articles au poids `kg`, autres remises, autres codes TVA) non devinés : import avec avertissements de cohérence, ou rejet en `ERREUR`.
- Les tickets sont datés de 2026 dans les PDF fournis (ils seront traités tels quels).
- La détection par signature est plus fragile qu'un nom d'enseigne : à ajuster si Intermarché change le pied de ticket.

## Résultat
- 3 PDF importés (Super ×2, Hyper ×1), sommes des articles = `MONTANT DU`, aucun avertissement ; les 13 tickets existants ont `format_magasin` à `NULL` et leurs hashes sont inchangés.
- 121 tests ; JSON des 16 tickets existants identiques avant/après.


# Évolution 5 — Lidl, Marcel&Fils (+ re-scans Auchan/Carrefour)

## Constats (4 fichiers de `data/A_TRAITER/`)
- **Aucun parseur Lidl ni Marcel&Fils** (`config.STORES` contient `lidl` mais rien ne l'utilise ; `marcel&fils` n'existe pas).
  Tels quels : Lidl → OCR vide (`i 1`) → `ERREUR` ; Marcel&Fils → OCR bruité → « Aucun parseur » → `ERREUR`.
- **Cause commune : scans A4 à plat (2550×3501, 300 DPI) où le ticket n'occupe que ~37 % de la largeur** (le reste est du fond blanc).
  Le pipeline ramène la page à 2000 px de large → le ticket devient minuscule. Lidl en plus est sur papier gris‑bleu très peu contrasté.
  Mesuré sur prototype (session `files/proto/`) : **recadrage automatique sur le ticket + normalisation du fond** → Lidl lisible (13 lignes),
  Marcel quasi parfait (seulement 1 à 2 chiffres faux selon l'échelle, ex. `2.70` lu `2.10`).
- **`Auchan_20240614.png` et `Carrefour_20251108.png` = re-scans (968/1033 px) des 2 tickets déjà en base** (Auchan 14/06/2024, 12 art., 52,18 € ; Carrefour 54,44 €, 14 art.).
  Le hash de contenu ne les reconnaît pas (OCR différent : Auchan total lu 52,16) → ils seraient **comptés en double**.
  Le Carrefour porte cette fois la date `08/11/2025` (ticket existant : date NULL) que le parseur Carrefour ne lit pas encore.

### Lidl (ticket de vente, 13 lignes, vérifié à la main)
- Ligne : `Mélange de graines 3,59 2 7,18 A T` = désignation, PU, quantité, total, lettre TVA (A 5,5 % / B 20 %), `T` = éligible titres‑restaurant (pas un code TVA).
- Remise **sous** l'article : `Rem fruits secs -1,79`. Article au poids : `Banane Bio Fairtrade 1,52 A T` puis (ligne suivante) `0,726 kg x 2,09 EUR/kg`.
- Synthèse : `Nombre de lignes: 13` (= **lignes**, pas unités : 2+6+… unités), `A payer 29,66`, `Total Promotion 2,38`, date `11.09.26 18:49:08` près du code‑barres.
- Contrôle : 32,04 (brut) − 2,38 = 29,66 ✔.
- Le logo est lu `LéDL` par l'OCR ; `lidl.fr` est en pied de ticket → routage par mise en page (`Ticket de vente`, `Nombre de lignes`, `Total Promotion`, `lidl`).
- OCR bruité sur les montants de ligne (`7,18`→`BAT`, `3,41`→`3,44`) : PU × quantité sert de recoupement.

### Marcel&Fils (facture/ticket, 11 articles)
- Bloc par article : nom / `4.26 € x 1 (Taux 5,50 %)` / `Montant net HT : 4.26 €`. Date en tête `02/05/2026 10:32`.
- **Prix de ligne HT** (somme 45,87 = `Total HT`) ; `TVA Taux 5,50 % : 2.52`, `Total TTC : 48.39`, règlement `Espèces : 48.39`.
- Conversion TTC validée : Σ(HT × 1,055 arrondis) = 48,38 → +0,01 réparti sur une ligne → 48,39 ✔.
- **Données personnelles** dans le ticket (nom, code client, rue) : **jamais stockées**, **anonymisées dans les fixtures**.

## Décisions validées
- Re‑scans : détectés comme **DOUBLON** (même enseigne, même nombre d'articles, total à 0,05 € près, date identique ou absente d'un côté)
  et **date manquante complétée** sur le ticket existant.
- Marcel&Fils : **conversion en TTC** (prix de ligne = HT × (1 + taux), arrondis ajustés pour que Σ = Total TTC).

## Approche
1. **Prétraitement des images** (`pdf_extractor.py`, `config.py`) — `OCR_CROP_TO_TICKET = True`, param `PDFExtractor(crop_to_ticket=…)` (défaut `False`, donc tests existants inchangés) :
   composition des images transparentes sur fond blanc ; normalisation du fond (division par le flou gaussien) ; **recadrage sur la bande verticale dense en pixels sombres**
   uniquement si elle occupe < ~70 % de la largeur (scan A4) ; marge ; puis min/max width existants.
   Non‑régression mesurée sur : `ticket_Auchan.png`, `ticket_Carrefour.png`, les 2 re‑scans, le scan Leclerc PDF (chemin PDF inchangé) et les images synthétiques des tests.
2. **`src/ocr_common.py`** : helper de date numérique (`JJ/MM/AAAA [HH:MM]`) ; constructeur d'article déjà présent (ajout poids/prix au kg).
3. **`src/lidl_parser.py`** (`parse_lidl_ticket`) : articles (nom, PU, quantité, total, TVA A/B) tolérants au bruit ; **total = lu s'il concorde avec PU × qté, sinon PU × qté** ;
   remise `Rem …` (négatif) → `total_remises` (prix bruts ; `Total Promotion` prioritaire) ; ligne `x,xxx kg x p EUR/kg` → `poids_kg`/`prix_kg` de l'article précédent ;
   `total_ticket` = `A payer` ; `nombre_articles_ticket` = `Nombre de lignes` ; date `JJ.MM.AA HH:MM:SS` ; enseigne « Lidl » ; `ValueError` si vide / aucun article.
4. **`src/marcel_fils_parser.py`** (`parse_marcel_fils_ticket`) : blocs nom / `PU € x QTE (Taux …)` / `Montant net HT` (lecture tolérante : `€`→`@`, `Taux 5,50-X`…),
   `Montant net HT` = vérité, PU recoupé ; conversion **TTC** avec ajustement d'arrondi **uniquement si Σ HT = Total HT** (sinon pas d'ajustement → l'avertissement de somme apparaît) ;
   `total_ticket` = `Total TTC` (repli : règlement) ; `tva_code` = taux ; date d'en‑tête ; enseigne « Marcel&Fils » ; nom/code client/adresse ignorés.
5. **`src/parser_dispatcher.py`** : ordre = mots‑clés dans l'en‑tête → **signatures de mise en page** (Intermarché, Lidl, Marcel&Fils) → mots‑clés dans tout le texte ;
   `config.STORES['marcel_fils']` ajouté ; aucun changement pour les enseignes existantes.
6. **Parseur Carrefour** : lecture de la date `JJ/MM/AAAA` de l'en‑tête (ticket sans date → inchangé).
7. **`src/importer.py`** : contrôle « Nombre d'articles » accepte **unités ou lignes** (Lidl imprime un nombre de lignes) ;
   **déduplication métier** (`ticket_db.find_similar_ticket`, SQL paramétré) après le hash de contenu ;
   doublon → `ERREUR`/`DOUBLON` ; si le ticket existant n'a pas de date et le nouveau en a une → `ticket_db.complete_ticket_date` :
   mise à jour de la date **+ recalcul de `content_hash` + réécriture du JSON** dans la même transaction ; `ImportSummary.completed`, journalisé par `main.py`.
8. **Réglage OCR mesuré** (conditionnel) : comparer échelles (1,0 / 0,7) et psm sur Lidl/Marcel avec les parseurs ; si une 2ᵉ passe d'OCR rattrape
   les tickets incohérents (somme ≠ total) sans régression, l'ajouter (sinon documenter la limite).
9. **Tests** (`unittest`) : fixtures OCR **anonymisées** (Lidl propre + bruité, Marcel propre + chiffre faux), recadrage (A4 synthétique, photo non recadrée, image transparente),
   articles/remises/poids/TVA/totaux/dates/comptes, TTC + ajustement d'arrondi + pas d'ajustement si HT incohérent, routage (Lidl/Marcel + non‑régression des 5 autres),
   dédup métier (tolérance, date absente, autre enseigne, totaux différents), complétion de date (hash/JSON), contrôle lignes vs unités, import complet via `import_pending`.
10. **Docs** : README (enseignes, formats, recadrage des scans A4, dédup métier, TTC Marcel) + section « Évolution 5 » dans `docs/PLAN_EVOLUTION_TICKETS.md`.
11. **Validation** : suite complète ; JSON/hash des tickets existants inchangés (sauf Carrefour : date complétée) ; **sauvegarde de `tickets.db`** puis `python main.py` ;
    attendu : Lidl et Marcel `TRAITE` (Lidl 29,66 €, Marcel 48,39 €), Auchan/Carrefour `DOUBLON`, Carrefour n°13 daté 2025‑11‑08 ; relance sans doublon ; nettoyage.

## Todos (SQL)
image-prep → ocr-common → (lidl-parser, marcel-parser, carrefour-date) → dispatcher → importer-dedup (+ count-check) → ocr-tuning → tests → docs → validate

## Points d'attention
- Un seul ticket par enseigne : formats non observés (Lidl : autres types de remises, consigne ; Marcel : articles au poids, TVA multiples) non devinés → avertissements ou `ERREUR`.
- OCR des scans A4 en papier thermique : quelques chiffres peuvent rester faux ; le ticket est importé `OCR` avec avertissement de somme (Lidl : recoupement PU × quantité).
- La date Lidl est lue près du code‑barres : si l'OCR la déforme, date vide + avertissement (le nom de fichier n'est **pas** utilisé).
- La dédup métier est une heuristique : deux vrais tickets identiques (même jour, même nombre d'articles, même total) seraient fusionnés — cas très improbable.
- Les re‑scans `Auchan_20240614.png` / `Carrefour_20251108.png` finissent dans `ERREUR` avec le statut `DOUBLON` (comme les autres doublons).

## Résultat
- Lidl (29,66 €, 13 lignes, remises 2,38 €) et Marcel&Fils (48,39 € TTC, 11 articles) importés en `TRAITE` ; `Auchan_20240614.png` et `Carrefour_20251108.png` détectés `DOUBLON` (nouveau scan) ;
  date `2025-11-08` ajoutée au Carrefour n°13 (hash recalculé depuis son JSON d'origine, JSON mis à jour ; seule la date change).
- Recadrage sur le ticket (`src/image_prep.py`, `OCR_CROP_TO_TICKET`) sans effet sur les tickets déjà lisibles.
- **Relectures OCR** (`OCR_RETRY_SCALES = (0.9, 0.8, 0.7, 1.15)`) : Tesseract lit un même ticket très différemment selon l'échelle ; tant qu'un ticket image
  a des avertissements de cohérence, il est relu à d'autres échelles et la lecture avec le moins d'avertissements est retenue (la 1re gagne à égalité).
  Lidl retenu à la 2ᵉ lecture, Marcel à la 3ᵉ ; les tickets cohérents du premier coup ne sont pas relus.
- Contrôle « nombre d'articles » accepté s'il égale les unités **ou** les lignes (Lidl imprime un nombre de lignes).
- **Limite connue** : la date du pied de ticket Lidl (petits chiffres) est lue de façon instable selon l'échelle (jour ou année faux possibles) — à vérifier.



# Évolution 6 — Enseigne Casino (scans A4 de mauvaise qualité, tickets sans date)

## Constats (2 fichiers de `data/A_TRAITER/` : `casino_2-1.png`, `casino_2-2.png`)
- **Aucun parseur Casino** (`config.STORES` n'a pas `casino`). Le logo « Casino #hyperFrais » est lu sur le 1er scan mais **illisible sur le 2ᵉ**
  (`tad / ( GSUTO [ci`) → routage par mise en page nécessaire (comme Lidl / Intermarché).
- **Scans A4 à plat (2552×3508, 300 DPI)**, ticket sur ~1/3 de la largeur. Le **recadrage actuel échoue sur le 1er scan** (`crop_to_receipt` renvoie `None`,
  donc page entière réduite à 2000 px) : traits noirs de bord de scanner (haut, gauche) + points parasites élargissent la bande de texte à toute la page.
  Prototype validé en session : ignorer les lignes/colonnes noires sur >50 % de la page et une bande de 1,5 % sur les bords
  → 1er scan recadré (986×2738), 2ᵉ mieux recadré (862×2757 au lieu de 885×3200), **Lidl, Marcel&Fils, Auchan, Carrefour : recadrage strictement identique**.
- **Format du ticket** (vérifié à la main sur les images) :
  - Article : `NOM  2.81€` (prix avec `€`, point décimal, pas de quantité ni TVA ni rayon).
  - Article au poids : ligne suivante `0.212kg X 19.95€/kg` (prix de la ligne = poids × prix/kg).
  - Multi-quantité : `KIWIS 7.00€` puis `10 x 0.70€` (quantité × prix unitaire).
  - `TOTAL ACHATS 54.38€` (somme brute des articles, vérifiée : Σ = 54,38) ; bloc `VOS REMISES :` (`*CAFES CHICOREES -1.93€`, `*KIWI -2.00€`) ; `Total remises -3.93€` ;
    `TOTAL A REGLER ( 29) 50.45€` (le nombre entre parenthèses = **unités** : 19 lignes simples + 10 kiwis) ; `CB EMV 50.45€`.
  - Pied : `Date : JJ/MM/AAAA / Heure : HH:MM:SS`, `Caissier`, `Mag`, `N° ticket`, `Caisse`.
  - Ticket 1 : 20 lignes, 29 unités, brut 54,38 − remises 3,93 = **50,45 €**, **pied coupé par le scan → aucune date**.
  - Ticket 2 : 19 lignes, 23 unités, brut 75,48 − remises 3,41 = **72,07 €**, date `07/?/2023` (**mois effacé**, année lue 2023/2025/2093 selon l'échelle).
- **OCR** : articles et `TOTAL ACHATS` bien lus (quelques chiffres faux selon l'échelle : `83.55`, `38.21`, `ÿ.21`, `-0ZZA` pour `POZZA`) ;
  **`TOTAL A REGLER` (gros caractères) est presque toujours faux** (`0, 456`, `00.456`, `90,45€`, absent sur le 2ᵉ) alors que `CB EMV` est fiable
  → le total doit être déduit de `TOTAL ACHATS − remises` et confirmé par `CB EMV`, pas lu sur `TOTAL A REGLER`.
  Les relectures à d'autres échelles (`OCR_RETRY_SCALES`) déjà en place rattrapent les chiffres faux.

## Décisions validées
- **Dates** : `date = NULL` pour les deux tickets + avertissement existant « Date du ticket non détectée ». Une date partielle (`07/?/2023`) n'est jamais devinée ni complétée.
- Convention de remises identique à Auchan/Intermarché/Lidl : articles à **prix brut**, `total_remises` séparé, `total_ticket` = montant payé.

## Approche
1. **`src/image_prep.py` — recadrage robuste** : dans `crop_to_receipt`, ignorer les lignes/colonnes sombres sur >50 % de la page (bords de scanner), une bande de 1,5 % sur chaque bord,
   puis même logique qu'aujourd'hui (bande dense la plus fournie, recadrage seulement si < 70 % de la largeur, marge 3 %). Tests : page synthétique avec traits noirs de bord + points parasites,
   non-régression (photo pleine largeur non recadrée, scan propre identique).
2. **`src/casino_parser.py`** — `parse_casino_ticket(ocr_text, source_file, categories)` (helpers de `ocr_common`) :
   - articles entre le logo/`3931 Service gratuit` et `TOTAL ACHATS` : `NOM prix€` tolérant au bruit (`€` lu `E`/`8`/absent, préfixes `| : ‘ ' + - *`, point ou virgule, espace avant la décimale `2. 79€`) ;
   - ligne `poids kg X prix€/kg` → `poids_kg` / `prix_kg` de l'article précédent ; ligne `N x prix€` → `quantite` / `prix_unitaire` (le total de ligne imprimé reste le prix) ;
   - bloc `VOS REMISES` : tous les montants sont des remises (le signe `-` est parfois perdu par l'OCR), `Total remises` prioritaire sinon somme ;
   - **total payé** : candidats `TOTAL ACHATS − remises`, `CB EMV` (ou autre règlement : `ESPECES`, `CB`), `TOTAL A REGLER` s'il est lisible ; on garde la valeur confirmée par au moins deux sources,
     sinon `CB EMV`, sinon la différence ; `nombre_articles_ticket` = nombre entre parenthèses de `TOTAL A REGLER` (unités) s'il est lu, sinon `None` ;
   - date : `Date : JJ/MM/AAAA` uniquement si jour, mois et année sont complets et valides (+ heure si lisible) ; sinon `None` ;
   - enseigne `Casino` ; rien de personnel conservé (caissier, n° de ticket, caisse ignorés) ; erreurs `ValueError` sur texte vide / aucun article.
3. **`parser_dispatcher.py` + `config.py`** : `config.STORES['casino'] = ['casino']` ; `('casino', parse_casino_ticket)` dans `_PARSERS` ; signature de mise en page (≥ 3 marqueurs parmi
   `TOTAL ACHATS`, `VOS REMISES`, `TOTAL A REGLER`, `CB EMV`, `hyperFrais`, `Service gratuit`, `Total remises`) pour le 2ᵉ scan au logo illisible. Non-régression des 7 autres enseignes.
4. **Tests** (`tests/test_casino_parser.py` + recadrage dans `tests/test_rescans_and_crop.py`) : fixtures cohérentes (texte propre + variantes bruitées : `€` perdu, signe des remises perdu, `TOTAL A REGLER` faux, préfixe `-0ZZA`),
   articles/poids/quantité/remises/total/compte d'unités, date complète vs partielle vs absente (→ `None`), sommes (Σ articles = `TOTAL ACHATS`, `− remises = total`), routage par mise en page sans logo, erreurs, import complet via `import_pending`.
5. **Docs** : ligne Casino dans le tableau des formats du README, section « Évolution 6 » dans `docs/PLAN_EVOLUTION_TICKETS.md` (plan + résultat).
6. **Validation** : suite complète ; recadrage des images existantes identique ; sauvegarde de `data/tickets.db` puis `python main.py`.
   **Attendu** : 2 tickets Casino en `TRAITE` (54,38 − 3,93 = 50,45 € / 29 unités ; 75,48 − 3,41 = 72,07 € / 23 unités), `date` NULL + avertissement sur les deux, JSON des tickets existants inchangés ; relance → aucun doublon.

6. **Relectures « traits épaissis »** (`OCR_RETRY_THICKEN_SCALES = (0.86, 0.7)`, `PDFExtractor(ocr_thicken_scales=…)`) : ajoutées après les essais de variantes OCR,
   car le 2ᵉ scan (très pâle) n'est lisible qu'avec des traits épaissis (`ImageFilter.MinFilter(3)`) ; utilisées seulement si les relectures précédentes laissent des avertissements.

## Todos (SQL)
e6-crop-edges → e6-casino-parser → e6-dispatcher → e6-tests → e6-docs → e6-validate

## Points d'attention
- Aucune date en base pour ces 2 tickets : ils ne figureront pas dans la tendance temporelle (la date peut être ajoutée plus tard à la main dans `tickets.date`, le hash sera alors à recalculer).
- Un seul format Casino observé (hyperFrais, magasin CG829) : Casino Proxi/Supermarché/Petit Casino, TVA ou autres modes de règlement non devinés (avertissements de cohérence ou `ERREUR`).
- `TOTAL A REGLER` n'est pas fiable à l'OCR : le compte d'unités entre parenthèses peut être absent (`nombre_articles_ticket` NULL) ; le dédoublonnage par similarité ne s'applique alors pas.
- Le recadrage modifié touche tous les scans : validé sur les 6 images existantes (résultat identique) mais à surveiller sur de nouveaux scans.

## Résultat
- Casino : 2 tickets importés en `TRAITE` — n°22 (50,45 €, 20 lignes, 29 unités, remises 3,93 €) et n°23 (72,07 €, 19 lignes, 23 unités, remises 3,41 €) ; `date` NULL
  + avertissement « Date du ticket non détectée » sur les deux. Ticket n°23 : `nombre_articles_ticket` NULL (`TOTAL A REGLER` illisible). Somme des articles = `TOTAL ACHATS` sur les deux.
- Recadrage : seuil de bord retenu à **50 %** (30 % cassait le recadrage de colonnes denses) ; 1er scan recadré (il ne l'était pas avant), autres images inchangées ; JSON et hashes des 21 tickets existants inchangés ; relance sans doublon.
- Le parseur recoupe les montants ambigus avec `TOTAL ACHATS` (variantes par ligne : imprimé, poids × prix/kg, quantité × PU, chiffre parasite retiré) et ne déduit jamais un montant manquant par différence.
- 199 tests OK (21 nouveaux dans `tests/test_casino_parser.py`).

