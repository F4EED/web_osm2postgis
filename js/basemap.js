/**
 * basemap.js — fonds OSM / OpenTopoMap, liste des schémas, aperçu vectoriel.
 *
 * Dépendances : window.map, window.setOverlayForBasemap (map.js).
 * Exporte : window.loadOsmPreview(schema), window.refreshImportList(),
 *           window.previewActive.
 */
(() => {
  const map = window.map;
  const tilesToggle = document.getElementById("osm-tiles");
  const basemapSelect = document.getElementById("basemap-select");
  const importSelect = document.getElementById("import-select");
  const dbStatus = document.getElementById("db-status");
  const mapEl = document.getElementById("map");
  if (!map) return;

  map.createPane("osmRasterPane");
  map.getPane("osmRasterPane").style.zIndex = 450;

  const PREVIEW_PANES = [
    ["otmLandPane", 460],
    ["otmWaterPane", 470],
    ["otmBuildingPane", 480],
    ["otmContourPane", 490],
    ["otmLinePane", 500],
    ["otmPointPane", 620],
  ];
  for (const [name, z] of PREVIEW_PANES) {
    map.createPane(name);
    map.getPane(name).style.zIndex = z;
  }

  const osmTiles = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    pane: "osmRasterPane",
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  });

  const opentopoTiles = L.tileLayer(
    "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", {
      pane: "osmRasterPane",
      maxZoom: 17,
      subdomains: "abc",
      attribution:
        'carte : &copy; <a href="https://opentopomap.org">OpenTopoMap</a> (CC-BY-SA) · MNT SRTM · &copy; OSM',
    }
  );

  const rasterLayers = { osm: osmTiles, opentopo: opentopoTiles };
  let activeRaster = null;

  const FOREST = new Set(["forest", "wood", "scrub"]);
  const GRASS = new Set(["grass", "meadow", "grassland", "heath"]);
  const FARM = new Set(["farmland", "orchard", "vineyard", "allotments"]);
  const PARK = new Set(["park", "garden", "nature_reserve", "pitch", "golf_course", "recreation_ground"]);
  const WATER = new Set(["water", "wetland", "bay", "reservoir", "riverbank", "basin", "pond", "coastline"]);

  function landStyle(feature) {
    const p = feature.properties || {};
    const sub = String(p.subtype || "").toLowerCase();
    if (p.kind === "risk_flood") {
      return { color: "#7eb6c9", weight: 0.4, fillColor: "#cfe6ee", fillOpacity: 0.14 };
    }
    if (FOREST.has(sub)) {
      return { color: "#b4cc9c", weight: 0.2, fillColor: "#c6ddb0", fillOpacity: 0.92 };
    }
    if (GRASS.has(sub)) {
      return { color: "#c8dba0", weight: 0.2, fillColor: "#dcebb6", fillOpacity: 0.9 };
    }
    if (FARM.has(sub)) {
      return { color: "#d4c498", weight: 0.2, fillColor: "#ead9a8", fillOpacity: 0.9 };
    }
    if (PARK.has(sub)) {
      return { color: "#b5c99a", weight: 0.2, fillColor: "#cde0b0", fillOpacity: 0.9 };
    }
    if (sub === "residential") {
      return { color: "#d4ccc4", weight: 0.2, fillColor: "#e8e0d5", fillOpacity: 0.92 };
    }
    if (sub === "industrial" || sub === "commercial" || sub === "retail") {
      return { color: "#cfc6ba", weight: 0.2, fillColor: "#ddd4c8", fillOpacity: 0.88 };
    }
    return { color: "#d2c8b4", weight: 0.2, fillColor: "#e6dcc8", fillOpacity: 0.75 };
  }

  function waterStyle(feature) {
    const kind = (feature.properties || {}).kind;
    if (kind === "shom_coast") {
      return { color: "#5a9bb0", weight: 1.8, opacity: 0.9, fill: false };
    }
    const sub = String((feature.properties || {}).subtype || "").toLowerCase();
    if (!WATER.has(sub) && sub && sub !== "water") {
      return { color: "#7eb6c9", weight: 0.4, fillColor: "#aad3df", fillOpacity: 0.8 };
    }
    return { color: "#7eb6c9", weight: 0.45, fillColor: "#aad3df", fillOpacity: 0.88 };
  }

  function buildingStyle() {
    return { color: "#c4b49a", weight: 0.35, fillColor: "#d4c4ae", fillOpacity: 0.9 };
  }

  function contourStyle(feature) {
    const sub = String((feature.properties || {}).subtype || "").toLowerCase();
    if (sub === "index") {
      return { color: "#a86b3c", weight: 1.35, opacity: 0.92, fill: false };
    }
    if (sub === "major") {
      return { color: "#c0844e", weight: 0.95, opacity: 0.82, fill: false };
    }
    return { color: "#d4b08a", weight: 0.45, opacity: 0.62, fill: false };
  }

  function lineStyle(feature) {
    const p = feature.properties || {};
    const kind = p.kind;
    const sub = String(p.subtype || "").toLowerCase();
    if (kind === "waterway") {
      if (sub === "river") {
        return { color: "#7eb6c9", weight: 2.2, opacity: 0.95, fill: false };
      }
      if (sub === "canal") {
        return { color: "#7eb6c9", weight: 1.8, opacity: 0.9, fill: false };
      }
      return { color: "#8fc4d4", weight: 0.9, opacity: 0.85, fill: false };
    }
    if (kind === "railway") {
      return {
        color: "#6a6a6a",
        weight: 1.15,
        opacity: 0.85,
        dashArray: "7 5",
        fill: false,
      };
    }
    if (sub === "motorway" || sub === "motorway_link") {
      return { color: "#e892a2", weight: 3.1, opacity: 0.96, fill: false };
    }
    if (sub === "trunk" || sub === "trunk_link") {
      return { color: "#f9b29c", weight: 2.7, opacity: 0.95, fill: false };
    }
    if (sub === "primary" || sub === "primary_link") {
      return { color: "#fcd6a4", weight: 2.3, opacity: 0.95, fill: false };
    }
    if (sub === "secondary" || sub === "secondary_link") {
      return { color: "#f7fabf", weight: 1.9, opacity: 0.95, fill: false };
    }
    if (
      sub === "tertiary" ||
      sub === "tertiary_link" ||
      sub === "residential" ||
      sub === "unclassified" ||
      sub === "living_street" ||
      sub === "service"
    ) {
      return { color: "#f7f2e8", weight: 1.55, opacity: 0.95, fill: false };
    }
    if (sub === "track" || sub === "bridleway") {
      return { color: "#8b5a2b", weight: 1.05, opacity: 0.8, dashArray: "5 4", fill: false };
    }
    if (sub === "path" || sub === "footway" || sub === "cycleway" || sub === "steps") {
      return { color: "#8b5a2b", weight: 0.85, opacity: 0.75, dashArray: "3 4", fill: false };
    }
    return { color: "#e8e0d5", weight: 1.3, opacity: 0.9, fill: false };
  }

  function bindPreviewPopup(feature, layer) {
    const p = feature.properties || {};
    const title = p.name || p.subtype || p.kind || "objet OSM";
    const extra = p.kind === "contour" && p.name ? ` ${p.name} m` : "";
    layer.bindPopup(
      `<strong>${title}${extra}</strong><span class="meta">${p.kind || ""} ${p.subtype || ""}</span>`
    );
    if (p.kind === "contour" && (p.subtype === "index" || p.subtype === "major") && p.name) {
      layer.bindTooltip(`${p.name} m`, {
        sticky: true,
        direction: "center",
        className: "otm-contour-label",
        opacity: 0.92,
      });
    }
  }

  function pointToLayer(feature, latlng) {
    const kind = (feature.properties || {}).kind || "";
    const extra =
      kind === "risk_catnat"
        ? " is-catnat"
        : kind === "risk_dicrim"
          ? " is-dicrim"
          : kind === "risk_bdhi"
            ? " is-bdhi"
            : kind === "officiel"
              ? " is-officiel is-" + String((feature.properties || {}).subtype || "")
              : kind === "risk_cavite"
                ? " is-cavite"
                : kind === "risk_mvt"
                  ? " is-mvt"
                  : kind === "shom_point"
                    ? " is-shom"
                    : "";
    return L.marker(latlng, {
      pane: "otmPointPane",
      keyboard: false,
      icon: L.divIcon({
        className: "osm-preview-point" + extra,
        html: "<span></span>",
        iconSize: [8, 8],
        iconAnchor: [4, 4],
      }),
    });
  }

  function makeCanvasLayer(pane, styleFn) {
    return L.geoJSON(null, {
      pane,
      renderer: L.canvas({ pane }),
      style: styleFn,
      onEachFeature: bindPreviewPopup,
    }).addTo(map);
  }

  const landLayer = makeCanvasLayer("otmLandPane", landStyle);
  const waterLayer = makeCanvasLayer("otmWaterPane", waterStyle);
  const buildingLayer = makeCanvasLayer("otmBuildingPane", buildingStyle);
  const contourLayer = makeCanvasLayer("otmContourPane", contourStyle);
  const waterwayLayer = makeCanvasLayer("otmLinePane", lineStyle);
  const railwayLayer = makeCanvasLayer("otmLinePane", lineStyle);
  const highwayLayer = makeCanvasLayer("otmLinePane", lineStyle);
  const pointLayer = L.geoJSON(null, {
    pane: "otmPointPane",
    pointToLayer,
    onEachFeature: bindPreviewPopup,
  }).addTo(map);

  const KIND_TO_LAYER = {
    land: landLayer,
    risk_flood: landLayer,
    water: waterLayer,
    shom_coast: waterLayer,
    building: buildingLayer,
    contour: contourLayer,
    waterway: waterwayLayer,
    railway: railwayLayer,
    highway: highwayLayer,
    shom_point: pointLayer,
    risk_catnat: pointLayer,
    risk_dicrim: pointLayer,
    risk_bdhi: pointLayer,
    officiel: pointLayer,
    point: pointLayer,
  };

  const ALL_PREVIEW_LAYERS = [
    landLayer,
    waterLayer,
    buildingLayer,
    contourLayer,
    waterwayLayer,
    railwayLayer,
    highwayLayer,
    pointLayer,
  ];

  function previewFeatureCount() {
    return ALL_PREVIEW_LAYERS.reduce((n, layer) => n + layer.getLayers().length, 0);
  }

  function clearPreviewLayers() {
    ALL_PREVIEW_LAYERS.forEach((layer) => layer.clearLayers());
  }

  function setOtmPreviewMode(on) {
    if (mapEl) mapEl.classList.toggle("is-otm-preview", Boolean(on));
    if (activeRaster) activeRaster.setOpacity(on ? 0.2 : 1);
  }

  function syncOverlay() {
    const detailed = Boolean(activeRaster) || previewFeatureCount() > 0;
    if (window.setOverlayForBasemap) window.setOverlayForBasemap(detailed);
  }

  function setBasemap(key) {
    Object.values(rasterLayers).forEach((layer) => {
      if (map.hasLayer(layer)) map.removeLayer(layer);
    });
    activeRaster = rasterLayers[key] || null;
    if (activeRaster) {
      activeRaster.addTo(map);
      activeRaster.setOpacity(window.previewActive ? 0.2 : 1);
    }
    if (tilesToggle) tilesToggle.checked = key === "osm";
    syncOverlay();
  }

  function setDbStatus(text, kind) {
    if (!dbStatus) return;
    dbStatus.className = kind ? `db-status is-${kind}` : "db-status";
    dbStatus.textContent = text;
  }

  async function refreshStatus() {
    try {
      const resp = await fetch("/api/status");
      const data = await resp.json();
      if (data.ok) {
        setDbStatus(`PostGIS : ${data.host}:${data.port} / ${data.database}`, "ok");
      } else {
        setDbStatus(data.error || "PostGIS hors ligne.", "error");
      }
    } catch (err) {
      setDbStatus(`PostGIS injoignable : ${err.message}`, "error");
    }
  }

  async function refreshImportList(selectSchema) {
    if (!importSelect) return;
    try {
      const resp = await fetch("/api/imports");
      const data = await resp.json();
      const current = selectSchema || importSelect.value;
      importSelect.innerHTML = '<option value="">Aucun aperçu</option>';
      for (const item of data.imports || []) {
        const option = document.createElement("option");
        option.value = item.schema;
        const n = item.counts
          ? Object.values(item.counts).reduce((s, v) => s + Number(v || 0), 0)
          : "?";
        option.textContent = `${item.schema} (${n} objets)`;
        importSelect.appendChild(option);
      }
      if (current && [...importSelect.options].some((o) => o.value === current)) {
        importSelect.value = current;
      }
    } catch (_err) {
      // La liste se remplira au prochain succès d'import.
    }
  }

  async function loadOsmPreview(schema) {
    if (!schema) {
      clearPreviewLayers();
      window.previewActive = false;
      setOtmPreviewMode(false);
      syncOverlay();
      return;
    }
    const resp = await fetch(`/api/preview/${encodeURIComponent(schema)}`);
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Aperçu impossible.");
    clearPreviewLayers();
    const buckets = new Map();
    for (const feature of data.features || []) {
      const kind = (feature.properties || {}).kind;
      const layer = KIND_TO_LAYER[kind];
      if (!layer) continue;
      if (!buckets.has(layer)) buckets.set(layer, []);
      buckets.get(layer).push(feature);
    }
    for (const [layer, features] of buckets) {
      layer.addData({ type: "FeatureCollection", features });
    }
    window.previewActive = true;
    setOtmPreviewMode(true);
    syncOverlay();
    const frameLayers = [
      landLayer,
      waterLayer,
      buildingLayer,
      contourLayer,
      waterwayLayer,
      railwayLayer,
      highwayLayer,
    ];
    const occupied = frameLayers.filter((layer) => layer.getLayers().length > 0);
    if (occupied.length) {
      const bounds = L.featureGroup(occupied).getBounds();
      if (bounds.isValid()) map.fitBounds(bounds.pad(0.08));
    }
  }

  window.loadOsmPreview = loadOsmPreview;
  window.refreshImportList = refreshImportList;
  window.previewActive = false;

  if (basemapSelect) {
    basemapSelect.addEventListener("change", () => setBasemap(basemapSelect.value));
    setBasemap(basemapSelect.value);
  } else if (tilesToggle) {
    tilesToggle.addEventListener("change", () => {
      setBasemap(tilesToggle.checked ? "osm" : "");
    });
  }

  if (importSelect) {
    importSelect.addEventListener("change", () => {
      loadOsmPreview(importSelect.value).catch((err) => {
        const statusEl = document.getElementById("extract-status");
        if (statusEl) {
          statusEl.className = "extract-status is-error";
          statusEl.textContent = err.message;
        }
      });
    });
  }

  refreshStatus();
  refreshImportList();
  setInterval(refreshStatus, 15000);
})();
