/**
 * map.js — création de la carte Leaflet et des couches vectorielles de base.
 *
 * C'est le script fondateur du front : il instancie la carte, charge les trois
 * fichiers GeoJSON du dossier data/ (pays d'Europe, départements français,
 * villes) et publie sur `window` les objets dont les autres scripts ont besoin.
 *
 * Contrat public exposé aux scripts suivants :
 *   window.map                   instance Leaflet
 *   window.mapConfig             bornes et zooms de référence
 *   window.paysLayer / deptLayer / citiesLayer   couches GeoJSON
 *   window.setOverlayForBasemap(actif)  bascule le style selon qu'un fond
 *                                       PMTiles est affiché ou non
 *   window.basemapActive         drapeau lu par les gestionnaires de survol
 *
 * Il *consomme* par ailleurs, quand ils existent :
 *   window.PAYS_FR               table de traduction (pays-fr.js)
 *   window.zoneSelectActive      mode sélection en cours (zone-extract.js)
 *   window.addZonePoint(latlng)  ajout d'un sommet (zone-extract.js)
 *
 * Le fichier entier est enfermé dans une IIFE : aucune variable ne fuit dans
 * l'espace global, seules les exportations explicites sur `window` sont
 * visibles. Même motif dans tous les scripts maison du projet.
 */
