"""Import d'une emprise OpenStreetMap dans PostGIS.

Principe
--------
L'utilisateur clique 4 points sur la carte (même interaction que pmtiles).
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
from pathlib import Path

from osgeo import gdal, ogr

gdal.UseExceptions()
ogr.UseExceptions()

ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / "data" / "cache"
OSMCONF = ROOT / "osmconf.ini"
META_SCHEMA = "osm2postgis"
SCHEMA_PREFIX = "osm_"

DEFAULT_PG = {
    "PGHOST": "127.0.0.1",
    "PGPORT": "5433",
    "PGUSER": "osm",
    "PGPASSWORD": "osm",
    "PGDATABASE": "osm",
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
MAX_OSM_BYTES = 1_500_000_000
USER_AGENT = "OSM2Postgis/1.0 (import local PostGIS)"

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


def order_ring(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Ordonne 4 points (lat, lon) en anneau autour du centroïde."""
    if len(points) != 4:
        raise OsmImportError("Il faut exactement 4 points.")
    lat0 = sum(p[0] for p in points) / 4
    lon0 = sum(p[1] for p in points) / 4
    return sorted(points, key=lambda p: math.atan2(p[0] - lat0, p[1] - lon0))


def bbox_of(points: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    """Rectangle englobant ``(ouest, sud, est, nord)`` avec garde-fous de taille."""
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    south, north = min(lats), max(lats)
    west, east = min(lons), max(lons)
    if east - west > MAX_SPAN_DEG or north - south > MAX_SPAN_DEG:
        raise OsmImportError(
            f"Zone trop large (max {MAX_SPAN_DEG:g}° de côté). "
            "Zoomez et resserrez les 4 points : Overpass refuse les très grandes emprises."
        )
    if east - west < MIN_SPAN_DEG or north - south < MIN_SPAN_DEG:
        raise OsmImportError("Zone trop petite. Écartez davantage les 4 points.")
    return west, south, east, north


def _append_log(job: dict, line: str) -> None:
    job["log"] = (job.get("log") or "") + line
    if len(job["log"]) > 8000:
        job["log"] = job["log"][-6000:]


def _overpass_ql(west: float, south: float, east: float, north: float) -> str:
    """Requête Overpass bornée, sans les relations administratives nationales.

    Les relations ``boundary`` (pays, régions) intersectent souvent un simple
    département et feraient télécharger des millions de nœuds hors zone.
    On se limite aux nœuds, chemins, et relations de type multipolygone
    (bâtiments, occupations du sol).
    """
    s, w, n, e = f"{south:.7f}", f"{west:.7f}", f"{north:.7f}", f"{east:.7f}"
    return (
        "[out:xml][timeout:180][maxsize:1073741824];\n"
        "(\n"
        f"  node({s},{w},{n},{e});\n"
        f"  way({s},{w},{n},{e});\n"
        f'  relation["type"="multipolygon"]({s},{w},{n},{e});\n'
        ");\n"
        "(._;>;);\n"
        "out meta;\n"
    )


def _download_overpass(job: dict, dest: Path, west: float, south: float, east: float, north: float) -> None:
    query = _overpass_ql(west, south, east, north)
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
                _append_log(job, f"Fichier OSM : {written / 1048576:.1f} Mo\n")
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
            "Base PostGIS injoignable. Dans le dossier du projet, lancez "
            "« docker compose up -d » puis réessayez."
        ) from exc
    if ds is None:
        raise OsmImportError(
            "Base PostGIS injoignable. Dans le dossier du projet, lancez "
            "« docker compose up -d » puis réessayez."
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
            "error": f"Base PostGIS injoignable ({exc}). Lancez : docker compose up -d",
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
    ]
    for sql in statements:
        try:
            _pg_exec(sql)
        except RuntimeError:
            # La table correspondante n'existe pas (couche OSM vide) : normal.
            continue


def _run_import(job: dict) -> None:
    osm_path = Path(job["osm_path"])
    schema = job["schema"]
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
            _download_overpass(job, osm_path, west, south, east, north)
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
        try:
            with GDAL_LOCK:
                _pg_exec(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001
        job["status"] = "error"
        job["error"] = f"Échec inattendu : {exc}"
        _append_log(job, f"Erreur : {exc}\n")
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
    if not isinstance(raw_points, list) or len(raw_points) != 4:
        raise OsmImportError("Fournissez 4 points [lat, lon].")
    points: list[tuple[float, float]] = []
    for item in raw_points:
        if not (isinstance(item, (list, tuple)) and len(item) == 2):
            raise OsmImportError("Chaque point doit être [lat, lon].")
        lat, lon = float(item[0]), float(item[1])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise OsmImportError("Coordonnées hors limites.")
        points.append((lat, lon))
    ring = order_ring(points)
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


def preview_geojson(schema: str, limit: int = 1800) -> dict:
    """Échantillon GeoJSON pour affichage sur la carte (routes, surfaces, lieux)."""
    schema = ident(schema if schema.startswith(SCHEMA_PREFIX) else sanitize_name(schema))
    known = {item["schema"] for item in list_imports()}
    if schema not in known:
        # Autoriser un schéma encore en cours d'aperçu si les tables existent.
        with GDAL_LOCK:
            tables = _layer_counts(schema)
        if not tables:
            raise OsmImportError("Schéma introuvable.")
    limit = max(50, min(int(limit), 4000))
    queries = [
        (
            "highway",
            f"""
            SELECT 'highway' AS kind, name, highway AS subtype,
                   ST_SimplifyPreserveTopology(geom, 0.00008) AS geom
            FROM {schema}.lines
            WHERE highway IS NOT NULL AND geom IS NOT NULL
            ORDER BY z_order DESC NULLS LAST
            LIMIT {limit}
            """,
        ),
        (
            "area",
            f"""
            SELECT 'area' AS kind, name,
                   COALESCE(building, landuse, "natural", amenity) AS subtype,
                   ST_SimplifyPreserveTopology(ST_MakeValid(geom), 0.00012) AS geom
            FROM {schema}.multipolygons
            WHERE geom IS NOT NULL
              AND (building IS NOT NULL OR landuse IS NOT NULL OR "natural" IS NOT NULL)
            LIMIT {max(400, limit // 2)}
            """,
        ),
        (
            "point",
            f"""
            SELECT 'point' AS kind, name,
                   COALESCE(place, amenity, shop, tourism, highway) AS subtype,
                   geom
            FROM {schema}.points
            WHERE geom IS NOT NULL
              AND (place IS NOT NULL OR amenity IS NOT NULL OR shop IS NOT NULL
                   OR tourism IS NOT NULL)
            LIMIT {max(200, limit // 4)}
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
