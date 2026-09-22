"""Import d'une emprise OpenStreetMap dans PostGIS.

Principe
--------
L'utilisateur clique un polygone sur la carte (3 sommets ou plus).
Ce module :

1. valide le nom et la zone ;
2. télécharge les objets OSM de l'emprise via l'API Overpass ;
3. les charge dans un **schéma PostGIS** dédié (``osm_<nom>``) avec GDAL/OGR,
   déjà présent sur la machine (le même outil que QGIS) ;
4. enregistre un résumé (tables, effectifs, emprise) que le navigateur
   interroge comme un job.

Une seule importation à la fois, volontairement : Overpass et GDAL saturent
le disque et le réseau. Les jobs vivent en mémoire ; les schémas PostGIS, eux,
restent jusqu'à suppression explicite.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import threading
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from osgeo import gdal, ogr, osr

from carroyage import dfci_features, utm_features

gdal.UseExceptions()
ogr.UseExceptions()

ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / "data" / "cache"
OSMCONF = ROOT / "osmconf.ini"
ERRORS_MD = ROOT / "erreurs.md"
QGIS_STYLES = ROOT / "qgis"
META_SCHEMA = "osm2postgis"
SCHEMA_PREFIX = "osm_"

DEFAULT_PG = {
    "PGHOST": "127.0.0.1",
    "PGPORT": "5432",
    "PGUSER": "gmc",
    "PGPASSWORD": "gmc",
    "PGDATABASE": "gmc",
}

OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
)

# Un département français tient largement en dessous de 3°. Au-delà, Overpass
# timeout souvent (Paris intra-muros est déjà très dense).
MAX_SPAN_DEG = 3.0
MIN_SPAN_DEG = 0.02
MIN_POINTS = 3
MAX_POINTS = 64
MAX_OSM_BYTES = 1_500_000_000
USER_AGENT = "OSM2Postgis/1.0 (import local PostGIS)"
# Tuiles GeoTIFF Mapzen / AWS Terrain (même famille que OpenTopoMap).
DEM_TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/geotiff/{z}/{x}/{y}.tif"
MAX_DEM_TILES = 64
SHOM_WFS = "https://services.data.shom.fr/INSPIRE/wfs"
GEORISQUES_WFS = "https://georisques.gouv.fr/services"
CATNAT_API = "https://georisques.gouv.fr/api/v1/gaspar/catnat"
GEO_API_COMMUNE = "https://geo.api.gouv.fr/communes/{code}"
GASPAR_ZIP = "http://files.georisques.fr/GASPAR/gaspar.zip"
DICRIM_URL = "https://www.georisques.gouv.fr/DICRIM/{code}"
BDHI_DATASET = "https://entrepot.recherche.data.gouv.fr/api/datasets/:persistentId/?persistentId=doi:10.57745/DKTV1G"
BDHI_FILE = "https://entrepot.recherche.data.gouv.fr/api/access/datafile/{fid}"
# Couches ouvertes utiles en bord de mer (Licence Ouverte / CC-BY-SA, pas pour la navigation).
SHOM_LAYERS = (
    ("shom_limite_terre_mer", "LIMTM_2154_WFS:limite_terre_mer_france_metropolitaine_ligne"),
    ("shom_epaves", "EPAVES_BDD_WFS:wrecks"),
    ("shom_obstructions", "EPAVES_BDD_WFS:obstrn"),
    ("shom_feux", "BALISAGE_BDD_WFS:lights"),
    ("shom_bouees", "BALISAGE_BDD_WFS:boylat"),
    ("shom_cables", "CABLES_BDD_WFS:cblsub_lv"),
    ("shom_3_milles", "LIMITES_PECHE_BDD_WFS:limite_3milles_peche_wgs84_epsg4326"),
    ("shom_natures_fond", "NDF_BDD_WLD_WGS84G_WFS:natures_fond_50000"),
)

RISK_LAYERS = (
    ("risk_cavites", "ms:CAVITE_LOCALISEE"),
    ("risk_mvt", "ms:MVT_LOCALISE"),
    ("risk_pprn_inondation", "ms:PPRN_PERIMETRE_INOND"),
    ("risk_pprn_submersion", "ms:PPRN_PERIMETRE_SUBMAR"),
    ("risk_argiles", "ms:ALEARG_REALISE"),
    ("risk_tri", "ms:LIMITETRI_FXX"),
    ("risk_zonage_sismique", "ms:risq_zonage_sismique"),
    ("risk_sis_historique", "ms:SIS_INTENSITE_EVTCOM"),
)

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
IMPORT_LOCK = threading.Lock()
GDAL_LOCK = threading.RLock()


class OsmImportError(ValueError):
    """Erreur attendue, message destiné à l'utilisateur (français, actionnable)."""


def load_env() -> None:
    """Charge ``.env`` s'il existe, sans écraser l'environnement déjà défini."""
    path = ROOT / ".env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key:
            os.environ.setdefault(key, value)


load_env()


def pg_settings() -> dict[str, str]:
    """Paramètres de connexion, avec les valeurs du docker-compose en repli."""
    return {key: os.environ.get(key, default) for key, default in DEFAULT_PG.items()}


def pg_dsn() -> str:
    cfg = pg_settings()
    return (
        f"PG:host={cfg['PGHOST']} port={cfg['PGPORT']} user={cfg['PGUSER']} "
        f"password={cfg['PGPASSWORD']} dbname={cfg['PGDATABASE']}"
    )


def pg_info() -> dict:
    """Vue publique de la connexion (sans mot de passe)."""
    cfg = pg_settings()
    return {
        "host": cfg["PGHOST"],
        "port": int(cfg["PGPORT"]),
        "user": cfg["PGUSER"],
        "database": cfg["PGDATABASE"],
        "qgis": (
            f"host={cfg['PGHOST']} port={cfg['PGPORT']} dbname={cfg['PGDATABASE']} "
            f"user={cfg['PGUSER']}"
        ),
    }


def sanitize_name(raw: str) -> str:
    """Transforme un nom saisi en identifiant PostgreSQL sûr (préfixe ``osm_``)."""
    text = (raw or "").strip()
    if text.lower().startswith(SCHEMA_PREFIX):
        text = text[len(SCHEMA_PREFIX) :]
    normalized = unicodedata.normalize("NFKD", text)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii").lower()
    cleaned = re.sub(r"[^a-z0-9_]+", "_", ascii_name).strip("_")
    if not cleaned or len(cleaned) > 48:
        raise OsmImportError("Nom invalide. Utilisez des lettres, chiffres, tirets (max 48).")
    if cleaned[0].isdigit():
        cleaned = "z" + cleaned
    return SCHEMA_PREFIX + cleaned


def ident(name: str) -> str:
    """Vérifie qu'un identifiant SQL est déjà assaini, puis le renvoie."""
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise OsmImportError("Identifiant interne invalide.")
    return name


