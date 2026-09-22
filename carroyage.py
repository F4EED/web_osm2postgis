"""Génération des carroyages DFCI (Lambert II étendu) et UTM pour une emprise."""

from __future__ import annotations

from osgeo import osr

osr.UseExceptions()

LETTERS_100 = "ABCDEFGHKLMN"
LETTERS_2 = "ABCDEFGHKL"
DFCI_Y0 = 1_600_000
DFCI_BOUNDS = (0, 1_200_000, 1_600_000, 2_700_000)


def _srs(epsg: int) -> osr.SpatialReference:
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(epsg)
    srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return srs


def _ct(src: int, dst: int) -> osr.CoordinateTransformation:
    return osr.CoordinateTransformation(_srs(src), _srs(dst))


def _lambert_bbox(
    west: float, south: float, east: float, north: float
) -> tuple[float, float, float, float]:
    ct = _ct(4326, 27572)
    xs: list[float] = []
    ys: list[float] = []
    lons = [west, east, (west + east) / 2]
    lats = [south, north, (south + north) / 2]
    for lon in lons:
        for lat in lats:
            x, y, _ = ct.TransformPoint(lon, lat)
            xs.append(x)
            ys.append(y)
    return min(xs), min(ys), max(xs), max(ys)


def _rect_wgs(
    x0: float, y0: float, x1: float, y1: float, ct_to_wgs: osr.CoordinateTransformation
) -> list[list[float]]:
    ring = []
    for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)):
        lon, lat, _ = ct_to_wgs.TransformPoint(x, y)
        ring.append([lon, lat])
    return ring


def dfci_code(x: float, y: float, size: int) -> str | None:
    minx, maxx, miny, maxy = DFCI_BOUNDS
    if x < minx or x >= maxx or y < miny or y >= maxy:
        return None
    yrel = y - DFCI_Y0
    ix = int(x // 100_000)
    iy = int(yrel // 100_000) + 1
    if ix < 0 or ix >= len(LETTERS_100) or iy < 0 or iy >= len(LETTERS_100):
        return None
    code = LETTERS_100[ix] + LETTERS_100[iy]
    if size >= 100_000:
        return code
    dx = x % 100_000
    dy = yrel % 100_000
    code += str(int(dx // 20_000) * 2) + str(int(dy // 20_000) * 2)
    if size >= 20_000:
        return code
    dx %= 20_000
    dy %= 20_000
    kx = int(dx // 2_000)
    ky = int(dy // 2_000)
    if kx < 0 or kx >= len(LETTERS_2) or ky < 0 or ky > 9:
        return None
    return code + LETTERS_2[kx] + str(ky)


def dfci_features(
    west: float, south: float, east: float, north: float
) -> list[dict]:
    minx, miny, maxx, maxy = _lambert_bbox(west, south, east, north)
    minx = max(DFCI_BOUNDS[0], minx)
    maxx = min(DFCI_BOUNDS[1], maxx)
    miny = max(DFCI_BOUNDS[2], miny)
    maxy = min(DFCI_BOUNDS[3], maxy)
    if minx >= maxx or miny >= maxy:
        return []
    ct = _ct(27572, 4326)
    features: list[dict] = []
    for size, niveau in ((100_000, "100km"), (20_000, "20km"), (2_000, "2km")):
        x0 = int(minx // size) * size
        y0 = int(miny // size) * size
        x = x0
        while x < maxx:
            y = y0
            while y < maxy:
                code = dfci_code(x + size / 2, y + size / 2, size)
                if code:
                    features.append(
                        {
                            "type": "Feature",
                            "geometry": {
                                "type": "Polygon",
                                "coordinates": [_rect_wgs(x, y, x + size, y + size, ct)],
                            },
                            "properties": {
                                "code": code,
                                "niveau": niveau,
                                "niveau_m": size,
                            },
                        }
                    )
                y += size
            x += size
    return features


def utm_zone_for_lon(lon: float) -> int:
    zone = int((lon + 180.0) / 6.0) + 1
    return max(1, min(60, zone))


def utm_features(
    west: float, south: float, east: float, north: float, max_1km: int = 40_000
) -> list[dict]:
    z0 = utm_zone_for_lon(west + 1e-6)
    z1 = utm_zone_for_lon(east - 1e-6)
    features: list[dict] = []
    area_km2 = max(0.0, (east - west) * 111.0) * max(0.0, (north - south) * 111.0)
    sizes = [10_000]
    if area_km2 <= max_1km:
        sizes.append(1_000)
    for zone in range(z0, z1 + 1):
        epsg = 32600 + zone
        ct_fwd = _ct(4326, epsg)
        ct_back = _ct(epsg, 4326)
        xs, ys = [], []
        for lon in (west, east, (west + east) / 2):
            for lat in (south, north, (south + north) / 2):
                x, y, _ = ct_fwd.TransformPoint(lon, lat)
                xs.append(x)
                ys.append(y)
        minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
        for size in sizes:
            x0 = int(minx // size) * size
            y0 = int(miny // size) * size
            x = x0
            n_before = len(features)
            while x < maxx:
                y = y0
                while y < maxy:
                    e_km = int(x // 1000)
                    n_km = int(y // 1000)
                    features.append(
                        {
                            "type": "Feature",
                            "geometry": {
                                "type": "Polygon",
                                "coordinates": [
                                    _rect_wgs(x, y, x + size, y + size, ct_back)
                                ],
                            },
                            "properties": {
                                "code": f"{zone}N_E{e_km}_N{n_km}",
                                "zone": f"{zone}N",
                                "easting": int(x),
                                "northing": int(y),
                                "niveau_m": size,
                            },
                        }
                    )
                    y += size
                x += size
            if size == 1_000 and len(features) - n_before > max_1km:
                features = features[:n_before]
                break
    return features
