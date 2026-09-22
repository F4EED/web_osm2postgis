# OSM2Postgis — sélectionner une zone, importer OpenStreetMap dans PostGIS

Même principe que le projet **pmtiles** : une carte d'Europe locale, un polygone cliqué pour délimiter une emprise (3 sommets ou plus). Au lieu d'écrire une archive `.pmtiles`, l'application **télécharge les objets OSM de la zone** (API Overpass) et les **charge dans PostGIS**, dans un schéma dédié (`osm_loire`, `osm_annecy`…).

Rien à compiler. Il faut le PostgreSQL local (port 5432, base `gmc`) avec l’extension PostGIS, et Python 3.10+ avec GDAL/OGR — déjà présents si QGIS est installé.

Les URL, licences et tables de **chaque source** sont listées dans **[sources.md](sources.md)**.

## Démarrage

En service permanent (recommandé) — survit à la fermeture de Cursor et se relance à l’ouverture de session :

```bash
mkdir -p ~/.config/systemd/user
cp osm2postgis.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now osm2postgis
```

Puis ouvrez **http://127.0.0.1:8001/**.

À la main, le temps d’un essai :

```bash
python3 serve.py
```

N'ouvrez pas `index.html` en `file://` : le navigateur a besoin du serveur pour l'API et les GeoJSON.

```bash
systemctl --user status osm2postgis
journalctl --user -u osm2postgis -f
```

Connexion PostGIS : variables `PG*` dans `.env` (modèle `.env.example`). Le fichier local `pwd.md` (non versionné) peut rappeler les comptes de la machine.

## Utilisation

1. La carte s'affiche comme dans pmtiles (pays, départements, villes).
2. Choisissez le **fond** : Europe locale, OpenStreetMap, ou **OpenTopoMap** (relief + courbes raster).
3. **Sélectionner une zone**, cliquez les sommets **dans l'ordre** autour de l'emprise (3 ou plus, 64 max), puis **Terminer** (ou double-clic / Entrée).
4. Donnez un nom : il devient le schéma PostgreSQL `osm_<nom>`. Cochez selon le besoin :
   - **Courbes de niveau** (défaut) → table `contours` (MNT SRTM / AWS Terrain) ;
   - **Données maritimes SHOM** (défaut) → tables `shom_*` ;
   - **Risques et crises** (défaut) → tables `risk_*` (Géorisques, CATNAT, DICRIM, BDHI).
5. L’import crée aussi, toujours, les **carroyages DFCI et UTM** (`grid_dfci`, `grid_utm`) et extrait les **bâtiments officiels** OSM (`officiels`).
6. Un dialogue récapitule les opérations PostGIS : **Oui** lance, **Non** annule. L’import tourne en arrière-plan. À la fin, un aperçu se superpose à la carte.

L’aperçu web reprend un rendu type **OpenTopoMap** (occupation du sol, hydrographie, voies ferrées, routes, courbes de niveau). Les tuiles raster OTM/OSM passent en fond très atténué pour que les **vecteurs importés** se voient. Les carroyages DFCI/UTM et les aplats d’aléas (argiles, sismique, zonage, submersion) restent en base et dans QGIS, mais **ne s’affichent plus** dans cet aperçu.

Les courbes sont interpolées en **Web Mercator (EPSG:3857)** puis stockées en **EPSG:4326**. Un import déjà fait uniquement en 4326 donne des courbes irrégulières : **réimporter la zone avec l’option courbes** pour les recalculer.

En cas d’échec, le message est ajouté à **`erreurs.md`**.

Dans **QGIS** : Ajouter une couche PostGIS

| | |
|---|---|
| Hôte | `127.0.0.1` |
| Port | `5432` |
| Base | `gmc` |
| Utilisateur / mot de passe | `gmc` / `gmc` |
| Schéma | `osm_<nom>` |

### Tables OSM

`points`, `lines`, `multilinestrings`, `multipolygons`, `other_relations` (colonne `geom`, index GIST).

### Autres tables du schéma

| Table | Contenu |
|---|---|
| `contours` | Courbes de niveau (`elev`) |
| `shom_limite_terre_mer` | Limite terre-mer IGN-SHOM |
| `shom_epaves`, `shom_obstructions` | Épaves et obstructions |
| `shom_feux`, `shom_bouees` | Balisage |
| `shom_cables` | Câbles sous-marins |
| `shom_3_milles` | Limite des 3 milles |
| `shom_natures_fond` | Natures de fond |
| `risk_cavites` | Cavités souterraines |
| `risk_mvt` | Mouvements de terrain |
| `risk_pprn_inondation` | Périmètres PPR inondation |
| `risk_pprn_submersion` | Périmètres PPR submersion marine |
| `risk_argiles` | Aléa retrait-gonflement des argiles |
| `risk_tri` | TRI (inondation) |
| `risk_zonage_sismique` | Zonage sismique réglementaire |
| `risk_sis_historique` | Intensités macrosismiques (SIS) |
| `risk_catnat` | Arrêtés CATNAT (point au chef-lieu) |
| `risk_dicrim` | DICRIM (année + lien Géorisques) |
| `risk_bdhi` | Fiches de synthèse BDHI (crues historiques, lien PDF) |
| `grid_dfci` | Carroyage DFCI 100 / 20 / 2 km |
| `grid_utm` | Carroyage UTM 10 km (et 1 km si la zone est petite) |
| `officiels` | Bâtiments officiels OSM, stylés par `categorie` |

Les tables `shom_*` vides (hors littoral) sont supprimées. **Pas pour la navigation.**

Les couches `risk_*` et `risk_dicrim` sont **informatives**. Elles ne remplaceront pas un PPR, un atlas des zones inondables, un porter-à-connaissance ou un DICRIM officiel. Les [repères de crues](https://www.reperesdecrues.developpement-durable.gouv.fr/) n’ont pas de WFS public : ils ne sont pas importés.

Style QGIS : enregistré **dans PostGIS** (`public.layer_styles`, style `opentopo` par défaut). En ajoutant une couche (Browser / Ajouter une couche PostGIS), QGIS l’applique tout seul. Copie locale : `qgis/*.qml` (dont `grid_dfci.qml` et `officiels.qml`).

Suivi des imports : schéma `osm2postgis.imports`.

## Limites

- **Une seule importation à la fois.**
- Zone bornée à **3° de côté** : Overpass timeout au-delà, surtout en ville dense. Un département rural passe ; l'Île-de-France entière, non — resserrez.
- Il faut **Internet** le temps du téléchargement OSM, MNT, WFS SHOM, Géorisques, CATNAT, GASPAR, BDHI et geo.api.gouv.fr. Les grilles DFCI/UTM et `officiels` sont calculés en local.
- Les cartes marines **raster** du SHOM restent un fond WMS (non importées) ; l’import vecteur ne remplace pas une carte marine officielle.
- Le serveur utilise le PostgreSQL local déjà présent sur le **port 5432** (base `gmc`). Pour une autre instance, éditez `.env`.

## Licence

GNU GPL v3 — voir `LICENSE`. Interface et carte dérivées de [pmtiles](https://github.com/F4EED/pmtiles).

Données : voir **[sources.md](sources.md)** (OSM ODbL, SHOM, Géorisques / BRGM / GASPAR Licence Ouverte, etc.).