def as_ring(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Conserve l'ordre des clics (contour), en retirant les doublons consécutifs."""
    if len(points) < MIN_POINTS:
        raise OsmImportError(
            f"Il faut au moins {MIN_POINTS} points pour former un polygone."
        )
    if len(points) > MAX_POINTS:
        raise OsmImportError(f"Trop de sommets (maximum {MAX_POINTS}).")
    cleaned: list[tuple[float, float]] = [points[0]]
    for lat, lon in points[1:]:
        prev_lat, prev_lon = cleaned[-1]
        if abs(lat - prev_lat) > 1e-9 or abs(lon - prev_lon) > 1e-9:
            cleaned.append((lat, lon))
    if len(cleaned) < MIN_POINTS:
        raise OsmImportError("Les points sont trop proches. Écartez-les autour de la zone.")
    area = 0.0
    n = len(cleaned)
    for i, (lat, lon) in enumerate(cleaned):
        lat2, lon2 = cleaned[(i + 1) % n]
        area += lon * lat2 - lon2 * lat
    if abs(area) < 1e-12:
        raise OsmImportError(
            "Le polygone est dégénéré. Cliquez autour de la zone, dans l'ordre."
        )
    return cleaned


def bbox_of(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    """Rectangle englobant ``(ouest, sud, est, nord)`` avec garde-fous de taille."""
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    south, north = min(lats), max(lats)
    west, east = min(lons), max(lons)
    if east - west > MAX_SPAN_DEG or north - south > MAX_SPAN_DEG:
        raise OsmImportError(
            f"Zone trop large (max {MAX_SPAN_DEG:g}° de côté). "
            "Zoomez et resserrez les points : Overpass refuse les très grandes emprises."
        )
    if east - west < MIN_SPAN_DEG or north - south < MIN_SPAN_DEG:
        raise OsmImportError("Zone trop petite. Écartez davantage les points.")
    return west, south, east, north


def _append_log(job: dict, line: str) -> None:
    job["log"] = (job.get("log") or "") + line
    if len(job["log"]) > 8000:
        job["log"] = job["log"][-6000:]


def _append_erreurs_md(job: dict, message: str) -> None:
    """Ajoute l'échec courant à ``erreurs.md`` (créé s'il n'existe pas)."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    bbox = job.get("bbox") or {}
    emprise = ""
    if bbox:
        emprise = (
            f"{bbox.get('west')}, {bbox.get('south')} → "
            f"{bbox.get('east')}, {bbox.get('north')}"
        )
    log = (job.get("log") or "").strip() or "(journal vide)"
    if len(log) > 4000:
        log = "…\n" + log[-4000:]
    err = (message or "").strip() or "(sans message)"
    if len(err) > 4000:
        err = err[:4000] + "…"
    header = (
        "# Erreurs d’import OSM → PostGIS\n\n"
        "Journal **automatique** : chaque échec d’import ajoute une entrée ici.\n"
    )
    block = (
        f"\n---\n\n"
        f"## {now} — `{job.get('schema') or '?'}` (échec)\n\n"
        f"| | |\n"
        f"|---|---|\n"
        f"| Job | `{job.get('id') or ''}` |\n"
        f"| Nom | `{job.get('name') or ''}` |\n"
        f"| Schéma | `{job.get('schema') or ''}` |\n"
        f"| Emprise | `{emprise}` |\n"
        f"\n### Message\n\n```\n{err}\n```\n\n"
        f"### Journal d’import\n\n```\n{log}\n```\n"
    )
    try:
        if not ERRORS_MD.is_file() or ERRORS_MD.stat().st_size == 0:
            ERRORS_MD.write_text(header + block, encoding="utf-8")
        else:
            with ERRORS_MD.open("a", encoding="utf-8") as fh:
                fh.write(block)
    except OSError:
        pass


def _ring_wkt(ring: list[tuple[float, float]]) -> str:
    """WKT POLYGON en lon/lat, anneau fermé."""
    pairs = [f"{lon:.7f} {lat:.7f}" for lat, lon in ring]
    pairs.append(pairs[0])
    return "POLYGON((" + ", ".join(pairs) + "))"


def _overpass_poly(ring: list[tuple[float, float]]) -> str:
    """Filtre Overpass ``poly`` : suites latitude longitude."""
    return " ".join(f"{lat:.7f} {lon:.7f}" for lat, lon in ring)


def _overpass_ql(ring: list[tuple[float, float]]) -> str:
    """Requête Overpass bornée au polygone, sans les relations administratives nationales.

    Les relations ``boundary`` (pays, régions) intersectent souvent un simple
    département et feraient télécharger des millions de nœuds hors zone.
    On se limite aux nœuds, chemins, et relations de type multipolygone
    (bâtiments, occupations du sol).
    """
    poly = _overpass_poly(ring)
    return (
        "[out:xml][timeout:180][maxsize:1073741824];\n"
        "(\n"
        f'  node(poly:"{poly}");\n'
        f'  way(poly:"{poly}");\n'
        f'  relation["type"="multipolygon"](poly:"{poly}");\n'
        ");\n"
        "(._;>;);\n"
        "out meta;\n"
    )


# Dans un attribut XML, tout octet < 32 (NL, CR, TAB, NUL…) devient un espace.
# Overpass encode souvent le saut de ligne en entité ``&#10;`` : GDAL la
# décode ensuite, et PostgreSQL refuse le JSON other_tags.
_CTRL_TO_SPACE = bytes(32 if byte < 32 else byte for byte in range(256))
_XML_NUM_ENTITY = re.compile(br"&#(x[0-9a-fA-F]+|\d+);", re.IGNORECASE)


def _replace_xml_ctrl_entities(segment: bytes) -> tuple[bytes, int]:
    """Remplace les entités numériques XML de contrôle (``&#10;``, ``&#x0a;``…)."""
    replaced = 0
    parts: list[bytes] = []
    last = 0
    for match in _XML_NUM_ENTITY.finditer(segment):
        body = match.group(1)
        try:
            code = int(body[1:], 16) if body[:1] in b"xX" else int(body, 10)
        except ValueError:
            continue
        if 0 <= code < 32:
            parts.append(segment[last : match.start()])
            parts.append(b" ")
            last = match.end()
            replaced += 1
    if not replaced:
        return segment, 0
    parts.append(segment[last:])
    return b"".join(parts), replaced


def _sanitize_osm_xml(path: Path) -> int:
    """Nettoie les caractères de contrôle dans les attributs OSM (``k=`` / ``v=``).

    GDAL assemble ``other_tags`` en JSON ; PostgreSQL refuse un JSON avec un
    retour à la ligne brut (``0x0a must be escaped``), ce qui abortait le COPY
    puis produisait « Transaction not established ».
    """
    tmp = path.with_name(path.name + ".clean")
    replaced = 0
    in_quote = False
    with path.open("rb") as src, tmp.open("wb") as dst:
        while True:
            chunk = src.read(1024 * 1024)
            if not chunk:
                break
            start = 0
            length = len(chunk)
            while start < length:
                if not in_quote:
                    quote = chunk.find(b'"', start)
                    if quote < 0:
                        dst.write(chunk[start:])
                        break
                    dst.write(chunk[start : quote + 1])
                    in_quote = True
                    start = quote + 1
                else:
                    quote = chunk.find(b'"', start)
                    end = quote if quote >= 0 else length
                    segment = chunk[start:end]
                    segment, n_ent = _replace_xml_ctrl_entities(segment)
                    replaced += n_ent
                    n_ctrl = sum(byte < 32 for byte in segment)
                    if n_ctrl:
                        replaced += n_ctrl
                        segment = segment.translate(_CTRL_TO_SPACE)
                    dst.write(segment)
                    if quote < 0:
                        break
                    dst.write(b'"')
                    in_quote = False
                    start = quote + 1
    if replaced:
        tmp.replace(path)
    else:
        tmp.unlink(missing_ok=True)
    return replaced


def _download_overpass(job: dict, dest: Path, ring: list[tuple[float, float]]) -> None:
    query = _overpass_ql(ring)
    body = urllib.parse.urlencode({"data": query}).encode("utf-8")
    last_error = "Overpass injoignable."
    for url in OVERPASS_ENDPOINTS:
        _append_log(job, f"Téléchargement OSM depuis {url}…\n")
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=240) as resp:
                dest.parent.mkdir(parents=True, exist_ok=True)
                written = 0
                with dest.open("wb") as out:
                    while True:
                        chunk = resp.read(64 * 1024)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > MAX_OSM_BYTES:
                            raise OsmImportError(
                                "Fichier OSM trop volumineux. Resserrez la zone."
                            )
                        out.write(chunk)
                if written < 200:
                    last_error = "Réponse Overpass vide."
                    dest.unlink(missing_ok=True)
                    continue
                head = dest.read_bytes()[:800].decode("utf-8", errors="replace")
                if "<remark>" in head and "error" in head.lower():
                    last_error = "Overpass a renvoyé une erreur (zone trop dense ou timeout)."
                    dest.unlink(missing_ok=True)
                    continue
                if "<osm" not in head and "<?xml" not in head:
                    last_error = "Réponse Overpass inattendue (pas de XML OSM)."
                    dest.unlink(missing_ok=True)
                    continue
                _append_log(job, f"Fichier OSM : {written / 1048576:.1f} Mo ({len(ring)} sommets)\n")
                return
        except OsmImportError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = str(exc)
            dest.unlink(missing_ok=True)
            _append_log(job, f"Échec ({last_error}), essai du miroir suivant.\n")
            continue
    raise OsmImportError(
        f"Téléchargement OSM impossible ({last_error}). "
        "Vérifiez Internet, ou resserrez la zone."
    )


def _pg_open(update: bool = False):
    flags = gdal.OF_VECTOR
    if update:
        flags |= gdal.OF_UPDATE
    try:
        ds = gdal.OpenEx(pg_dsn(), flags)
    except RuntimeError as exc:
        raise OsmImportError(
            "Base PostGIS injoignable. Vérifiez que le conteneur postgresql-db-1 "
            "tourne sur le port 5432 (base gmc), puis réessayez."
        ) from exc
    if ds is None:
        raise OsmImportError(
            "Base PostGIS injoignable. Vérifiez que le conteneur postgresql-db-1 "
            "tourne sur le port 5432 (base gmc), puis réessayez."
        )
    return ds


def _pg_exec(sql: str) -> None:
    ds = _pg_open(update=True)
    try:
        result = ds.ExecuteSQL(sql, dialect="POSTGRESQL")
        if result is not None:
            ds.ReleaseResultSet(result)
    finally:
        ds = None


def _pg_query(sql: str) -> list[tuple]:
    ds = _pg_open()
    try:
        layer = ds.ExecuteSQL(sql, dialect="POSTGRESQL")
        if layer is None:
            return []
        rows = []
        try:
            layer.ResetReading()
            feat = layer.GetNextFeature()
            while feat:
                rows.append(tuple(feat.GetField(i) for i in range(feat.GetFieldCount())))
                feat = layer.GetNextFeature()
        finally:
            ds.ReleaseResultSet(layer)
        return rows
    finally:
        ds = None


def ensure_meta() -> None:
    """Crée le schéma de suivi des imports s'il n'existe pas encore."""
    with GDAL_LOCK:
        _pg_exec("CREATE EXTENSION IF NOT EXISTS postgis")
        _pg_exec(f"CREATE SCHEMA IF NOT EXISTS {META_SCHEMA}")
        _pg_exec(
            f"""
            CREATE TABLE IF NOT EXISTS {META_SCHEMA}.imports (
                name text PRIMARY KEY,
                schema_name text NOT NULL,
                west double precision,
                south double precision,
                east double precision,
                north double precision,
                created_at timestamptz DEFAULT now(),
                source text,
                bytes bigint,
                counts jsonb
            )
            """
        )


_QGIS_LAYER_STYLES = {
    "points": ("Point", "points"),
    "lines": ("Line", "lines"),
    "multilinestrings": ("Line", "multilinestrings"),
    "multipolygons": ("Polygon", "multipolygons"),
    "other_relations": ("Unknown Geometry", "other_relations"),
    "contours": ("Line", "contours"),
    "shom_limite_terre_mer": ("Line", "lines"),
    "shom_epaves": ("Point", "points"),
    "shom_obstructions": ("Point", "points"),
    "shom_feux": ("Point", "points"),
    "shom_bouees": ("Point", "points"),
    "shom_cables": ("Line", "multilinestrings"),
    "shom_3_milles": ("Line", "lines"),
    "shom_natures_fond": ("Polygon", "multipolygons"),
    "risk_cavites": ("Point", "points"),
    "risk_mvt": ("Point", "points"),
    "risk_pprn_inondation": ("Polygon", "multipolygons"),
    "risk_pprn_submersion": ("Polygon", "multipolygons"),
    "risk_argiles": ("Polygon", "multipolygons"),
    "risk_tri": ("Polygon", "multipolygons"),
    "risk_zonage_sismique": ("Polygon", "multipolygons"),
    "risk_sis_historique": ("Polygon", "multipolygons"),
    "risk_catnat": ("Point", "points"),
    "risk_dicrim": ("Point", "risk_dicrim"),
    "risk_bdhi": ("Point", "risk_dicrim"),
    "grid_dfci": ("Polygon", "grid_dfci"),
    "grid_utm": ("Polygon", "grid_utm"),
    "officiels": ("Point", "officiels"),
}


def _ensure_layer_styles_table() -> None:
    """Table QGIS ``public.layer_styles`` (styles par défaut au chargement)."""
    _pg_exec(
        """
        CREATE TABLE IF NOT EXISTS public.layer_styles (
            id SERIAL PRIMARY KEY,
            f_table_catalog varchar,
            f_table_schema varchar,
            f_table_name varchar,
            f_geometry_column varchar,
            stylename text,
            styleqml xml,
            stylesld xml,
            useasdefault boolean,
            description text,
            owner varchar(63) DEFAULT CURRENT_USER,
            ui xml,
            update_time timestamp DEFAULT CURRENT_TIMESTAMP,
            type varchar,
            r_raster_column varchar
        )
        """
    )


def _qml_document(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"^<!DOCTYPE[^>]*>\s*", "", text, count=1, flags=re.IGNORECASE)
    return text.strip()


def _save_qgis_styles(schema: str) -> int:
    """Enregistre le style OpenTopoMap dans ``layer_styles`` pour le schéma importé."""
    ident(schema)
    _ensure_layer_styles_table()
    catalog = pg_settings()["PGDATABASE"]
    tables = {
        str(name)
        for (name,) in _pg_query(
            f"""
            SELECT tablename FROM pg_tables
            WHERE schemaname = '{schema}'
            """
        )
    }
    saved = 0
    for table, spec in _QGIS_LAYER_STYLES.items():
        if table not in tables:
            continue
        geom_type, qml_stem = spec
        qml_path = QGIS_STYLES / f"{qml_stem}.qml"
        if not qml_path.is_file():
            continue
        qml = _qml_document(qml_path)
        tag = f"qml_{table}"
        if f"${tag}$" in qml:
            continue
        _pg_exec(
            f"""
            DELETE FROM public.layer_styles
            WHERE f_table_catalog = '{catalog}'
              AND f_table_schema = '{schema}'
              AND f_table_name = '{table}'
              AND stylename = 'opentopo'
            """
        )
        _pg_exec(
            f"""
            UPDATE public.layer_styles
            SET useasdefault = false
            WHERE f_table_catalog = '{catalog}'
              AND f_table_schema = '{schema}'
              AND f_table_name = '{table}'
            """
        )
        _pg_exec(
            f"""
            INSERT INTO public.layer_styles (
                f_table_catalog, f_table_schema, f_table_name, f_geometry_column,
                stylename, styleqml, useasdefault, description, owner, type
            ) VALUES (
                '{catalog}',
                '{schema}',
                '{table}',
                'geom',
                'opentopo',
                XMLPARSE(DOCUMENT ${tag}${qml}${tag}$),
                true,
                'OSM2Postgis — rendu type OpenTopoMap',
                CURRENT_USER,
                '{geom_type}'
            )
            """
        )
        saved += 1
    return saved


def postgis_status() -> dict:
    """État de la base, pour le bandeau du panneau latéral."""
    info = pg_info()
    try:
        ensure_meta()
        with GDAL_LOCK:
            rows = _pg_query("SELECT CAST(postgis_full_version() AS varchar) AS v")
        version = rows[0][0] if rows else "PostGIS"
        return {"ok": True, "error": None, "postgis": version, **info}
    except OsmImportError as exc:
        return {"ok": False, "error": str(exc), "postgis": None, **info}
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": f"Base PostGIS injoignable ({exc}). Vérifiez postgresql-db-1 sur le port 5432.",
            "postgis": None,
            **info,
        }


def _layer_counts(schema: str) -> dict[str, int]:
    ident(schema)
    tables = _pg_query(
        f"""
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = '{schema}'
        ORDER BY tablename
        """
    )
    counts: dict[str, int] = {}
    for (table,) in tables:
        if not re.fullmatch(r"[a-z0-9_]+", str(table)):
            continue
        rows = _pg_query(f"SELECT COUNT(*) AS n FROM {schema}.{table}")
        counts[str(table)] = int(rows[0][0]) if rows else 0
    return counts


def _extra_indexes(schema: str) -> None:
    ident(schema)
    statements = [
        f'CREATE INDEX IF NOT EXISTS {schema}_lines_highway_idx ON {schema}.lines (highway)',
        f'CREATE INDEX IF NOT EXISTS {schema}_poly_building_idx ON {schema}.multipolygons (building)',
        f'CREATE INDEX IF NOT EXISTS {schema}_poly_landuse_idx ON {schema}.multipolygons (landuse)',
        f'CREATE INDEX IF NOT EXISTS {schema}_points_amenity_idx ON {schema}.points (amenity)',
        f'CREATE INDEX IF NOT EXISTS {schema}_points_place_idx ON {schema}.points (place)',
        f'CREATE INDEX IF NOT EXISTS {schema}_contours_elev_idx ON {schema}.contours (elev)',
        f'CREATE INDEX IF NOT EXISTS {schema}_officiels_cat_idx ON {schema}.officiels (categorie)',
        f'CREATE INDEX IF NOT EXISTS {schema}_dfci_code_idx ON {schema}.grid_dfci (code)',
        f'CREATE INDEX IF NOT EXISTS {schema}_utm_code_idx ON {schema}.grid_utm (code)',
    ]
    for sql in statements:
        try:
            _pg_exec(sql)
        except RuntimeError:
            # La table correspondante n'existe pas (couche OSM vide) : normal.
            continue


def _clip_to_ring(schema: str, ring: list[tuple[float, float]]) -> None:
    """Retire les objets hors du polygone (complétés par Overpass hors emprise)."""
    ident(schema)
    wkt = _ring_wkt(ring)
    poly = f"ST_MakeValid(ST_SetSRID(ST_GeomFromText('{wkt}'), 4326))"
    tables = _pg_query(
        f"""
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = '{schema}'
        """
    )
    for (table,) in tables:
        if not re.fullmatch(r"[a-z0-9_]+", str(table)):
            continue
        # Centroïdes de commune : hors polygone même si la commune recouvre la zone.
        if str(table) in {"risk_catnat", "risk_dicrim", "risk_bdhi"}:
            continue
        try:
            _pg_exec(
                f"""
                DELETE FROM {schema}.{table}
                WHERE geom IS NULL
                   OR NOT ST_Intersects(ST_MakeValid(geom), {poly})
                """
            )
        except RuntimeError:
            continue


def _lonlat_to_tile(lon: float, lat: float, zoom: int) -> tuple[int, int]:
    n = 1 << zoom
    lat = max(-85.05112878, min(85.05112878, lat))
    x = int((lon + 180.0) / 360.0 * n)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return max(0, min(n - 1, x)), max(0, min(n - 1, y))


def _dem_zoom_and_interval(west: float, south: float, east: float, north: float) -> tuple[int, float]:
    span = max(east - west, north - south)
    if span <= 0.25:
        return 13, 10.0
    if span <= 0.7:
        return 12, 10.0
    if span <= 1.5:
        return 11, 20.0
    return 10, 20.0


def _dem_tile_list(
    west: float, south: float, east: float, north: float
) -> tuple[int, float, list[tuple[int, int]]]:
    zoom, interval = _dem_zoom_and_interval(west, south, east, north)
    while zoom >= 8:
        x0, y_n = _lonlat_to_tile(west, north, zoom)
        x1, y_s = _lonlat_to_tile(east, south, zoom)
        xs = range(min(x0, x1), max(x0, x1) + 1)
        ys = range(min(y_n, y_s), max(y_n, y_s) + 1)
        tiles = [(x, y) for x in xs for y in ys]
        if len(tiles) <= MAX_DEM_TILES:
            return zoom, interval, tiles
        zoom -= 1
        interval = 20.0 if zoom <= 11 else interval
    x0, y_n = _lonlat_to_tile(west, north, 8)
    x1, y_s = _lonlat_to_tile(east, south, 8)
    tiles = [
        (x, y)
        for x in range(min(x0, x1), max(x0, x1) + 1)
        for y in range(min(y_n, y_s), max(y_n, y_s) + 1)
    ]
    return 8, 50.0, tiles[:MAX_DEM_TILES]


def _import_contours(
    job: dict,
    schema: str,
    west: float,
    south: float,
    east: float,
    north: float,
) -> None:
    """Courbes de niveau depuis les tuiles MNT Mapzen/SRTM (comme OpenTopoMap)."""
    ident(schema)
    pad = max((east - west), (north - south)) * 0.02
    west, south = west - pad, south - pad
    east, north = east + pad, north + pad
    zoom, interval, tiles = _dem_tile_list(west, south, east, north)
    if not tiles:
        raise OsmImportError("Aucune tuile MNT pour cette emprise.")
    _append_log(
        job,
        f"MNT SRTM (z{zoom}, {len(tiles)} tuiles) → courbes tous les {interval:g} m…\n",
    )
    gdal.SetConfigOption("GDAL_HTTP_USERAGENT", USER_AGENT)
    gdal.SetConfigOption("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
    gdal.SetConfigOption("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")
    gdal.SetConfigOption("CPL_VSIL_CURL_USE_HEAD", "NO")
    gdal.SetConfigOption("GDAL_HTTP_TIMEOUT", "45")
    sources = [
        f"/vsicurl/{DEM_TILE_URL.format(z=zoom, x=x, y=y)}" for x, y in tiles
    ]
    job_id = job["id"]
    vrt_path = CACHE_DIR / f".dem-{job_id}.vrt"
    dem_path = CACHE_DIR / f".dem-{job_id}.tif"
    gpkg_path = CACHE_DIR / f".contours-{job_id}.gpkg"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        vrt = gdal.BuildVRT(str(vrt_path), sources)
        if vrt is None:
            raise OsmImportError("Mosaïque MNT impossible.")
        vrt = None
        warped = gdal.Warp(
            str(dem_path),
            str(vrt_path),
            options=gdal.WarpOptions(
                format="GTiff",
                dstSRS="EPSG:3857",
                outputBounds=[west, south, east, north],
                outputBoundsSRS="EPSG:4326",
                srcNodata=-32768,
                dstNodata=-32768,
                resampleAlg="bilinear",
                creationOptions=["COMPRESS=LZW", "TILED=YES"],
            ),
        )
        if warped is None:
            raise OsmImportError("Reprojection du MNT impossible.")
        warped = None
        dem = gdal.Open(str(dem_path))
        if dem is None:
            raise OsmImportError("MNT introuvable après téléchargement.")
        band = dem.GetRasterBand(1)
        gpkg_path.unlink(missing_ok=True)
        drv = ogr.GetDriverByName("GPKG")
        cds = drv.CreateDataSource(str(gpkg_path))
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(3857)
        srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        layer = cds.CreateLayer("contours", srs, ogr.wkbLineString)
        layer.CreateField(ogr.FieldDefn("elev", ogr.OFTReal))
        elev_idx = layer.GetLayerDefn().GetFieldIndex("elev")
        gdal.ContourGenerate(
            band,
            interval,
            0.0,
            [],
            1,
            -32768.0,
            layer,
            -1,
            elev_idx,
        )
        n_feat = layer.GetFeatureCount()
        cds = None
        dem = None
        if not n_feat:
            _append_log(job, "Aucune courbe de niveau (relief trop plat ou MNT vide).\n")
            return
        options = gdal.VectorTranslateOptions(
            format="PostgreSQL",
            accessMode="overwrite",
            dstSRS="EPSG:4326",
            layerCreationOptions=[
                f"SCHEMA={schema}",
                "GEOMETRY_NAME=geom",
                "SPATIAL_INDEX=GIST",
                "FID=ogc_fid",
                "PRECISION=NO",
            ],
            layerName="contours",
            geometryType="PROMOTE_TO_MULTI",
        )
        result = gdal.VectorTranslate(pg_dsn(), str(gpkg_path), options=options)
        if result is None:
            raise OsmImportError("L'écriture des courbes dans PostGIS a échoué.")
        result = None
        _append_log(job, f"Courbes de niveau : {n_feat} lignes (table {schema}.contours).\n")
    finally:
        vrt_path.unlink(missing_ok=True)
        dem_path.unlink(missing_ok=True)
        gpkg_path.unlink(missing_ok=True)
        Path(str(gpkg_path) + "-wal").unlink(missing_ok=True)
        Path(str(gpkg_path) + "-shm").unlink(missing_ok=True)


def _import_wfs_layers(
    job: dict,
    schema: str,
    west: float,
    south: float,
    east: float,
    north: float,
    src: str,
    layers: tuple[tuple[str, str], ...],
    title: str,
    empty_hint: str,
) -> None:
    ident(schema)
    gdal.SetConfigOption("GDAL_HTTP_USERAGENT", USER_AGENT)
    gdal.SetConfigOption("OGR_WFS_USE_STREAMING", "NO")
    _append_log(job, f"{title}…\n")
    loaded: list[str] = []
    for table, layer in layers:
        try:
            options = gdal.VectorTranslateOptions(
                format="PostgreSQL",
                accessMode="overwrite",
                layers=[layer],
                layerName=table,
                spatFilter=(west, south, east, north),
                spatSRS="EPSG:4326",
                dstSRS="EPSG:4326",
                skipFailures=True,
                layerCreationOptions=[
                    f"SCHEMA={schema}",
                    "GEOMETRY_NAME=geom",
                    "SPATIAL_INDEX=GIST",
                    "FID=ogc_fid",
                    "PRECISION=NO",
                ],
                geometryType="PROMOTE_TO_MULTI",
            )
            result = gdal.VectorTranslate(pg_dsn(), src, options=options)
            result = None
            rows = _pg_query(f"SELECT COUNT(*) AS n FROM {schema}.{table}")
            n = int(rows[0][0]) if rows else 0
            if n == 0:
                try:
                    _pg_exec(f"DROP TABLE IF EXISTS {schema}.{table} CASCADE")
                except RuntimeError:
                    pass
                continue
            loaded.append(f"{table}={n}")
        except Exception as exc:  # noqa: BLE001
            _append_log(job, f"  {table} : {exc}\n")
            try:
                _pg_exec(f"DROP TABLE IF EXISTS {schema}.{table} CASCADE")
            except RuntimeError:
                pass
    if loaded:
        _append_log(job, title.split("(")[0].strip() + " : " + ", ".join(loaded) + "\n")
    else:
        _append_log(job, empty_hint + "\n")


def _coords_to_latlon(centre: object) -> tuple[float, float] | None:
    coords = (centre or {}).get("coordinates") if isinstance(centre, dict) else None
    if not (isinstance(coords, list) and len(coords) >= 2):
        return None
    return float(coords[1]), float(coords[0])


def _commune_center(code_insee: str) -> tuple[float, float] | None:
    url = GEO_API_COMMUNE.format(code=urllib.parse.quote(code_insee)) + "?fields=centre"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None
    return _coords_to_latlon((data or {}).get("centre"))


def _insee_dept(code: str) -> str:
    if len(code) >= 3 and code[:2] in {"97", "98"}:
        return code[:3]
    return code[:2]


def _commune_centers(codes: set[str]) -> dict[str, tuple[float, float]]:
    """Chef-lieux via geo.api.gouv.fr, groupés par département."""
    out: dict[str, tuple[float, float]] = {}
    depts = {_insee_dept(code) for code in codes if len(code) >= 2}
    for dept in depts:
        url = (
            f"https://geo.api.gouv.fr/departements/{urllib.parse.quote(dept)}"
            "/communes?fields=centre,code"
        )
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                rows = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            continue
        if not isinstance(rows, list):
            continue
        for item in rows:
            code = str((item or {}).get("code") or "")
            xy = _coords_to_latlon((item or {}).get("centre"))
            if code and xy:
                out[code] = xy
    for code in codes:
        if code in out:
            continue
        xy = _commune_center(code)
        if xy:
            out[code] = xy
    return out


def _import_catnat(
    job: dict,
    schema: str,
    west: float,
    south: float,
    east: float,
    north: float,
) -> None:
    """Arrêtés CATNAT (mémoire des crises) géocodés au chef-lieu de commune."""
    ident(schema)
    lat0 = (south + north) / 2
    lon0 = (west + east) / 2
    span = max(east - west, north - south)
    rayon = int(min(80_000, max(4_000, span * 111_000 * 0.65)))
    events: list[dict] = []
    page = 1
    while page <= 25:
        q = urllib.parse.urlencode(
            {
                "latlon": f"{lon0},{lat0}",
                "rayon": rayon,
                "page": page,
                "page_size": 100,
            }
        )
        req = urllib.request.Request(
            f"{CATNAT_API}?{q}", headers={"User-Agent": USER_AGENT}
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            _append_log(job, f"CATNAT : {exc}\n")
            return
        events.extend(payload.get("data") or [])
        total_pages = int(payload.get("total_pages") or 1)
        if page >= total_pages:
            break
        page += 1
    insee_codes = {str(ev.get("code_insee") or "") for ev in events}
    insee_codes.discard("")
    centers = _commune_centers(insee_codes)
    features = []
    for ev in events:
        code = str(ev.get("code_insee") or "")
        xy = centers.get(code)
        if not xy:
            continue
        lat, lon = xy
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "code_catnat": ev.get("code_national_catnat"),
                    "debut": ev.get("date_debut_evt"),
                    "fin": ev.get("date_fin_evt"),
                    "arrete": ev.get("date_publication_arrete"),
                    "risque": ev.get("libelle_risque_jo"),
                    "insee": code,
                    "commune": ev.get("libelle_commune"),
                },
            }
        )
    if not features:
        _append_log(job, "Aucun arrêté CATNAT dans l’emprise.\n")
        return
    geojson_path = CACHE_DIR / f".catnat-{job['id']}.geojson"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    geojson_path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False),
        encoding="utf-8",
    )
    try:
        options = gdal.VectorTranslateOptions(
            format="PostgreSQL",
            accessMode="overwrite",
            layerName="risk_catnat",
            dstSRS="EPSG:4326",
            layerCreationOptions=[
                f"SCHEMA={schema}",
                "GEOMETRY_NAME=geom",
                "SPATIAL_INDEX=GIST",
                "FID=ogc_fid",
                "PRECISION=NO",
            ],
        )
        result = gdal.VectorTranslate(pg_dsn(), str(geojson_path), options=options)
        result = None
        n_communes = len({feat["properties"]["insee"] for feat in features})
        _append_log(
            job,
            f"CATNAT : {len(features)} arrêtés ({n_communes} communes) → {schema}.risk_catnat.\n",
        )
    finally:
        geojson_path.unlink(missing_ok=True)


