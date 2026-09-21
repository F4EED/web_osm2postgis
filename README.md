# OSM2Postgis — sélectionner une zone, importer OpenStreetMap dans PostGIS

Même principe que le projet **pmtiles** : une carte d'Europe locale, quatre clics pour délimiter une emprise. Au lieu d'écrire une archive `.pmtiles`, l'application **télécharge les objets OSM de la zone** (API Overpass) et les **charge dans PostGIS**, dans un schéma dédié (`osm_loire`, `osm_annecy`…).

Rien à compiler. Il faut Docker (base PostGIS) et Python 3.10+ avec GDAL/OGR — déjà présents si QGIS est installé.

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
docker compose up -d
python3 serve.py
```

N'ouvrez pas `index.html` en `file://` : le navigateur a besoin du serveur pour l'API et les GeoJSON.

```bash
systemctl --user status osm2postgis
journalctl --user -u osm2postgis -f
```

## Utilisation

1. La carte s'affiche comme dans pmtiles (pays, départements, villes).
2. Cochez **Fond OpenStreetMap (en ligne)** si vous voulez viser plus précisément.
3. **Sélectionner une zone**, puis quatre clics (l'ordre n'a pas d'importance).
4. Donnez un nom : il devient le schéma PostgreSQL `osm_<nom>`.
5. L'import tourne en arrière-plan. À la fin, un aperçu (routes, bâtiments, lieux) se superpose à la carte.

Dans **QGIS** : Ajouter une couche PostGIS

| | |
|---|---|
| Hôte | `127.0.0.1` |
| Port | `5433` |
| Base | `osm` |
| Utilisateur / mot de passe | `osm` / `osm` |
| Schéma | `osm_<nom>` |

Tables typiques : `points`, `lines`, `multilinestrings`, `multipolygons`, `other_relations`. Les tags OSM non extraits en colonnes sont dans `other_tags` (JSON).

## Limites

- **Une seule importation à la fois.**
- Zone bornée à **3° de côté** : Overpass timeout au-delà, surtout en ville dense. Un département rural passe ; l'Île-de-France entière, non — resserrez.
- Il faut **Internet** le temps du téléchargement OSM. La carte de contours, elle, est locale.
- Le port **5433** évite d'écraser un PostgreSQL déjà sur 5432. Pour une autre base, éditez `.env`.

## Licence

GNU GPL v3 — voir `LICENSE`. Interface et carte dérivées de [pmtiles](https://github.com/F4EED/pmtiles). Données © contributeurs OpenStreetMap (ODbL).
