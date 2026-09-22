# Erreurs d’import OSM → PostGIS

Journal **automatique** : chaque échec d’import ajoute une entrée ici.

---

## 2026-09-21 10:33:51 — `osm_hatecorse` (échec)

| | |
|---|---|
| Job | `d162eaa2b778` |
| Nom | `hatecorse` |
| Schéma | `osm_hatecorse` |
| Emprise | `8.3798, 41.7897 → 9.9976, 43.0589` |

### Message

```
Échec inattendu : Transaction not established
```

PostgreSQL :

```
ERROR:  invalid input syntax for type json
DETAIL:  Character with value 0x0a must be escaped.
CONTEXT:  JSON data, line 1: {"description":"Mise en service 2007-08-06T14:51:32
COPY "osm_hatecorse"."points" (..., "other_tags") FROM STDIN;
```

---

## 2026-09-21 11:05:33 — `osm_hautecorse` (échec)

| | |
|---|---|
| Job | (non conservé) |
| Nom | `hautecorse` |
| Schéma | `osm_hautecorse` |
| Emprise | (Corse, même objet OSM `description`) |

### Message

```
ERROR:  invalid input syntax for type json
DETAIL:  Character with value 0x0a must be escaped.
COPY "osm_hautecorse"."points" (..., "other_tags") FROM STDIN;
```

---

## 2026-09-21 11:09:57 — `osm_test1` (échec)

| | |
|---|---|
| Job | `e04ca646aaf6` |
| Nom | `test1` |
| Schéma | `osm_test1` |
| Emprise | `9.3765, 42.5935 → 9.5128, 42.7175` |

### Message

```
Échec inattendu : Transaction not established
[…]
May be caused by: Transaction not established
```

### Journal d’import

```
Connexion à PostGIS…
Téléchargement OSM depuis https://overpass-api.de/api/interpreter…
Échec (HTTP Error 504: Gateway Timeout), essai du miroir suivant.
Téléchargement OSM depuis https://overpass.kumi.systems/api/interpreter…
Échec (HTTP Error 504: Gateway Timeout), essai du miroir suivant.
Téléchargement OSM depuis https://overpass.openstreetmap.fr/api/interpreter…
Fichier OSM : 51.8 Mo (9 sommets)
Création du schéma osm_test1…
Import dans PostGIS (GDAL/OGR)…
Import GDAL 0% … 100%
Erreur : Transaction not established
```

PostgreSQL (09:09:57 UTC) :

```
ERROR:  invalid input syntax for type json
CONTEXT:  JSON data, line 1: {"description":"Mise en service 2007-08-06T14:51:32
COPY "osm_test1"."points" (..., "other_tags") FROM STDIN;
```

GDAL : `Warning 1: Non closed ring detected.`

Cause : Overpass encode le retour à la ligne du tag `description` en `&#10;`. Le premier nettoyeur ne voyait que les octets bruts `< 32`, donc le JSON `other_tags` cassait encore le `COPY`.