def _import_geojson_features(
    job: dict,
    schema: str,
    table: str,
    features: list[dict],
    *,
    promote: bool = True,
) -> int:
    if not features:
        return 0
    ident(schema)
    path = CACHE_DIR / f".{table}-{job['id']}.geojson"
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False),
        encoding="utf-8",
    )
    try:
        opts: dict = {
            "format": "PostgreSQL",
            "accessMode": "overwrite",
            "layerName": table,
            "dstSRS": "EPSG:4326",
            "layerCreationOptions": [
                f"SCHEMA={schema}",
                "GEOMETRY_NAME=geom",
                "SPATIAL_INDEX=GIST",
                "FID=ogc_fid",
                "PRECISION=NO",
            ],
        }
        if promote:
            opts["geometryType"] = "PROMOTE_TO_MULTI"
        result = gdal.VectorTranslate(
            pg_dsn(), str(path), options=gdal.VectorTranslateOptions(**opts)
        )
        result = None
        return len(features)
    finally:
        path.unlink(missing_ok=True)


def _dicrim_dates() -> dict[str, str]:
    """Index INSEE → date de publication (export GASPAR, mis en cache 24 h)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / "dicrim_gaspar.csv"
    stale = (not cache.is_file()) or (
        datetime.now().timestamp() - cache.stat().st_mtime > 86_400
    )
    if stale:
        req = urllib.request.Request(GASPAR_ZIP, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=90) as resp:
            blob = resp.read()
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            name = next(n for n in zf.namelist() if "dicrim" in n.lower())
            cache.write_bytes(zf.read(name))
    out: dict[str, str] = {}
    with cache.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle, delimiter=";"):
            code = (row.get("cod_commune") or "").strip()
            if not code:
                continue
            date = (row.get("dat_publi_dicrim") or "")[:10]
            out[code] = date
    return out


def _import_dicrim(
    job: dict,
    schema: str,
    west: float,
    south: float,
    east: float,
    north: float,
) -> None:
    """Fiches DICRIM des communes de l'emprise (lien Géorisques)."""
    ident(schema)
    index = _dicrim_dates()
    q = urllib.parse.urlencode(
        {
            "bbox": f"{west},{south},{east},{north}",
            "fields": "nom,code,centre",
        }
    )
    req = urllib.request.Request(
        f"https://geo.api.gouv.fr/communes?{q}",
        headers={"User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=40) as resp:
        communes = json.loads(resp.read().decode("utf-8"))
    features = []
    for item in communes or []:
        code = str((item or {}).get("code") or "")
        if code not in index:
            continue
        xy = _coords_to_latlon((item or {}).get("centre"))
        if not xy:
            continue
        lat, lon = xy
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "insee": code,
                    "commune": (item or {}).get("nom"),
                    "annee": index[code][:4] if index[code] else None,
                    "date_publi": index[code] or None,
                    "url": DICRIM_URL.format(code=code),
                },
            }
        )
    if not features:
        _append_log(job, "Aucun DICRIM déclaré pour les communes de l’emprise.\n")
        return
    n = _import_geojson_features(job, schema, "risk_dicrim", features, promote=False)
    _append_log(job, f"DICRIM : {n} communes → {schema}.risk_dicrim (lien Géorisques).\n")


