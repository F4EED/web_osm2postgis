/**
 * basemap.js — fond OSM en ligne, liste des schémas PostGIS, aperçu vectoriel.
 *
 * Dépendances : window.map, window.setOverlayForBasemap (map.js).
 * Exporte : window.loadOsmPreview(schema), window.refreshImportList(),
 *           window.previewActive.
 */
(() => {
  const map = window.map;
  const tilesToggle = document.getElementById("osm-tiles");
  const importSelect = document.getElementById("import-select");
  const dbStatus = document.getElementById("db-status");
  if (!map) return;

  map.createPane("osmRasterPane");
  map.getPane("osmRasterPane").style.zIndex = 440;
  map.createPane("osmPreviewPane");
  map.getPane("osmPreviewPane").style.zIndex = 460;

  const osmTiles = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    pane: "osmRasterPane",
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  });

  const previewLayer = L.geoJSON(null, {
    pane: "osmPreviewPane",
    style(feature) {
      const kind = feature.properties && feature.properties.kind;
      if (kind === "area") {
        return {
          color: "#9a3412",
          weight: 1,
          fillColor: "#fdba74",
          fillOpacity: 0.35,
        };
      }
      return {
        color: "#1e3a5f",
        weight: 1.4,
        opacity: 0.85,
      };
    },
    pointToLayer(_feature, latlng) {
      return L.marker(latlng, {
        pane: "osmPreviewPane",
        keyboard: false,
        icon: L.divIcon({
          className: "osm-preview-point",
          html: "<span></span>",
          iconSize: [8, 8],
          iconAnchor: [4, 4],
        }),
      });
    },
    onEachFeature(feature, layer) {
      const p = feature.properties || {};
      const title = p.name || p.subtype || p.kind || "objet OSM";
      layer.bindPopup(
        `<strong>${title}</strong><span class="meta">${p.kind || ""} ${p.subtype || ""}</span>`
      );
    },
  }).addTo(map);

  function syncOverlay() {
    const detailed = map.hasLayer(osmTiles) || previewLayer.getLayers().length > 0;
    if (window.setOverlayForBasemap) window.setOverlayForBasemap(detailed);
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
      previewLayer.clearLayers();
      window.previewActive = false;
      syncOverlay();
      return;
    }
    const resp = await fetch(`/api/preview/${encodeURIComponent(schema)}`);
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Aperçu impossible.");
    previewLayer.clearLayers();
    previewLayer.addData(data);
    window.previewActive = true;
    syncOverlay();
    const bounds = previewLayer.getBounds();
    if (bounds.isValid()) map.fitBounds(bounds.pad(0.08));
  }

  window.loadOsmPreview = loadOsmPreview;
  window.refreshImportList = refreshImportList;
  window.previewActive = false;

  if (tilesToggle) {
    tilesToggle.addEventListener("change", () => {
      if (tilesToggle.checked) osmTiles.addTo(map);
      else map.removeLayer(osmTiles);
      syncOverlay();
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
