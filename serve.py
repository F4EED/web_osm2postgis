#!/usr/bin/env python3
"""Serveur HTTP local d'OSM2Postgis : carte Europe + import OSM vers PostGIS.

Lancement ::

    python3 serve.py            # http://127.0.0.1:8001/
    python3 serve.py -p 8080
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

import import_osm


class Handler(SimpleHTTPRequestHandler):
    """Fichiers statiques + API JSON. Une instance par requête."""

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))
        sys.stderr.flush()

    def _send_json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_api_get(self, path: str) -> bool:
        if path == "/api/status":
            self._send_json(200, import_osm.postgis_status())
            return True
        if path == "/api/imports":
            self._send_json(200, {"imports": import_osm.list_imports()})
            return True
        if path.startswith("/api/jobs/"):
            job_id = path.rsplit("/", 1)[-1]
            job = import_osm.get_job(job_id)
            if not job:
                self._send_json(404, {"error": "Job introuvable."})
                return True
            self._send_json(200, job)
            return True
        if path.startswith("/api/preview/"):
            schema = path.rsplit("/", 1)[-1]
            try:
                geojson = import_osm.preview_geojson(schema)
            except import_osm.OsmImportError as exc:
                self._send_json(404, {"error": str(exc)})
                return True
            self._send_json(200, geojson)
            return True
        if path.startswith("/api/"):
            self._send_json(404, {"error": "API inconnue."})
            return True
        return False

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if self._handle_api_get(path):
            return
        super().do_GET()

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path.startswith("/api/"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            return
        super().do_HEAD()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/import":
            self.send_error(404, "Not Found")
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0 or length > 50_000:
            self._send_json(400, {"error": "Requête invalide."})
            return
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "JSON invalide."})
            return
        try:
            job = import_osm.start_import(payload)
        except import_osm.OsmImportError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        self._send_json(202, job)


class ReuseThreadingServer(ThreadingHTTPServer):
    allow_reuse_address = True


def main() -> int:
    import_osm.load_env()
    parser = argparse.ArgumentParser(
        description="Sert la carte Europe et l'import OSM vers PostGIS."
    )
    parser.add_argument(
        "-p",
        "--port",
        type=int,
        default=int(os.environ.get("OSM2POSTGIS_PORT", "8001")),
    )
    args = parser.parse_args()

    os.chdir(os.path.dirname(os.path.abspath(__file__)) or ".")
    import_osm.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        httpd = ReuseThreadingServer(("0.0.0.0", args.port), Handler)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            print(
                f"Le port {args.port} est déjà utilisé.\n"
                f"Relancez avec un autre port, par exemple :\n"
                f"  python3 {os.path.abspath(__file__)} -p {args.port + 1}",
                file=sys.stderr,
            )
            return 1
        raise
    print(f"OSM → PostGIS : http://127.0.0.1:{args.port}/", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