def _fold_txt(value: str) -> str:
    nfkd = unicodedata.normalize("NFKD", value or "")
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch)).casefold()


def _bdhi_type(titre: str) -> str:
    t = _fold_txt(titre)
    if any(k in t for k in ("submersion", "houle", "maree", "xynthia")):
        return "submersion"
    if any(k in t for k in ("cyclone", "ouragan", "depression tropicale", "tempete tropicale")):
        return "cyclone"
    if "ruissellement" in t:
        return "ruissellement"
    if "nappe" in t:
        return "nappe"
    if any(k in t for k in ("rupture", "barrage", "poche")):
        return "rupture"
    if "crue" in t or "inondation" in t:
        return "crue"
    return "autre"


def _bdhi_catalog() -> list[dict]:
    """222 fiches de synthèse INRAE / Recherche Data Gouv (cache 30 j)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / "bdhi_syntheses.json"
    stale = (not cache.is_file()) or (
        datetime.now().timestamp() - cache.stat().st_mtime > 30 * 86_400
    )
    if not stale:
        return json.loads(cache.read_text(encoding="utf-8"))
    req = urllib.request.Request(BDHI_DATASET, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    files = (payload.get("data") or {}).get("latestVersion", {}).get("files") or []
    pdf_ids: dict[str, int] = {}
    liste_id = None
    for item in files:
        label = str(item.get("label") or "")
        fid = (item.get("dataFile") or {}).get("id")
        if not fid:
            continue
        if label.lower().startswith("listefiches"):
            liste_id = fid
        elif label.lower().endswith(".pdf"):
            pdf_ids[label] = int(fid)
    if not liste_id:
        raise OsmImportError("Catalogue BDHI introuvable (Recherche Data Gouv).")
    req = urllib.request.Request(
        BDHI_FILE.format(fid=liste_id), headers={"User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        text = resp.read().decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(text), delimiter="\t"))
    events = []
    for row in rows:
        nom = (row.get("nom_fichier") or "").strip().strip('"')
        fid = pdf_ids.get(nom)
        events.append(
            {
                "numero": (row.get("numero") or "").strip(),
                "fichier": nom,
                "annee_debut": (row.get("annee_debut") or "").strip() or None,
                "annee_fin": (row.get("annee_fin") or "").strip() or None,
                "titre": (row.get("titre") or "").strip().strip('"'),
                "url": BDHI_FILE.format(fid=fid) if fid else "https://doi.org/10.57745/DKTV1G",
            }
        )
    cache.write_text(json.dumps(events, ensure_ascii=False), encoding="utf-8")
    return events


def _import_bdhi(
    job: dict,
    schema: str,
    west: float,
    south: float,
    east: float,
    north: float,
) -> None:
    """Événements historiques d'inondation (fiches de synthèse BDHI) proches de l'emprise."""
    ident(schema)
    catalog = _bdhi_catalog()
    q = urllib.parse.urlencode(
        {
            "bbox": f"{west},{south},{east},{north}",
            "fields": "nom,code,centre,departement,region",
        }
    )
    req = urllib.request.Request(
        f"https://geo.api.gouv.fr/communes?{q}",
        headers={"User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(req, timeout=40) as resp:
        communes = json.loads(resp.read().decode("utf-8"))
    needles: list[tuple[str, tuple[float, float], str]] = []
    extra_keys: list[str] = []
    for item in communes or []:
        nom = str((item or {}).get("nom") or "")
        xy = _coords_to_latlon((item or {}).get("centre"))
        if nom and xy:
            needles.append((_fold_txt(nom), xy, nom))
        dept = (item or {}).get("departement") or {}
        region = (item or {}).get("region") or {}
        for label in (dept.get("nom"), region.get("nom")):
            folded = _fold_txt(str(label or ""))
            if folded and folded not in extra_keys and len(folded) >= 5:
                extra_keys.append(folded)
    features = []
    seen: set[str] = set()
    for ev in catalog:
        titre = ev.get("titre") or ""
        folded = _fold_txt(titre)
        hits = [xy for name, xy, _orig in needles if name and name in folded]
        if not hits and not any(key in folded for key in extra_keys):
            continue
        key = ev.get("numero") or ev.get("fichier") or titre
        if key in seen:
            continue
        seen.add(key)
        if hits:
            lat = sum(p[0] for p in hits) / len(hits)
            lon = sum(p[1] for p in hits) / len(hits)
        else:
            lat = (south + north) / 2
            lon = (west + east) / 2
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "numero": ev.get("numero"),
                    "titre": titre[:240],
                    "type_inond": _bdhi_type(titre),
                    "annee_debut": ev.get("annee_debut"),
                    "annee_fin": ev.get("annee_fin"),
                    "url": ev.get("url"),
                },
            }
        )
    if not features:
        _append_log(job, "Aucune fiche de synthèse BDHI ne correspond à l’emprise.\n")
        return
    n = _import_geojson_features(job, schema, "risk_bdhi", features, promote=False)
    _append_log(
        job,
        f"BDHI : {n} événements historiques (fiches de synthèse) → {schema}.risk_bdhi.\n",
    )


def _import_grids(
    job: dict,
    schema: str,
    west: float,
    south: float,
    east: float,
    north: float,
) -> None:
    """Carroyages DFCI (Lambert II) et UTM découpés à l'emprise."""
    ident(schema)
    dfci = dfci_features(west, south, east, north)
    if dfci:
        n = _import_geojson_features(job, schema, "grid_dfci", dfci)
        _append_log(job, f"Carroyage DFCI : {n} mailles → {schema}.grid_dfci.\n")
    else:
        _append_log(job, "Hors grille DFCI métropolitaine : pas de carroyage DFCI.\n")
    utm = utm_features(west, south, east, north)
    if utm:
        n = _import_geojson_features(job, schema, "grid_utm", utm)
        _append_log(job, f"Carroyage UTM : {n} mailles → {schema}.grid_utm.\n")


