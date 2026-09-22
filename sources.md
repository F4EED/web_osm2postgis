# Sources de données — OSM2Postgis

Inventaire de **tout ce que l’application récupère, génère ou affiche**.
Les zonages et documents officiels sont **informatifs** : ils ne remplacent pas un PPR, un atlas, un DICRIM papier ni une carte marine.

Connexion PostGIS par défaut : `127.0.0.1:5432`, base `gmc` (surcharge possible via `.env`).
Les tables listées ci-dessous sont créées dans le schéma d’import `osm_<nom>`.

---

## 1. OpenStreetMap (objets de la zone)

| | |
|---|---|
| **Quoi** | Nœuds, chemins et relations OSM de l’emprise (polygone 3–64 sommets) |
| **Producteur** | Contributeurs OpenStreetMap |
| **Licence** | [ODbL](https://www.openstreetmap.org/copyright) |
| **Accès** | API Overpass, dans l’ordre : [overpass-api.de](https://overpass-api.de/api/interpreter), [overpass.kumi.systems](https://overpass.kumi.systems/api/interpreter), [overpass.openstreetmap.fr](https://overpass.openstreetmap.fr/api/interpreter) |
| **Tables** | `points`, `lines`, `multilinestrings`, `multipolygons`, `other_relations` |
| **Usage** | Import GDAL/OGR (`osmconf.ini`), découpage au polygone, aperçu carte, QGIS |
| **Option** | Toujours (cœur de l’import) |

Les tags listés dans `osmconf.ini` deviennent des colonnes ; le reste va dans `other_tags` (JSON, colonne TEXT).

---

## 2. Bâtiments officiels (extrait d’OSM)

| | |
|---|---|
| **Quoi** | Hôpitaux, cliniques, pompiers, police, gendarmerie, mairies, préfectures, administrations, justice, poste, militaire, diplomatique, bâtiments publics |
| **Producteur** | Contributeurs OpenStreetMap (même jeu que §1) |
| **Licence** | ODbL |
| **Accès** | Aucun téléchargement supplémentaire : extraction SQL après import OSM |
| **Table** | `officiels` (points, attribut `categorie`) |
| **Usage** | Couche métier + style QGIS `qgis/officiels.qml` (forme et couleur selon l’appartenance) |
| **Option** | Toujours, après le clip |

---

## 3. Fonds de carte (affichage navigateur uniquement)

Ces tuiles **ne sont pas importées** dans PostGIS.

| Fond | URL | Producteur | Licence | Usage |
|---|---|---|---|---|
| Europe locale | Fichiers `data/*.geojson` servis par `serve.py` | Natural Earth / map-of-europe ; [france-geojson](https://github.com/gregoiredavid/france-geojson) | Licences des jeux d’origine (Natural Earth domaine public ; france-geojson selon le dépôt) | Contours pays, départements FR, villes |
| OpenStreetMap | `https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png` | OSMF / contributeurs OSM | ODbL + [tile usage](https://operations.osmfoundation.org/policies/tiles/) | Fond raster |
| OpenTopoMap | `https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png` | OpenTopoMap | CC-BY-SA (OSM + SRTM) | Fond raster par défaut (relief) |

Quand un schéma importé est affiché, l’aperçu vectoriel (occupation du sol, hydro, rail, routes, courbes) se superpose en style OpenTopoMap ; le raster OTM/OSM est opacifié (~0,2) pour éviter un mélange illisible. Les grilles DFCI/UTM et les aplats d’aléas ne sont plus dessinés dans cet aperçu (ils restent dans PostGIS / QGIS).

Noms français des pays : table locale `js/pays-fr.js` (pas une API).

---

## 4. Modèle numérique de terrain → courbes de niveau

| | |
|---|---|
| **Quoi** | Tuiles altimétriques GeoTIFF (famille Mapzen / AWS Terrain Tiles, dérivées notamment de SRTM), puis courbes interpolées localement |
| **Producteur** | NASA/NGA (SRTM) et assemblage Mapzen / AWS Terrain Tiles |
| **Licence** | Données SRTM largement réutilisables ; tuiles AWS Terrain : voir [terrain-tiles](https://registry.opendata.aws/terrain-tiles/) |
| **Accès** | `https://s3.amazonaws.com/elevation-tiles-prod/geotiff/{z}/{x}/{y}.tif` (zoom 8–13, max 64 tuiles) |
| **Table** | `contours` (lignes, attribut `elev`, identifiant `ogc_fid`) |
| **Usage** | Style type OpenTopoMap ; aperçu carte |
| **Calcul** | Interpolation des courbes en **EPSG:3857** (Web Mercator), puis écriture PostGIS en **EPSG:4326**. Un schéma déjà importé sans cette étape doit être **réimporté avec l’option courbes**. |
| **Option** | Case **Courbes de niveau** (cochée par défaut) |

---

## 5. SHOM / IGN — données maritimes (WFS)

| | |
|---|---|
| **Quoi** | Limite terre-mer, épaves, obstructions, feux, bouées, câbles, limite des 3 milles, natures de fond |
| **Producteur** | SHOM, IGN (limite terre-mer) |
| **Licence** | Licence Ouverte / mentions SHOM — **pas pour la navigation** |
| **Accès** | WFS `https://services.data.shom.fr/INSPIRE/wfs` (filtre spatial EPSG:4326) |
| **Tables** | `shom_limite_terre_mer`, `shom_epaves`, `shom_obstructions`, `shom_feux`, `shom_bouees`, `shom_cables`, `shom_3_milles`, `shom_natures_fond` |
| **Usage** | Import GDAL VectorTranslate ; tables vides supprimées |
| **Option** | Case **Données maritimes SHOM** (cochée par défaut) |

Couche WFS → table :

| Table | TypeName WFS |
|---|---|
| `shom_limite_terre_mer` | `LIMTM_2154_WFS:limite_terre_mer_france_metropolitaine_ligne` |
| `shom_epaves` | `EPAVES_BDD_WFS:wrecks` |
| `shom_obstructions` | `EPAVES_BDD_WFS:obstrn` |
| `shom_feux` | `BALISAGE_BDD_WFS:lights` |
| `shom_bouees` | `BALISAGE_BDD_WFS:boylat` |
| `shom_cables` | `CABLES_BDD_WFS:cblsub_lv` |
| `shom_3_milles` | `LIMITES_PECHE_BDD_WFS:limite_3milles_peche_wgs84_epsg4326` |
| `shom_natures_fond` | `NDF_BDD_WLD_WGS84G_WFS:natures_fond_50000` |

Les cartes marines **raster** du SHOM ne sont pas importées.

---

## 6. Géorisques — zonages de risque (WFS)

| | |
|---|---|
| **Quoi** | Cavités, mouvements de terrain, PPR inondation/submersion, aléa argiles, TRI, zonage sismique, intensités macrosismiques |
| **Producteur** | Géorisques / BRGM / ministère (Licence Ouverte) |
| **Licence** | Licence Ouverte |
| **Accès** | WFS `https://georisques.gouv.fr/services` |
| **Usage** | Connaissance des risques à titre informatif |
| **Option** | Case **Risques et crises** (cochée par défaut) |

| Table | Couche WFS | Contenu |
|---|---|---|
| `risk_cavites` | `ms:CAVITE_LOCALISEE` | Cavités souterraines |
| `risk_mvt` | `ms:MVT_LOCALISE` | Mouvements de terrain |
| `risk_pprn_inondation` | `ms:PPRN_PERIMETRE_INOND` | Périmètres PPR inondation |
| `risk_pprn_submersion` | `ms:PPRN_PERIMETRE_SUBMAR` | Périmètres PPR submersion marine |
| `risk_argiles` | `ms:ALEARG_REALISE` | Aléa retrait-gonflement des argiles |
| `risk_tri` | `ms:LIMITETRI_FXX` | Territoires à risque important d’inondation |
| `risk_zonage_sismique` | `ms:risq_zonage_sismique` | Zonage sismique réglementaire |
| `risk_sis_historique` | `ms:SIS_INTENSITE_EVTCOM` | Intensités SIS (communes) |

Non importé : [repères de crues](https://www.reperesdecrues.developpement-durable.gouv.fr/) (pas de WFS public).

---

## 7. CATNAT (mémoire des crises)

| | |
|---|---|
| **Quoi** | Arrêtés de catastrophe naturelle (GASPAR) |
| **Producteur** | Géorisques / DGPR |
| **Licence** | Licence Ouverte |
| **Accès** | API v1 `https://georisques.gouv.fr/api/v1/gaspar/catnat` (`latlon`, `rayon` ≤ 20 km, pagination) |
| **Géométrie** | Absente dans l’API → point au **chef-lieu** de commune via geo.api.gouv.fr |
| **Table** | `risk_catnat` (`code_catnat`, dates, `risque`, `insee`, `commune`) |
| **Option** | Incluse dans **Risques et crises** |

---

## 8. BDHI — Base de données historiques sur les inondations

Complément de CATNAT : **événements remarquables** (souvent plus anciens, documentés) plutôt que des arrêtés administratifs.

| | |
|---|---|
| **Quoi** | Inondations historiques à impacts significatifs (Moyen Âge → ~2018) : crues, ruissellements, nappes, submersions marines, cyclones, ruptures |
| **Producteur** | DGPR / INRAE (RiverLy), Cerema, Acthys ; archivage Géorisques et Recherche Data Gouv |
| **Licence** | Licence Ouverte / Etalab 2.0 pour les fiches ouvertes |
| **État** | **Plus maintenue** depuis 2023. Le site [bdhi.fr](https://bdhi.fr/) est fermé (intranet État seulement). Pas d’API ni de WFS public |
| **Option** | Incluse dans **Risques et crises** |

### Ce qui vaut d’être stocké (retenu)

Le catalogue des **222 fiches de synthèse** (1770–2018) : identifiant, années, titre (type + lieu), lien vers le PDF.

| | |
|---|---|
| **Accès** | [doi:10.57745/DKTV1G](https://doi.org/10.57745/DKTV1G) — `ListeFichesSyntheseBDHI2025_05_19.tab` + PDF `Synthese_N.pdf` |
| **Table** | `risk_bdhi` (`numero`, `titre`, `type_inond`, `annee_debut`, `annee_fin`, `url`) |
| **Géométrie** | Pas dans le tableau : point calé sur les communes / département / région de l’emprise dont le nom apparaît dans le titre |
| **Usage** | Mémoire des crises, culture du risque, lien vers la fiche PDF |

### Intéressant mais pas importé (trop lourd ou inaccessible)

| Objet BDHI | Intérêt | Pourquoi on ne le stocke pas |
|---|---|---|
| Dump Géorisques (~499 Mo) | Notes d’inondation, fiches document, parfois **périmètre inondé** (contour) | Téléchargement Géorisques actuellement en erreur ; trop volumineux pour un import de zone ; pas de WFS |
| Fiches document (rapports, presse, archives) | Bibliographie, pièces scannées | Droits de diffusion variables ; pas une couche SIG |
| Relevés hydro / météo / marée | Intensité, période de retour | Tableaux imbriqués dans les notes, pas dans le TSV ouvert |
| Victimes, dommages chiffrés | Gravité | Dans le PDF, pas structurés dans le catalogue |
| Site BDHI / intranet | Recherche avancée | Fermé au public |

CATNAT reste plus **exhaustif et à jour** (arrêtés). La BDHI apporte les **grandes crues historiques** (1910, Xynthia, Malpasset, cyclones DOM, etc.) qui n’ont pas toujours un arrêté « inondation » comparable.

Page de référence : [Géorisques — BDHI](https://www.georisques.gouv.fr/base-de-donnees/BDHI).

---

## 9. DICRIM

| | |
|---|---|
| **Quoi** | Liste des communes ayant un DICRIM déclaré + lien vers le PDF s’il est hébergé |
| **Producteur** | Communes / GASPAR / Géorisques |
| **Licence** | Licence Ouverte (métadonnées GASPAR) ; le PDF reste un document communal |
| **Accès liste** | Export `http://files.georisques.fr/GASPAR/gaspar.zip` → `dicrim_gaspar.csv` (cache 24 h dans `data/cache/`) |
| **Accès communes** | `https://geo.api.gouv.fr/communes?bbox=…&fields=nom,code,centre` |
| **Lien document** | `https://www.georisques.gouv.fr/DICRIM/{code_insee}` (PDF s’il existe, sinon erreur côté Géorisques) |
| **Table** | `risk_dicrim` (`insee`, `commune`, `annee`, `date_publi`, `url`) |
| **Option** | Incluse dans **Risques et crises** |

Toutes les communes de France ne sont pas dans GASPAR ; toutes les fiches n’ont pas un PDF en ligne.

---

## 10. geo.api.gouv.fr (géocodage communal)

| | |
|---|---|
| **Quoi** | Chef-lieu (centroïde) et liste des communes d’une emprise |
| **Producteur** | Etalab / DINUM (API Géo) |
| **Licence** | Licence Ouverte (découpages administratifs IGN/INSEE) |
| **Accès** | `https://geo.api.gouv.fr/communes/{code}?fields=centre` ; `…/departements/{dept}/communes?fields=centre,code` ; `…/communes?bbox=` |
| **Tables** | Pas de table propre : sert `risk_catnat`, `risk_dicrim` et `risk_bdhi` |
| **Usage** | Positionner CATNAT, DICRIM et les fiches BDHI |

---

## 11. Carroyage DFCI (généré, pas téléchargé)

| | |
|---|---|
| **Quoi** | Mailles 100 km, 20 km et 2 km en Lambert II étendu (EPSG:27572), reprojetées en WGS84 |
| **Référence** | Grille Défense de la Forêt Contre les Incendies (Lambert II étendu, origine Y = 1 600 000 m) |
| **Licence** | Grille calculée localement (`carroyage.py`) ; pas de fichier data.gouv téléchargé |
| **Table** | `grid_dfci` (`code`, `niveau`, `niveau_m`) |
| **Usage** | Style QGIS `qgis/grid_dfci.qml` (100 km rouge, 20 km orange, 2 km or + étiquettes) |
| **Option** | Toujours, pour toute zone qui intersecte la métropole |

Hors France métropolitaine (bornes Lambert de la grille) : aucune maille.

---

## 12. Carroyage UTM (généré)

| | |
|---|---|
| **Quoi** | Mailles 10 km (et 1 km si l’emprise est assez petite) dans le fuseau UTM Nord qui couvre la zone (30N / 31N / 32N en métropole) |
| **Référence** | WGS84 / UTM (EPSG:32630, 32631, 32632…) |
| **Licence** | Calcul local (`carroyage.py`) |
| **Table** | `grid_utm` (`code`, `zone`, `easting`, `northing`, `niveau_m`) |
| **Usage** | Style QGIS `qgis/grid_utm.qml` |
| **Option** | Toujours |

---

## 13. Styles QGIS

| | |
|---|---|
| **Quoi** | Fichiers `.qml` copiés dans `public.layer_styles` (style par défaut `opentopo`) |
| **Producteur** | OSM2Postgis |
| **Licence** | GPL-3 du projet |
| **Accès** | Fichiers locaux `qgis/*.qml` — aucun téléchargement |
| **Table** | `public.layer_styles` (base `gmc`, pas le schéma `osm_*`) |
| **Usage** | QGIS applique le style à l’ajout de la couche |

| Fichier | Couches |
|---|---|
| `points.qml`, `lines.qml`, `multilinestrings.qml`, `multipolygons.qml`, `other_relations.qml` | OSM (rendu type OpenTopoMap ; `lines.qml` : routes + rivières/canaux/ruisseaux + voies ferrées) |
| `contours.qml` | Courbes de niveau |
| `grid_dfci.qml` | Carroyage DFCI |
| `grid_utm.qml` | Carroyage UTM |
| `officiels.qml` | Bâtiments officiels (catégorisé) |
| `risk_dicrim.qml` | Points DICRIM et BDHI |
| Réemploi `points.qml` / `multipolygons.qml` | Couches `shom_*` et `risk_*` sans qml dédié |

---

## 14. Métadonnées d’import (local)

| Table | Rôle |
|---|---|
| `osm2postgis.imports` | Nom, schéma, emprise, taille OSM, effectifs JSON |
| `erreurs.md` | Journal des imports en échec (fichier local, pas une source distante) |

---

## Récapitulatif des options d’import

| Case à cocher | Sources |
|---|---|
| (toujours) | OSM Overpass, grilles DFCI/UTM, `officiels`, styles QGIS |
| Courbes de niveau | Tuiles MNT AWS / SRTM |
| Données maritimes SHOM | WFS SHOM |
| Risques et crises | WFS Géorisques + API CATNAT + GASPAR DICRIM + fiches BDHI + geo.api.gouv.fr |

Internet est requis pour OSM, MNT, WFS et API. Les grilles DFCI/UTM et l’extraction `officiels` sont calculées en local une fois l’OSM en base.
