/**
 * pays-fr.js — noms français des pays d'Europe.
 *
 * Le fichier `data/europe-countries.geojson` ne porte que des noms anglais
 * (« Germany », « Spain »…) et un code ISO 3166-1 alpha-2. Cette table sert à
 * afficher des noms français dans les popups et l'encart de survol.
 *
 * Utilisée par `js/map.js`, fonction `countryLabel()`, qui retombe sur le nom
 * anglais du GeoJSON puis sur le code ISO si la clé est absente. Ajouter un
 * pays revient donc simplement à ajouter une ligne ici.
 *
 * Clé = code ISO 3166-1 alpha-2 (propriété ISO2 du GeoJSON).
 */
window.PAYS_FR = {
  AD: "Andorre",
  AL: "Albanie",
  AM: "Arménie",
  AT: "Autriche",
  AZ: "Azerbaïdjan",
  BA: "Bosnie-Herzégovine",
  BE: "Belgique",
  BG: "Bulgarie",
  BY: "Biélorussie",
  CH: "Suisse",
  CY: "Chypre",
  CZ: "Tchéquie",
  DE: "Allemagne",
  DK: "Danemark",
  EE: "Estonie",
  ES: "Espagne",
  FI: "Finlande",
  FO: "Îles Féroé",
  FR: "France",
  GB: "Royaume-Uni",
  GE: "Géorgie",
  GR: "Grèce",
  HR: "Croatie",
  HU: "Hongrie",
  IE: "Irlande",
  IL: "Israël",
  IS: "Islande",
  IT: "Italie",
  LI: "Liechtenstein",
  LT: "Lituanie",
  LU: "Luxembourg",
  LV: "Lettonie",
  MC: "Monaco",
  MD: "Moldavie",
  ME: "Monténégro",
  MK: "Macédoine du Nord",
  MT: "Malte",
  NL: "Pays-Bas",
  NO: "Norvège",
  PL: "Pologne",
  PT: "Portugal",
  RO: "Roumanie",
  RS: "Serbie",
  RU: "Russie",
  SE: "Suède",
  SI: "Slovénie",
  SK: "Slovaquie",
  SM: "Saint-Marin",
  TR: "Turquie",
  UA: "Ukraine",
  VA: "Vatican",
};