def _extract_officiels(job: dict, schema: str) -> None:
    """Hôpitaux, secours, forces, mairies et autres bâtiments officiels OSM."""
    ident(schema)
    parts: list[str] = []
    for table, geom_sql in (
        ("points", "geom"),
        (
            "multipolygons",
            "ST_PointOnSurface(ST_MakeValid(geom))",
        ),
    ):
        cols = {
            str(name)
            for (name,) in _pg_query(
                f"""
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = '{schema}' AND table_name = '{table}'
                """
            )
        }
        if "geom" not in cols:
            continue
        amenity = "amenity" if "amenity" in cols else "NULL::text"
        name = "name" if "name" in cols else "NULL::text"
        operator = "operator" if "operator" in cols else "NULL::text"
        office = "office" if "office" in cols else "NULL::text"
        military = "military" if "military" in cols else "NULL::text"
        building = "building" if "building" in cols else "NULL::text"
        landuse = "landuse" if "landuse" in cols else "NULL::text"
        healthcare = "healthcare" if "healthcare" in cols else "NULL::text"
        emergency = "emergency" if "emergency" in cols else "NULL::text"
        other = "other_tags" if "other_tags" in cols else "NULL::text"
        osm_id = "osm_id" if "osm_id" in cols else "NULL::bigint"
        parts.append(
            f"""
            SELECT {osm_id} AS osm_id, {name} AS name, {amenity} AS amenity,
                   {operator} AS operator, {office} AS office,
                   {military} AS military, {building} AS building,
                   {landuse} AS landuse, {healthcare} AS healthcare,
                   {emergency} AS emergency, {other} AS other_tags,
                   {geom_sql} AS geom, '{table}' AS osm_layer
            FROM {schema}.{table}
            WHERE geom IS NOT NULL
            """
        )
    if not parts:
        return
    union_sql = " UNION ALL ".join(parts)
    _pg_exec(f"DROP TABLE IF EXISTS {schema}.officiels CASCADE")
    _pg_exec(
        f"""
        CREATE TABLE {schema}.officiels AS
        SELECT
            osm_id,
            name,
            CASE
              WHEN amenity = 'hospital'
                OR COALESCE(healthcare, '') = 'hospital'
                OR COALESCE(other_tags, '') ~* '"healthcare"\\s*:\\s*"hospital"'
                THEN 'hopital'
              WHEN amenity IN ('clinic', 'doctors')
                OR COALESCE(healthcare, '') IN ('clinic', 'doctor', 'centre')
                THEN 'clinique'
              WHEN amenity = 'fire_station'
                OR COALESCE(emergency, '') IN ('fire_station', 'ambulance_station')
                THEN 'pompier'
              WHEN amenity = 'police' AND (
                    COALESCE(name, '') ILIKE '%gendarmerie%'
                 OR COALESCE(operator, '') ILIKE '%gendarmerie%'
                 OR COALESCE(other_tags, '') ILIKE '%gendarmerie%'
              ) THEN 'gendarmerie'
              WHEN amenity = 'police' THEN 'police'
              WHEN amenity = 'townhall' THEN 'mairie'
              WHEN amenity = 'courthouse' THEN 'justice'
              WHEN amenity = 'post_office' THEN 'poste'
              WHEN amenity IN ('embassy', 'consulate')
                OR COALESCE(office, '') = 'diplomatic'
                THEN 'diplomatique'
              WHEN COALESCE(name, '') ILIKE '%préfecture%'
                OR COALESCE(name, '') ILIKE '%prefecture%'
                THEN 'prefecture'
              WHEN COALESCE(office, '') IN (
                    'government', 'administrative', 'tax', 'register', 'prosecutor'
                 )
                THEN 'administration'
              WHEN (COALESCE(military, '') NOT IN ('', 'no'))
                OR COALESCE(landuse, '') = 'military'
                THEN 'militaire'
              WHEN COALESCE(building, '') IN ('civic', 'public', 'government')
                THEN 'public'
              ELSE NULL
            END AS categorie,
            amenity,
            operator,
            office,
            osm_layer,
            geom
        FROM ({union_sql}) src
        """
    )
    _pg_exec(f"DELETE FROM {schema}.officiels WHERE categorie IS NULL OR geom IS NULL")
    _pg_exec(
        f"""
        ALTER TABLE {schema}.officiels
        ALTER COLUMN geom TYPE geometry(Point, 4326)
        USING ST_SetSRID(ST_PointOnSurface(ST_MakeValid(geom)), 4326)
        """
    )
    _pg_exec(
        f"CREATE INDEX IF NOT EXISTS {schema}_officiels_geom_idx ON {schema}.officiels USING GIST (geom)"
    )
    rows = _pg_query(f"SELECT COUNT(*) AS n FROM {schema}.officiels")
    n = int(rows[0][0]) if rows else 0
    if n == 0:
        _pg_exec(f"DROP TABLE IF EXISTS {schema}.officiels CASCADE")
        _append_log(job, "Aucun bâtiment officiel OSM dans l’emprise.\n")
        return
    cats = _pg_query(
        f"""
        SELECT categorie, COUNT(*) AS n
        FROM {schema}.officiels
        GROUP BY categorie
        ORDER BY n DESC
        """
    )
    summary = ", ".join(f"{c}={v}" for c, v in cats)
    _append_log(job, f"Bâtiments officiels : {n} ({summary}) → {schema}.officiels.\n")


