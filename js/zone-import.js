/**
 * zone-import.js — sélection d'un polygone (3 sommets ou plus) et import OSM → PostGIS.
 *
 * Parcours :
 *   1. « Sélectionner une zone » ;
 *   2. clics successifs autour de l'emprise (ordre = contour) ;
 *   3. Terminer / double-clic / Entrée (à partir de 3 points) ;
 *   4. dialogue (nom, écrasement) ;
 *   5. POST /api/import → sondage /api/jobs/<id> ;
 *   6. à la fin : aperçu vectoriel du schéma importé.
 *
 * Dépendances : window.map (map.js), window.loadOsmPreview, window.refreshImportList.
 * Exporte : window.addZonePoint(latlng), window.zoneSelectActive.
 */
(() => {
  const MIN_POINTS = 3;
  const MAX_POINTS = 64;

  const btn = document.getElementById("zone-btn");
  const doneBtn = document.getElementById("zone-done");
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
    map.doubleClickZoom.enable();
    btn.setAttribute("aria-pressed", "false");
    btn.textContent = "Sélectionner une zone";
    if (doneBtn) doneBtn.hidden = true;
    cancelBtn.hidden = true;
  }

  function startSelecting() {
    if (dialog.open) dialog.close();
    clearDrawing();
    selecting = true;
    window.zoneSelectActive = true;
    map.closePopup();
    map.getContainer().classList.add("is-selecting");
    map.doubleClickZoom.disable();
    btn.setAttribute("aria-pressed", "true");
    btn.textContent = "Sélection en cours…";
    cancelBtn.hidden = false;
    if (doneBtn) doneBtn.hidden = true;
    setStatus("");
    setHint(
      `Cliquez autour de la zone, dans l’ordre (au moins ${MIN_POINTS} points). ` +
        "Double-clic ou « Terminer » pour valider."
    );
  }

  function cancelSelecting() {
    stopSelecting();
    clearDrawing();
    setHint("");
  }

  function closeTo(a, b, px = 14) {
    return map.latLngToLayerPoint(a).distanceTo(map.latLngToLayerPoint(b)) < px;
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
    if (points.length === 2) {
      L.polyline(points, {
        pane: "zonePane",
        color: "#1d4ed8",
        weight: 2,
      }).addTo(drawn);
      return;
    }
    if (points.length < 2) return;
    L.polygon(points, {
      pane: "zonePane",
      color: "#1d4ed8",
      weight: 2,
      fillColor: "#3b82f6",
      fillOpacity: 0.18,
    }).addTo(drawn);
  }

  function updateHints() {
    if (doneBtn) doneBtn.hidden = !selecting || points.length < MIN_POINTS;
    if (!selecting) return;
    if (points.length < MIN_POINTS) {
      const left = MIN_POINTS - points.length;
      setHint(
        `${points.length} point${points.length > 1 ? "s" : ""}. ` +
          `Encore ${left} pour former un polygone, puis « Terminer ».`
      );
      return;
    }
    if (points.length >= MAX_POINTS) {
      setHint(`Maximum ${MAX_POINTS} sommets atteint. Validez la zone.`);
      return;
    }
    setHint(
      `${points.length} sommets. Continuez, ou « Terminer » / double-clic pour valider.`
    );
  }

  function finishZone() {
    if (!selecting) return;
    if (points.length < MIN_POINTS) {
      setHint(`Il faut au moins ${MIN_POINTS} points pour délimiter la zone.`);
      return;
    }
    setHint("Zone définie. Vérifiez le récapitulatif PostGIS.");
    stopSelecting();
    openDialog();
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

  let pgInfo = { host: "127.0.0.1", port: 5432, database: "gmc", user: "gmc" };
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
      `Télécharger les objets OpenStreetMap du polygone (${points.length} sommets), emprise <code>${fmt(b.getWest())}, ${fmt(b.getSouth())} → ${fmt(b.getEast())}, ${fmt(b.getNorth())}</code>.`,
      schemaStep,
      `Y créer les tables <code>points</code>, <code>lines</code>, <code>multilinestrings</code>, <code>multipolygons</code>, <code>other_relations</code> (colonne <code>geom</code>, index spatial GIST).`,
      form.contours && form.contours.checked
        ? `Calculer les <strong>courbes de niveau</strong> (SRTM / OpenTopoMap) dans <code>${escapeHtml(schema)}.contours</code> (attribut <code>elev</code>).`
        : `Ne pas importer les courbes de niveau.`,
      form.maritime && form.maritime.checked
        ? `Télécharger les <strong>données maritimes SHOM</strong> (limite terre-mer IGN-SHOM, épaves, feux, bouées, câbles, 3 milles, natures de fond) dans des tables <code>shom_*</code>. Pas pour la navigation.`
        : `Ne pas importer les données SHOM.`,
      form.risques && form.risques.checked
        ? `Télécharger <strong>risques et mémoire des crises</strong> (Géorisques : cavités, mouvements de terrain, PPR inondation/submersion, argiles, TRI, zonage sismique, intensités SIS ; arrêtés CATNAT ; DICRIM ; fiches de synthèse BDHI) dans des tables <code>risk_*</code>. Informel, pas un PPR officiel.`
        : `Ne pas importer les zonages de risque, les CATNAT ni les DICRIM.`,
      `Générer les carroyages <strong>DFCI</strong> (<code>grid_dfci</code>, style QGIS) et <strong>UTM</strong> (<code>grid_utm</code>).`,
      `Extraire les <strong>bâtiments officiels</strong> OSM (hôpitaux, cliniques, pompiers, police, gendarmerie, mairie, préfecture, etc.) dans <code>officiels</code>, stylés par appartenance.`,
      `Indexer <code>highway</code>, <code>building</code>, <code>landuse</code>, <code>amenity</code> et <code>place</code>.`,
      `Enregistrer l’emprise et les effectifs dans <code>osm2postgis.imports</code>.`,
      `Enregistrer le style QGIS (OpenTopoMap) dans <code>public.layer_styles</code> : il s’applique à l’ajout des couches.`,
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
      `${points.length} sommets · ${fmt(b.getWest())}, ${fmt(b.getSouth())} → ${fmt(b.getEast())}, ${fmt(b.getNorth())}` +
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
    const now = Date.now();
    if (now - lastAddAt < 80) return;
    if (points.length >= MIN_POINTS && closeTo(points[0], latlng)) {
      lastAddAt = now;
      finishZone();
      return;
    }
    if (points.length >= MAX_POINTS) {
      setHint(`Maximum ${MAX_POINTS} sommets. Cliquez « Terminer » pour valider.`);
      return;
    }
    lastAddAt = now;
    points.push(latlng);
    render();
    if (points.length >= MAX_POINTS) {
      updateHints();
      finishZone();
      return;
    }
    updateHints();
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
  if (doneBtn) doneBtn.addEventListener("click", finishZone);
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
  if (form.contours) form.contours.addEventListener("change", fillRecap);
  if (form.maritime) form.maritime.addEventListener("change", fillRecap);
  if (form.risques) form.risques.addEventListener("change", fillRecap);

  map.on("click", (e) => addPoint(e.latlng));
  map.on("dblclick", (e) => {
    if (!selecting) return;
    L.DomEvent.stop(e);
    finishZone();
  });
  map.on("popupopen", () => {
    if (window.zoneSelectActive) map.closePopup();
  });
  document.addEventListener("keydown", (ev) => {
    if (!selecting) return;
    if (ev.key === "Escape") {
      cancelSelecting();
      return;
    }
    if (ev.key === "Enter") {
      ev.preventDefault();
      finishZone();
      return;
    }
    if (ev.key === "Backspace") {
      ev.preventDefault();
      if (!points.length) return;
      points.pop();
      render();
      updateHints();
    }
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
      contours: Boolean(form.contours && form.contours.checked),
      maritime: Boolean(form.maritime && form.maritime.checked),
      risques: Boolean(form.risques && form.risques.checked),
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
