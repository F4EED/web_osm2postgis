/**
 * zone-import.js — sélection d'une zone à 4 points et import OSM → PostGIS.
 *
 * Même parcours que js/zone-extract.js de pmtiles :
 *   1. « Sélectionner une zone » ;
 *   2. 4 clics ;
 *   3. dialogue (nom, écrasement) ;
 *   4. POST /api/import → sondage /api/jobs/<id> ;
 *   5. à la fin : aperçu vectoriel du schéma importé.
 *
 * Dépendances : window.map (map.js), window.loadOsmPreview, window.refreshImportList.
 * Exporte : window.addZonePoint(latlng), window.zoneSelectActive.
 */
(() => {
  const MAX_POINTS = 4;

  const btn = document.getElementById("zone-btn");
  const cancelBtn = document.getElementById("zone-cancel");
  const hintEl = document.getElementById("zone-hint");
  const statusEl = document.getElementById("extract-status");
  const dialog = document.getElementById("extract-dialog");
  const form = document.getElementById("extract-form");
  const nameInput = document.getElementById("extract-name");
  const schemaPreview = document.getElementById("schema-preview");
  const bboxEl = document.getElementById("extract-bbox");
  const recapEl = document.getElementById("pg-recap");
  const dialogCancel = document.getElementById("extract-dialog-cancel");

  const map = window.map;
  if (!map || !btn) return;

  map.createPane("zonePane");
  map.getPane("zonePane").style.zIndex = 650;

  const drawn = L.layerGroup().addTo(map);
  let points = [];
  let selecting = false;
  let pollTimer = null;

  function setHint(text) {
    hintEl.textContent = text || "";
  }

  function setStatus(html, kind) {
    statusEl.className = kind ? `extract-status is-${kind}` : "extract-status";
    statusEl.innerHTML = html || "";
  }

  function clearDrawing() {
    drawn.clearLayers();
    points = [];
  }

  function stopSelecting() {
    selecting = false;
    window.zoneSelectActive = false;
    map.getContainer().classList.remove("is-selecting");
    btn.setAttribute("aria-pressed", "false");
    btn.textContent = "Sélectionner une zone";
    cancelBtn.hidden = true;
  }

  function startSelecting() {
    if (dialog.open) dialog.close();
    clearDrawing();
    selecting = true;
    window.zoneSelectActive = true;
    map.closePopup();
    map.getContainer().classList.add("is-selecting");
    btn.setAttribute("aria-pressed", "true");
    btn.textContent = "Sélection en cours…";
    cancelBtn.hidden = false;
    setStatus("");
    setHint(
      `Dézoomez si besoin, puis cliquez ${MAX_POINTS} points (0/${MAX_POINTS}).`
    );
  }

  function cancelSelecting() {
    stopSelecting();
    clearDrawing();
    setHint("");
  }

  function orderedRing(latlngs) {
    const lat0 = latlngs.reduce((s, p) => s + p.lat, 0) / latlngs.length;
    const lon0 = latlngs.reduce((s, p) => s + p.lng, 0) / latlngs.length;
    return [...latlngs].sort(
      (a, b) =>
        Math.atan2(a.lat - lat0, a.lng - lon0) -
        Math.atan2(b.lat - lat0, b.lng - lon0)
    );
  }

  function render() {
    drawn.clearLayers();
    points.forEach((ll, i) => {
      const icon = L.divIcon({
        className: "zone-marker",
        html: `<span>${i + 1}</span>`,
        iconSize: [22, 22],
        iconAnchor: [11, 11],
      });
      L.marker(ll, { icon, keyboard: false }).addTo(drawn);
    });
    if (points.length < 2) return;
    const ring = orderedRing(points);
    L.polygon(ring, {
      pane: "zonePane",
      color: "#1d4ed8",
      weight: 2,
      fillColor: "#3b82f6",
      fillOpacity: 0.18,
    }).addTo(drawn);
    if (points.length === MAX_POINTS) {
      const b = L.latLngBounds(points);
      L.rectangle(b, {
        pane: "zonePane",
        color: "#1e3a5f",
        weight: 1,
        dashArray: "5 4",
        fill: false,
      }).addTo(drawn);
    }
  }

  function fmt(n) {
    return n.toFixed(4);
  }

  function schemaFromInput() {
    const raw = (nameInput.value || "zone")
      .trim()
      .toLowerCase()
      .normalize("NFKD")
      .replace(/[\u0300-\u036f]/g, "")
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_+|_+$/g, "")
      .slice(0, 48);
    return raw || "zone";
  }

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  let pgInfo = { host: "127.0.0.1", port: 5433, database: "osm", user: "osm" };
  let acceptImport = false;

  function fillRecap() {
    if (!recapEl || points.length < 2) return;
    const schema = "osm_" + schemaFromInput();
    const overwrite = form.overwrite.checked;
    const b = L.latLngBounds(points);
    const conn = `${pgInfo.host}:${pgInfo.port}`;
    const schemaStep = overwrite
      ? `Supprimer le schéma <code>${escapeHtml(schema)}</code> s’il existe (<code>DROP SCHEMA … CASCADE</code>), puis le recréer.`
      : `Créer le schéma <code>${escapeHtml(schema)}</code> (refusé s’il existe déjà).`;
    recapEl.innerHTML = [
      `Connexion à PostGIS <code>${escapeHtml(conn)}</code>, base <code>${escapeHtml(pgInfo.database)}</code>, utilisateur <code>${escapeHtml(pgInfo.user)}</code>.`,
      `Télécharger les objets OpenStreetMap du rectangle <code>${fmt(b.getWest())}, ${fmt(b.getSouth())} → ${fmt(b.getEast())}, ${fmt(b.getNorth())}</code>.`,
      schemaStep,
      `Y créer les tables <code>points</code>, <code>lines</code>, <code>multilinestrings</code>, <code>multipolygons</code>, <code>other_relations</code> (colonne <code>geom</code>, index spatial GIST).`,
      `Indexer <code>highway</code>, <code>building</code>, <code>landuse</code>, <code>amenity</code> et <code>place</code>.`,
      `Enregistrer l’emprise et les effectifs dans <code>osm2postgis.imports</code>.`,
    ]
      .map((item) => `<li>${item}</li>`)
      .join("");
  }

  function cancelFromDialog() {
    acceptImport = false;
    if (dialog.open) dialog.close();
    cancelSelecting();
    setHint("Sélection annulée.");
    setStatus("");
  }

  async function openDialog() {
    const b = L.latLngBounds(points);
    bboxEl.textContent =
      `${fmt(b.getWest())}, ${fmt(b.getSouth())} → ${fmt(b.getEast())}, ${fmt(b.getNorth())}` +
      `  (${fmt(b.getEast() - b.getWest())}° × ${fmt(b.getNorth() - b.getSouth())}°)`;
    if (!nameInput.value) nameInput.value = "zone";
    if (schemaPreview) schemaPreview.textContent = "osm_" + schemaFromInput();
    acceptImport = false;
    try {
      const resp = await fetch("/api/status");
      const data = await resp.json();
      if (data.host) {
        pgInfo = {
          host: data.host,
          port: data.port,
          database: data.database,
          user: data.user,
        };
      }
    } catch (_err) {
      // Repli sur les valeurs par défaut déjà affichées.
    }
    fillRecap();
    dialog.showModal();
    nameInput.focus();
    nameInput.select();
  }

  let lastAddAt = 0;

  function addPoint(latlng) {
    if (!selecting) return;
    if (points.length >= MAX_POINTS) return;
    const now = Date.now();
    if (now - lastAddAt < 80) return;
    lastAddAt = now;
    points.push(latlng);
    render();
    if (points.length < MAX_POINTS) {
      setHint(`Cliquez ${MAX_POINTS} points sur la carte (${points.length}/${MAX_POINTS}).`);
      return;
    }
    setHint("Zone définie. Vérifiez le récapitulatif PostGIS.");
    stopSelecting();
    openDialog();
  }

  window.addZonePoint = addPoint;

  btn.addEventListener("click", () => {
    if (selecting) {
      cancelSelecting();
      return;
    }
    startSelecting();
  });
  cancelBtn.addEventListener("click", cancelSelecting);
  dialogCancel.addEventListener("click", cancelFromDialog);
  dialog.addEventListener("cancel", (ev) => {
    ev.preventDefault();
    cancelFromDialog();
  });
  dialog.addEventListener("close", () => {
    if (acceptImport) return;
    if (points.length) {
      cancelSelecting();
      setHint("Sélection annulée.");
      setStatus("");
    }
  });
  nameInput.addEventListener("input", () => {
    if (schemaPreview) schemaPreview.textContent = "osm_" + schemaFromInput();
    fillRecap();
  });
  form.overwrite.addEventListener("change", fillRecap);

  map.on("click", (e) => addPoint(e.latlng));
  map.on("popupopen", () => {
    if (window.zoneSelectActive) map.closePopup();
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && selecting) cancelSelecting();
  });

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const name = nameInput.value.trim();
    if (!name) {
      nameInput.focus();
      return;
    }
    const payload = {
      name,
      points: points.map((p) => [p.lat, p.lng]),
      overwrite: form.overwrite.checked,
    };
    acceptImport = true;
    dialog.close();
    setStatus("Lancement de l’import OSM…", "busy");
    try {
      const resp = await fetch("/api/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await resp.json();
      if (!resp.ok) {
        setStatus(data.error || "Import impossible.", "error");
        return;
      }
      pollJob(data.id);
    } catch (err) {
      setStatus(`Erreur réseau : ${err.message}`, "error");
    }
  });

  function pollJob(jobId) {
    if (pollTimer) clearInterval(pollTimer);
    const tick = async () => {
      try {
        const resp = await fetch(`/api/jobs/${jobId}`);
        const job = await resp.json();
        if (!resp.ok) {
          setStatus(job.error || "Suivi introuvable.", "error");
          clearInterval(pollTimer);
          return;
        }
        if (job.status === "queued" || job.status === "running") {
          const tail = (job.log || "").trim().split("\n").slice(-2).join(" · ");
          setStatus(
            `Import de <strong>${job.schema}</strong> en cours…` +
              (tail ? `<span class="log">${tail}</span>` : ""),
            "busy"
          );
          return;
        }
        clearInterval(pollTimer);
        if (job.status === "done") {
          const parts = Object.entries(job.counts || {})
            .map(([k, v]) => `${k} ${v}`)
            .join(", ");
          setStatus(
            `Schéma <strong>${job.schema}</strong> prêt` +
              (parts ? ` (${parts})` : "") +
              `. Ouvrez-le dans QGIS (PostGIS, base osm, schéma ${job.schema}).`,
            "ok"
          );
          if (window.refreshImportList) window.refreshImportList(job.schema);
          const select = document.getElementById("import-select");
          if (select) select.value = job.schema;
          if (window.loadOsmPreview) {
            window.loadOsmPreview(job.schema).catch((err) => {
              setStatus(`Import OK, aperçu impossible : ${err.message}`, "error");
            });
          }
          return;
        }
        setStatus(job.error || "Import échoué.", "error");
      } catch (err) {
        setStatus(`Erreur de suivi : ${err.message}`, "error");
        clearInterval(pollTimer);
      }
    };
    tick();
    pollTimer = setInterval(tick, 2000);
  }
})();