def _import_shom(
    job: dict,
    schema: str,
    west: float,
    south: float,
    east: float,
    north: float,
) -> None:
    """Couches maritimes SHOM (WFS public) découpées à l'emprise."""
    _import_wfs_layers(
        job,
        schema,
        west,
        south,
        east,
        north,
        f"WFS:{SHOM_WFS}",
        SHOM_LAYERS,
        "Données maritimes SHOM (WFS)",
        "Aucune couche SHOM dans l’emprise (zone hors littoral français, ou service indisponible).",
    )


def _import_risks(
    job: dict,
    schema: str,
    west: float,
    south: float,
    east: float,
    north: float,
) -> None:
    """Zonages de risque et mémoire des crises (Géorisques / GASPAR)."""
    _import_wfs_layers(
        job,
        schema,
        west,
        south,
        east,
        north,
        f"WFS:{GEORISQUES_WFS}",
        RISK_LAYERS,
        "Risques et mémoire des crises (Géorisques)",
        "Aucune couche Géorisques dans l’emprise (hors France, ou service indisponible).",
    )
    try:
        _import_catnat(job, schema, west, south, east, north)
    except Exception as exc:  # noqa: BLE001
        _append_log(job, f"CATNAT indisponible ({exc}).\n")
    try:
        _import_dicrim(job, schema, west, south, east, north)
    except Exception as exc:  # noqa: BLE001
        _append_log(job, f"DICRIM indisponible ({exc}).\n")
    try:
        _import_bdhi(job, schema, west, south, east, north)
    except Exception as exc:  # noqa: BLE001
        _append_log(job, f"BDHI indisponible ({exc}).\n")