(() => {
  // --- Cadrage géographique -------------------------------------------------
  // Emprise de l'Europe au sens large : de l'Atlantique (Açores exclues) à
  // l'Oural occidental, de la Crète au cap Nord.
  const EUROPE_BOUNDS = L.latLngBounds([34.5, -25.5], [71.5, 42.5]);
  const EUROPE_CENTER = [50.1, 9.5];
  const EUROPE_ZOOM = 4;
  const EUROPE_MIN_ZOOM = 3;
  // 18 alors que les tuiles Protomaps s'arrêtent au zoom 15 : les trois
  // derniers niveaux sont obtenus par surzoom (les tuiles du niveau 15 sont
  // agrandies). Le rendu vectoriel reste net, contrairement à des tuiles raster.
  const EUROPE_MAX_ZOOM = 18;

  // --- Styles ---------------------------------------------------------------
  // Deux jeux de styles coexistent, et `setOverlayForBasemap` bascule de l'un à
  // l'autre :
  //   - styles « pleins » (ci-dessous) quand la carte n'affiche que les
  //     contours : chaque polygone est rempli d'une couleur d'atlas ;
  //   - styles « contour » (countryOutlineStyle / deptOutlineStyle, plus bas)
  //     quand un fond PMTiles est chargé : les remplissages masqueraient le
  //     fond, seules les frontières restent tracées.
  const countryStyle = {
    color: "#4d5d6c",
    weight: 1.2,
    fillColor: "#f6f1e6",
    fillOpacity: 1,
    opacity: 1,
  };

  const franceDeptStyle = {
    color: "#7a3e14",
    weight: 1,
    fillColor: "#f3d7a4",
    fillOpacity: 1,
    opacity: 1,
  };

  // Style temporaire appliqué au polygone survolé.
  const hoverStyle = {
    fillColor: "#ffe08a",
    fillOpacity: 1,
    weight: 2,
    color: "#1c2833",
  };

  const hoverEl = document.getElementById("hover-box");

  const map = L.map("map", {
    center: EUROPE_CENTER,
    zoom: EUROPE_ZOOM,
    minZoom: EUROPE_MIN_ZOOM,
    maxZoom: EUROPE_MAX_ZOOM,
    // pad(0.2) élargit les limites de 20 % : on peut faire déborder un peu la
    // carte sans se sentir bloqué net contre un mur invisible.
    maxBounds: EUROPE_BOUNDS.pad(0.2),
    // Viscosité 0.7 : le déplacement au-delà des limites est freiné puis
    // ramené, plutôt qu'interdit brutalement (1) ou libre (0).
    maxBoundsViscosity: 0.7,
    zoomControl: true,
    attributionControl: true,
  });
  // Publication immédiate : basemap.js, zone-extract.js et meshtastic.js
  // s'interrompent s'ils ne trouvent pas window.map.
  window.map = map;

  // --- Plans de superposition (panes) ---------------------------------------
  // Leaflet empile ses couches dans des « panes » ordonnés par z-index. Les
  // valeurs par défaut utiles : tiles 200, overlays 400, markers 600,
  // popups 700. On intercale le fond PMTiles à 450, c'est-à-dire AU-DESSUS des
  // polygones GeoJSON (qui sont des overlays) mais SOUS les marqueurs.
  // Ce choix est ce qui permet au fond détaillé de couvrir les aplats de
  // couleur tout en laissant visibles villes, nœuds et points de sélection.
  map.createPane("pmtilesPane");
  map.getPane("pmtilesPane").style.zIndex = 450;

  // setPrefix("") retire la mention « Leaflet » pour ne garder que les crédits
  // des données, obligatoires du fait des licences ODbL / Natural Earth.
  map.attributionControl.setPrefix("");
  map.attributionControl.addAttribution(
    'Contours pays : Natural Earth / map-of-europe · Départements : <a href="https://github.com/gregoiredavid/france-geojson">france-geojson</a> · Villes : Natural Earth'
  );

  // Rendu Canvas plutôt que SVG : les fichiers GeoJSON contiennent plusieurs
  // dizaines de milliers de sommets. En SVG, chaque polygone deviendrait un
  // élément du DOM et le déplacement de la carte saccaderait ; le Canvas les
  // dessine tous dans une seule surface.
  // padding 0.4 = 40 % de marge dessinée hors écran, pour éviter les bords
  // blancs pendant un déplacement rapide.
  const canvas = L.canvas({ padding: 0.4 });
  const paysLayer = L.geoJSON(null, {
    renderer: canvas,
    style: countryStyle,
    onEachFeature: onEachCountry,
  }).addTo(map);

  const deptLayer = L.geoJSON(null, {
    renderer: canvas,
    style: franceDeptStyle,
    onEachFeature: onEachDepartment,
  }).addTo(map);

  // --- Couche des villes ----------------------------------------------------
  // Conservée à part (citiesData) car la couche est entièrement reconstruite à
  // chaque changement de zoom : `filter` n'est évalué qu'au moment de l'ajout
  // des données, il ne se réévalue pas tout seul.
  let citiesData = null;
  const citiesLayer = L.geoJSON(null, {
    // interactive: false → les étiquettes ne captent pas la souris, donc elles
    // n'empêchent ni le survol des départements dessous, ni les clics de
    // sélection de zone.
    interactive: false,
    /**
     * Affichage progressif selon le zoom, pour éviter un amas d'étiquettes.
     *   - capitales      : toujours visibles ;
     *   - 1re ville d'un département : à partir du zoom 8 ;
     *   - 2e ville       : à partir du zoom 10 ;
     *   - autres villes  : à partir du zoom 6.
     */
    filter(feature) {
      const z = map.getZoom();
      if (feature.properties.capital) return true;
      const deptRank = feature.properties.deptRank;
      if (deptRank === 1) return z >= 8;
      if (deptRank === 2) return z >= 10;
      return z >= 6;
    },
    /**
     * Rend chaque ville comme un divIcon (pastille + nom) plutôt qu'un marqueur
     * image : c'est du HTML stylable en CSS, donc le halo blanc du texte et la
     * taille des pastilles se règlent dans app.css sans toucher au JavaScript.
     */
    pointToLayer(feature, latlng) {
      const capital = feature.properties.capital;
      const name = feature.properties.name;
      const secondary = feature.properties.deptRank === 2;
      const cls = [
        "city-label",
        capital ? "is-capital" : "",
        secondary ? "is-secondary" : "",
      ]
        .filter(Boolean)
        .join(" ");
      return L.marker(latlng, {
        interactive: false,
        keyboard: false,
        icon: L.divIcon({
          className: cls,
          html: `<span class="city-dot"></span><span class="city-name">${name}</span>`,
          // Taille nulle : c'est le contenu HTML qui dimensionne l'étiquette,
          // sinon Leaflet réserverait un rectangle fixe.
          iconSize: [0, 0],
          iconAnchor: [4, 5],
        }),
      });
    },
  });

  /** Reconstruit la couche des villes pour réévaluer le filtre de zoom. */
  function refreshCities() {
    if (!citiesData) return;
    citiesLayer.clearLayers();
    citiesLayer.addData(citiesData);
  }

  map.on("zoomend", refreshCities);

  // Contrôle de couches, en bas à gauche. Premier argument `null` : aucun fond
  // de carte exclusif (le fond PMTiles a son propre menu dans le panneau), on
  // ne déclare que des surcouches cochables indépendamment.
  L.control
    .layers(
      null,
      {
        Pays: paysLayer,
        "Départements FR": deptLayer,
        "Villes principales": citiesLayer,
      },
      { collapsed: false, position: "bottomleft" }
    )
    .addTo(map);

  // Styles « contour seul », appliqués quand un fond PMTiles est visible.
  const countryOutlineStyle = {
    color: "#334155",
    weight: 1.2,
    fillOpacity: 0, // transparent : le fond détaillé apparaît au travers
    opacity: 0.85,
  };
  const deptOutlineStyle = {
    color: "#7a3e14",
    weight: 1.1,
    fillOpacity: 0,
    opacity: 0.9,
  };

  // --- Exports vers les autres scripts --------------------------------------
  window.mapConfig = {
    EUROPE_BOUNDS,
    EUROPE_CENTER,
    EUROPE_ZOOM,
    EUROPE_MIN_ZOOM,
    EUROPE_MAX_ZOOM,
  };
  window.paysLayer = paysLayer;
  window.deptLayer = deptLayer;
  window.citiesLayer = citiesLayer;

  /**
   * Adapte l'habillage vectoriel à la présence ou non d'un fond PMTiles.
   *
   * Appelée par basemap.js au chargement/déchargement d'une archive, mais aussi
   * à chaque changement de zoom : sous le zoom minimal de l'archive, il n'y a
   * aucune tuile à afficher, on repasse donc temporairement aux aplats pour ne
   * pas laisser une carte vide.
   *
   * @param {boolean} on true si un fond détaillé est visible à ce zoom.
   */
  window.setOverlayForBasemap = function setOverlayForBasemap(on) {
    window.basemapActive = on;
    paysLayer.setStyle(on ? countryOutlineStyle : countryStyle);
    deptLayer.setStyle(on ? deptOutlineStyle : franceDeptStyle);
    if (on) {
      // Le fond PMTiles a déjà ses propres noms de villes : garder les nôtres
      // produirait des étiquettes en double.
      if (map.hasLayer(citiesLayer)) map.removeLayer(citiesLayer);
    } else if (!map.hasLayer(citiesLayer)) {
      citiesLayer.addTo(map);
      refreshCities();
    }
  };

  /** Nom français d'un pays, avec repli sur le nom anglais puis le code ISO. */
  function countryLabel(props) {
    const iso = props.ISO2;
    return window.PAYS_FR[iso] || props.NAME || iso;
  }

  /** Écrit dans l'encart de survol, ou y remet l'invite par défaut si vide. */
  function setHover(text) {
    hoverEl.innerHTML = text
      ? text
      : '<span class="hint">Survolez un pays ou un département</span>';
  }

  /**
   * Met un polygone en évidence, et programme le retour à son style normal.
   *
   * `once("mouseout")` plutôt qu'un `on` permanent : l'écouteur se retire de
   * lui-même après usage, ce qui évite d'en accumuler un par survol sur
   * plusieurs milliers de polygones.
   *
   * @param {L.Path} layer polygone survolé
   * @param {object} baseStyle style à restaurer à la sortie du curseur
   */
  function highlight(layer, baseStyle) {
    layer.setStyle(hoverStyle);
    layer.bringToFront();
    layer.once("mouseout", () => layer.setStyle(baseStyle));
  }

  /**
   * Attache popup et interactions à un polygone de pays.
   *
   * Le test `window.zoneSelectActive` apparaît dans chaque gestionnaire : quand
   * l'utilisateur est en train de placer les sommets d'une zone, les clics
   * doivent servir à poser un sommet, pas à ouvrir une popup, et le survol ne
   * doit pas repeindre la carte.
   */
  function onEachCountry(feature, layer) {
    const name = countryLabel(feature.properties);
    layer.bindPopup(
      `<strong>${name}</strong><span class="meta">Pays · ${feature.properties.ISO2}</span>`
    );
    layer.on({
      click(e) {
        if (window.zoneSelectActive) {
          L.DomEvent.stop(e);
          layer.closePopup();
          if (window.addZonePoint) window.addZonePoint(e.latlng);
        }
      },
      mouseover() {
        if (window.zoneSelectActive) return;
        // Le style à restaurer dépend du mode d'affichage courant.
        highlight(layer, window.basemapActive ? countryOutlineStyle : countryStyle);
        setHover(name);
      },
      mouseout() {
        setHover("");
      },
    });
  }

  /** Même logique que onEachCountry, pour un département français. */
  function onEachDepartment(feature, layer) {
    const { code, nom } = feature.properties;
    const title = `${nom} (${code})`;
    layer.bindPopup(
      `<strong>${nom}</strong><span class="meta">Département ${code} · France</span>`
    );
    layer.on({
      click(e) {
        if (window.zoneSelectActive) {
          L.DomEvent.stop(e);
          layer.closePopup();
          if (window.addZonePoint) window.addZonePoint(e.latlng);
        }
      },
      mouseover() {
        if (window.zoneSelectActive) return;
        highlight(layer, window.basemapActive ? deptOutlineStyle : franceDeptStyle);
        setHover(title);
      },
      mouseout() {
        setHover("");
      },
    });
  }

  setHover("");

  // --- Chargement des données -----------------------------------------------
  // Promise.all : les trois fichiers sont téléchargés en parallèle et injectés
  // ensemble, ce qui évite un affichage en escalier où les départements
  // apparaîtraient plusieurs secondes après les pays.
  // Total d'environ 2,2 Mo, servis en local : le chargement est quasi instantané.
  Promise.all([
    fetch("data/europe-countries.geojson").then((r) => {
      if (!r.ok) throw new Error("Pays introuvables");
      return r.json();
    }),
    fetch("data/france-departements.geojson").then((r) => {
      if (!r.ok) throw new Error("Départements introuvables");
      return r.json();
    }),
    fetch("data/europe-villes.geojson").then((r) => {
      if (!r.ok) throw new Error("Villes introuvables");
      return r.json();
    }),
  ])
    .then(([countries, departments, cities]) => {
      paysLayer.addData(countries);
      deptLayer.addData(departments);
      citiesData = cities;
      refreshCities();
      // basemap.js peut avoir déjà chargé une archive pendant ces requêtes :
      // on réapplique alors le style « contour » aux données fraîchement
      // ajoutées, qui sont nées avec le style plein.
      if (window.basemapActive) window.setOverlayForBasemap(true);
      else citiesLayer.addTo(map);
    })
    .catch((err) => {
      // Cause la plus fréquente : page ouverte en file:// au lieu d'être servie
      // par serve.py, ce qui fait échouer tous les fetch.
      hoverEl.textContent = `Erreur de chargement : ${err.message}`;
    });
})();