def _run_import(job: dict) -> None:
    osm_path = Path(job["osm_path"])
    schema = job["schema"]
    ring = [(float(lat), float(lon)) for lat, lon in job["ring"]]
    west, south, east, north = (
        job["bbox"]["west"],
        job["bbox"]["south"],
        job["bbox"]["east"],
        job["bbox"]["north"],
    )
    try:
        if not IMPORT_LOCK.acquire(blocking=False):
            raise OsmImportError("Une importation est déjà en cours. Réessayez ensuite.")
        try:
            job["status"] = "running"
            _append_log(job, "Connexion à PostGIS…\n")
            ensure_meta()
            _download_overpass(job, osm_path, ring)
            cleaned = _sanitize_osm_xml(osm_path)
            if cleaned:
                _append_log(
                    job,
                    f"Tags OSM nettoyés : {cleaned} caractère(s) de contrôle "
                    "(retours à la ligne dans other_tags).\n",
                )
            _append_log(job, f"Création du schéma {schema}…\n")
            with GDAL_LOCK:
                _pg_exec(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
                _pg_exec(f"CREATE SCHEMA {schema}")
                gdal.SetConfigOption("PG_USE_COPY", "YES")
                gdal.SetConfigOption("OSM_CONFIG_FILE", str(OSMCONF))
                gdal.SetConfigOption("OSM_USE_CUSTOM_INDEXING", "NO")

                last_pct = [-1]

                def _progress(complete: float, message: str, _unused) -> int:
                    pct = int(complete * 100)
                    if pct >= last_pct[0] + 5 or pct in {0, 100}:
                        last_pct[0] = pct
                        extra = f" {message}" if message else ""
                        _append_log(job, f"Import GDAL {pct}%{extra}\n")
                    return 1

                _append_log(job, "Import dans PostGIS (GDAL/OGR)…\n")
                options = gdal.VectorTranslateOptions(
                    format="PostgreSQL",
                    accessMode="overwrite",
                    layerCreationOptions=[
                        f"SCHEMA={schema}",
                        "GEOMETRY_NAME=geom",
                        "SPATIAL_INDEX=GIST",
                        "FID=ogc_fid",
                        "PRECISION=NO",
                        "COLUMN_TYPES=other_tags=TEXT",
                    ],
                    geometryType="PROMOTE_TO_MULTI",
                    spatFilter=(west, south, east, north),
                    skipFailures=True,
                    callback=_progress,
                )
                result = gdal.VectorTranslate(pg_dsn(), str(osm_path), options=options)
                if result is None:
                    raise OsmImportError("L'import GDAL n'a produit aucune couche.")
                result = None
                if job.get("contours", True):
                    try:
                        _import_contours(job, schema, west, south, east, north)
                    except Exception as exc:  # noqa: BLE001
                        _append_log(
                            job,
                            f"Courbes de niveau indisponibles ({exc}). L'OSM est tout de même importé.\n",
                        )
                if job.get("maritime", True):
                    try:
                        _import_shom(job, schema, west, south, east, north)
                    except Exception as exc:  # noqa: BLE001
                        _append_log(
                            job,
                            f"Données SHOM indisponibles ({exc}). L'OSM est tout de même importé.\n",
                        )
                if job.get("risques", True):
                    try:
                        _import_risks(job, schema, west, south, east, north)
                    except Exception as exc:  # noqa: BLE001
                        _append_log(
                            job,
                            f"Données risques / crises indisponibles ({exc}). L'OSM est tout de même importé.\n",
                        )
                try:
                    _import_grids(job, schema, west, south, east, north)
                except Exception as exc:  # noqa: BLE001
                    _append_log(job, f"Carroyages DFCI/UTM indisponibles ({exc}).\n")
                _append_log(job, "Découpage au polygone…\n")
                _clip_to_ring(schema, ring)
                try:
                    _extract_officiels(job, schema)
                except Exception as exc:  # noqa: BLE001
                    _append_log(job, f"Bâtiments officiels non extraits ({exc}).\n")
                _extra_indexes(schema)
                counts = _layer_counts(schema)
                total = sum(counts.values())
                if total == 0:
                    raise OsmImportError(
                        "Aucune entité importée. La zone est peut-être vide, "
                        "ou Overpass a renvoyé un fichier trop pauvre."
                    )
                payload = json.dumps(counts)
                _pg_exec(
                    f"""
                    INSERT INTO {META_SCHEMA}.imports
                        (name, schema_name, west, south, east, north, source, bytes, counts)
                    VALUES (
                        '{schema}',
                        '{schema}',
                        {west}, {south}, {east}, {north},
                        'overpass',
                        {osm_path.stat().st_size},
                        $counts${payload}$counts$::jsonb
                    )
                    ON CONFLICT (name) DO UPDATE SET
                        west = EXCLUDED.west,
                        south = EXCLUDED.south,
                        east = EXCLUDED.east,
                        north = EXCLUDED.north,
                        source = EXCLUDED.source,
                        bytes = EXCLUDED.bytes,
                        counts = EXCLUDED.counts,
                        created_at = now()
                    """
                )
            try:
                with GDAL_LOCK:
                    n_styles = _save_qgis_styles(schema)
                if n_styles:
                    _append_log(
                        job,
                        f"Styles QGIS enregistrés dans public.layer_styles ({n_styles} couches).\n",
                    )
            except Exception as exc:  # noqa: BLE001
                _append_log(job, f"Styles QGIS non enregistrés ({exc}).\n")
            job["counts"] = counts
            job["size"] = osm_path.stat().st_size
            job["status"] = "done"
            _append_log(
                job,
                "Terminé : "
                + ", ".join(f"{name}={n}" for name, n in counts.items())
                + f"  (schéma {schema})\n",
            )
        finally:
            IMPORT_LOCK.release()
            osm_path.unlink(missing_ok=True)
    except OsmImportError as exc:
        job["status"] = "error"
        job["error"] = str(exc)
        _append_log(job, f"Erreur : {exc}\n")
        _append_erreurs_md(job, str(exc))
        try:
            with GDAL_LOCK:
                _pg_exec(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        job["status"] = "error"
        job["error"] = f"Échec inattendu : {exc}"
        _append_log(job, f"Erreur : {exc}\n")
        _append_erreurs_md(job, f"Échec inattendu : {exc}")
        try:
            with GDAL_LOCK:
                _pg_exec(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        except Exception:  # noqa: BLE001
            pass


def public_job(job: dict) -> dict:
    return {
        "id": job["id"],
        "name": job["name"],
        "schema": job["schema"],
        "status": job["status"],
        "error": job.get("error"),
        "log": job.get("log", ""),
        "size": job.get("size"),
        "counts": job.get("counts"),
        "bbox": job.get("bbox"),
    }


def start_import(payload: dict) -> dict:
    """Valide une demande d'import et démarre le thread de travail."""
    schema = sanitize_name(str(payload.get("name") or ""))
    raw_points = payload.get("points")
    if not isinstance(raw_points, list) or not (MIN_POINTS <= len(raw_points) <= MAX_POINTS):
        raise OsmImportError(
            f"Fournissez entre {MIN_POINTS} et {MAX_POINTS} points [lat, lon], dans l'ordre du contour."
        )
    points: list[tuple[float, float]] = []
    for item in raw_points:
        if not (isinstance(item, (list, tuple)) and len(item) == 2):
            raise OsmImportError("Chaque point doit être [lat, lon].")
        lat, lon = float(item[0]), float(item[1])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise OsmImportError("Coordonnées hors limites.")
        points.append((lat, lon))
    ring = as_ring(points)
    west, south, east, north = bbox_of(ring)
    overwrite = bool(payload.get("overwrite"))

    status = postgis_status()
    if not status["ok"]:
        raise OsmImportError(status["error"] or "Base PostGIS injoignable.")

    existing = {item["schema"] for item in list_imports()}
    if schema in existing and not overwrite:
        raise OsmImportError(
            f"Le schéma {schema} existe déjà. Cochez « remplacer » ou changez de nom."
        )

    job_id = uuid.uuid4().hex[:12]
    osm_path = CACHE_DIR / f".{schema}-{job_id}.osm"
    job = {
        "id": job_id,
        "name": schema.removeprefix(SCHEMA_PREFIX),
        "schema": schema,
        "status": "queued",
        "osm_path": str(osm_path),
        "ring": ring,
        "bbox": {"west": west, "south": south, "east": east, "north": north},
        "log": "",
        "error": None,
        "size": None,
        "counts": None,
        "contours": bool(payload.get("contours", True)),
        "maritime": bool(payload.get("maritime", True)),
        "risques": bool(payload.get("risques", True)),
    }
    with JOBS_LOCK:
        if any(j["status"] in {"queued", "running"} for j in JOBS.values()):
            raise OsmImportError("Une importation est déjà en cours.")
        JOBS[job_id] = job
    thread = threading.Thread(target=_run_import, args=(job,), daemon=True)
    thread.start()
    return public_job(job)


def get_job(job_id: str) -> dict | None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    return public_job(job) if job else None


def list_imports() -> list[dict]:
    """Liste les schémas importés, du plus récent au plus ancien."""
    try:
        ensure_meta()
    except OsmImportError:
        return []
    with GDAL_LOCK:
        rows = _pg_query(
            f"""
            SELECT name, schema_name, west, south, east, north,
                   source, bytes, CAST(counts AS text), CAST(created_at AS text)
            FROM {META_SCHEMA}.imports
            ORDER BY created_at DESC
            """
        )
    out = []
    for row in rows:
        name, schema, west, south, east, north, source, bytes_, counts_raw, created = row
        try:
            counts = json.loads(counts_raw) if counts_raw else {}
        except json.JSONDecodeError:
            counts = {}
        out.append(
            {
                "name": (name or schema or "").removeprefix(SCHEMA_PREFIX),
                "schema": schema,
                "bbox": {"west": west, "south": south, "east": east, "north": north},
                "source": source,
                "size": bytes_,
                "counts": counts,
                "created_at": created,
            }
        )
    return out


def preview_geojson(schema: str, limit: int = 3500) -> dict:
    """Aperçu type OpenTopoMap : occupation du sol, hydro, courbes, routes, puis points."""
    schema = ident(schema if schema.startswith(SCHEMA_PREFIX) else sanitize_name(schema))
    known = {item["schema"] for item in list_imports()}
    if schema not in known:
        with GDAL_LOCK:
            tables = _layer_counts(schema)
        if not tables:
            raise OsmImportError("Schéma introuvable.")
    limit = max(50, min(int(limit), 8000))
    queries = [
        (
            "land",
            f"""
            SELECT 'land' AS kind, name,
                   COALESCE(landuse, "natural", leisure) AS subtype,
                   ST_SimplifyPreserveTopology(ST_MakeValid(geom), 0.0001) AS geom
            FROM {schema}.multipolygons
            WHERE geom IS NOT NULL
              AND (
                landuse IN ('forest','wood','grass','meadow','farmland','orchard',
                            'vineyard','residential','industrial','commercial','retail',
                            'cemetery','quarry','allotments','recreation_ground','heath',
                            'scrub','military')
                OR "natural" IN ('wood','scrub','grassland','heath','scree','bare_rock',
                                 'beach','sand','fell','moor')
                OR leisure IN ('park','garden','nature_reserve','pitch','golf_course')
              )
            LIMIT {max(800, limit)}
            """,
        ),
        (
            "water",
            f"""
            SELECT 'water' AS kind, name,
                   COALESCE("natural", landuse, 'water') AS subtype,
                   ST_SimplifyPreserveTopology(ST_MakeValid(geom), 0.00008) AS geom
            FROM {schema}.multipolygons
            WHERE geom IS NOT NULL
              AND ("natural" IN ('water','bay','wetland','coastline')
                   OR landuse IN ('reservoir','basin','pond'))
            LIMIT {max(300, limit // 3)}
            """,
        ),
        (
            "building",
            f"""
            SELECT 'building' AS kind, name,
                   COALESCE(building, 'yes') AS subtype,
                   ST_SimplifyPreserveTopology(ST_MakeValid(geom), 0.00004) AS geom
            FROM {schema}.multipolygons
            WHERE geom IS NOT NULL AND building IS NOT NULL
            LIMIT {max(600, limit // 2)}
            """,
        ),
        (
            "contour",
            f"""
            SELECT kind, name, subtype, geom
            FROM (
              SELECT 'contour' AS kind,
                     CAST(ROUND(elev)::int AS text) AS name,
                     CASE
                       WHEN ROUND(elev)::int % 100 = 0 THEN 'index'
                       WHEN ROUND(elev)::int % 50 = 0 THEN 'major'
                       ELSE 'minor'
                     END AS subtype,
                     ST_SimplifyPreserveTopology(geom, 0.00002) AS geom,
                     0 AS rn
              FROM {schema}.contours
              WHERE geom IS NOT NULL AND ROUND(elev)::int % 50 = 0
              UNION ALL
              SELECT 'contour' AS kind,
                     CAST(ROUND(elev)::int AS text) AS name,
                     'minor' AS subtype,
                     ST_SimplifyPreserveTopology(geom, 0.00002) AS geom,
                     ROW_NUMBER() OVER (
                       PARTITION BY ROUND(elev)::int
                       ORDER BY ST_Length(geom) DESC
                     ) AS rn
              FROM {schema}.contours
              WHERE geom IS NOT NULL
                AND ROUND(elev)::int % 10 = 0
                AND ROUND(elev)::int % 50 <> 0
            ) sampled
            WHERE subtype IN ('index', 'major') OR rn <= 10
            LIMIT {min(7000, limit * 2)}
            """,
        ),
        (
            "waterway",
            f"""
            SELECT 'waterway' AS kind, name, waterway AS subtype,
                   ST_SimplifyPreserveTopology(geom, 0.00004) AS geom
            FROM {schema}.lines
            WHERE waterway IS NOT NULL AND geom IS NOT NULL
            LIMIT {max(400, limit // 3)}
            """,
        ),
        (
            "railway",
            f"""
            SELECT 'railway' AS kind, name, railway AS subtype,
                   ST_SimplifyPreserveTopology(geom, 0.00005) AS geom
            FROM {schema}.lines
            WHERE railway IS NOT NULL AND geom IS NOT NULL
            LIMIT 400
            """,
        ),
        (
            "highway",
            f"""
            SELECT 'highway' AS kind, name, highway AS subtype,
                   ST_SimplifyPreserveTopology(geom, 0.00004) AS geom
            FROM {schema}.lines
            WHERE highway IS NOT NULL AND geom IS NOT NULL
            ORDER BY z_order DESC NULLS LAST
            LIMIT {max(1200, limit)}
            """,
        ),
        (
            "shom_coast",
            f"""
            SELECT 'shom_coast' AS kind, 'limite terre-mer' AS name, 'coast' AS subtype,
                   ST_SimplifyPreserveTopology(geom, 0.00005) AS geom
            FROM {schema}.shom_limite_terre_mer
            WHERE geom IS NOT NULL
            LIMIT 800
            """,
        ),
        (
            "risk_flood",
            f"""
            SELECT 'risk_flood' AS kind, 'PPR inondation' AS name, 'inondation' AS subtype,
                   ST_SimplifyPreserveTopology(ST_MakeValid(geom), 0.0002) AS geom
            FROM {schema}.risk_pprn_inondation
            WHERE geom IS NOT NULL
            LIMIT 120
            """,
        ),
        (
            "shom_point",
            f"""
            SELECT 'shom_point' AS kind, CAST(ogc_fid AS text) AS name, 'epave' AS subtype, geom
            FROM {schema}.shom_epaves
            WHERE geom IS NOT NULL
            LIMIT 150
            """,
        ),
        (
            "risk_catnat",
            f"""
            SELECT 'risk_catnat' AS kind,
                   COALESCE(commune, CAST(ogc_fid AS text)) AS name,
                   COALESCE(risque, 'catnat') AS subtype, geom
            FROM {schema}.risk_catnat
            WHERE geom IS NOT NULL
            LIMIT 150
            """,
        ),
        (
            "risk_dicrim",
            f"""
            SELECT 'risk_dicrim' AS kind,
                   COALESCE(commune, insee) AS name,
                   COALESCE(url, 'dicrim') AS subtype, geom
            FROM {schema}.risk_dicrim
            WHERE geom IS NOT NULL
            LIMIT 80
            """,
        ),
        (
            "risk_bdhi",
            f"""
            SELECT 'risk_bdhi' AS kind,
                   COALESCE(titre, CAST(numero AS text)) AS name,
                   COALESCE(type_inond, 'bdhi') AS subtype, geom
            FROM {schema}.risk_bdhi
            WHERE geom IS NOT NULL
            LIMIT 80
            """,
        ),
        (
            "officiel",
            f"""
            SELECT 'officiel' AS kind,
                   COALESCE(name, categorie) AS name,
                   categorie AS subtype, geom
            FROM {schema}.officiels
            WHERE geom IS NOT NULL
            LIMIT 300
            """,
        ),
        (
            "point",
            f"""
            SELECT 'point' AS kind, name,
                   COALESCE(place, amenity, shop, tourism) AS subtype, geom
            FROM {schema}.points
            WHERE geom IS NOT NULL
              AND (place IS NOT NULL OR amenity IS NOT NULL OR shop IS NOT NULL
                   OR tourism IS NOT NULL)
            LIMIT {max(150, limit // 6)}
            """,
        ),
    ]
    features: list[dict] = []
    with GDAL_LOCK:
        ds = _pg_open()
        try:
            for kind, sql in queries:
                try:
                    layer = ds.ExecuteSQL(sql, dialect="POSTGRESQL")
                except RuntimeError:
                    continue
                if layer is None:
                    continue
                try:
                    layer.ResetReading()
                    feat = layer.GetNextFeature()
                    while feat:
                        data = json.loads(feat.ExportToJson())
                        props = data.setdefault("properties", {})
                        props.setdefault("kind", kind)
                        features.append(data)
                        feat = layer.GetNextFeature()
                finally:
                    ds.ReleaseResultSet(layer)
        finally:
            ds = None
    return {"type": "FeatureCollection", "features": features, "schema": schema}
