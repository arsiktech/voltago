#!/usr/bin/env node
/* Geolocation distance/ETA browser contract for Volta (React SPA).
 *
 * Verifies the Locate flow does more than render: browser geolocation permission is
 * granted, clicking "Use my location" reloads /api/chargers keyed by the user's real
 * coordinates (lat/lon, non-overview), every API row gains distance_km +
 * drive_eta_min_est, and the rendered cards flip from "Locate to see ETA" to honest
 * "X km · Y min drive" facts while preserving fail-closed nearby ranking: safer
 * evidence tiers first, then ascending distance inside each tier.
 *
 * Known SPA gaps vs the retired static shell (reported in output, not failed):
 *  - No "Trip: ~N min total / drive + verify charge" charge-time estimate facts.
 *  - No guarded style on the maps link for check/verify cards (decision pill +
 *    reason tooltip carry that caution instead).
 *  - Locate fetch no longer sends force=true (server cache TTL governs freshness).
 *
 * Usage: node scripts/qa_geolocation_distance.js <baseUrl>
 */
const { chromium } = require("playwright");

const baseUrl = (process.argv[2] || process.env.VOLTA_QA_BASE_URL || "http://127.0.0.1:8773").replace(/\/$/, "");
const chromePath = process.env.VOLTA_QA_CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const screenshot = process.env.VOLTA_QA_GEO_SCREENSHOT || "/tmp/volta-geolocation-distance.png";
const userLocation = {
  latitude: Number(process.env.VOLTA_QA_USER_LAT || "39.47"),
  longitude: Number(process.env.VOLTA_QA_USER_LON || "-0.377"),
};

const APP_GAPS = [
  "SPA does not render charge-time trip estimates ('Trip: ~N min total' / 'drive + verify charge') that the retired shell asserted",
  "SPA has no guarded state on the Open in Maps link for check/verify top cards; caution is expressed via the decision pill label + reason tooltip",
  "SPA locate fetch does not send force=true; freshness is governed by the server-side live cache TTL",
];

const DECISION_LEVELS = new Set(["go", "check", "verify", "lead", "avoid"]);

function decisionLevel(row) {
  const level = row?.decision?.level;
  if (DECISION_LEVELS.has(level)) return level;
  return row?.lead ? "verify" : "check";
}

function sourceTier(row) {
  if (!row?.lead) return "live";
  const reliability = String(row.reliability || "").toLowerCase();
  if (reliability.startsWith("official")) return "official";
  if (/directory|map|locator/.test(reliability)) return "directory";
  return "community";
}

function isRouteReady(row) {
  const available = Number(row?.connector_summary?.available ?? row?.available ?? 0);
  return !row?.lead && decisionLevel(row) === "go" && Number.isFinite(available) && available > 0;
}

// Mirrors server.py nearby_sort_key inside each separately returned API family.
function apiSafetyTier(row) {
  if (isRouteReady(row)) return 0;
  return { go: 1, check: 1, verify: 2, lead: 3, avoid: 4 }[String(row?.decision?.level || "")] ?? 5;
}

// Mirrors frontend/src/lib/format.ts sortNearbyBySafetyAndDistance after stations + leads merge.
function frontendSafetyTier(row) {
  if (isRouteReady(row)) return 0;
  const decision = decisionLevel(row);
  if (!row.lead && decision === "check") return 1;
  if (decision === "avoid") return 5;
  const source = sourceTier(row);
  if (!row.lead || row.verification?.route_checked === true || source === "official" || source === "directory") {
    return 2;
  }
  return 3;
}

function haversineKm(row, origin) {
  const lat = Number(row?.lat);
  const lon = Number(row?.lon);
  if (![lat, lon, origin.latitude, origin.longitude].every(Number.isFinite)) return Number.POSITIVE_INFINITY;
  const toRadians = (value) => (value * Math.PI) / 180;
  const dLat = toRadians(lat - origin.latitude);
  const dLon = toRadians(lon - origin.longitude);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRadians(origin.latitude)) * Math.cos(toRadians(lat)) * Math.sin(dLon / 2) ** 2;
  return 6371 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function safetyDistanceViolations(rows, tierFor, distanceFor) {
  const violations = [];
  for (let i = 1; i < rows.length; i += 1) {
    const before = rows[i - 1];
    const after = rows[i];
    const beforeTier = tierFor(before);
    const afterTier = tierFor(after);
    const beforeDistance = distanceFor(before);
    const afterDistance = distanceFor(after);
    if (afterTier < beforeTier || (afterTier === beforeTier && afterDistance + 1e-6 < beforeDistance)) {
      violations.push({
        before: { name: before.name || before.api?.name || before.dom?.name || "?", tier: beforeTier, distanceKm: beforeDistance },
        after: { name: after.name || after.api?.name || after.dom?.name || "?", tier: afterTier, distanceKm: afterDistance },
      });
    }
  }
  return violations;
}

// Collected in page context on the virtualized list (only on-screen cards exist in DOM).
// NOTE: no trailing \b after "drive" — textContent concatenates sibling spans without
// whitespace ("…min driveRetail/customer parking…"), which would defeat the boundary.
function collectCards() {
  const driveRe = /\b\d+(?:\.\d+)?\s*(?:km|m)\s*·\s*(?:\d+h(?:\s*\d+m)?|\d+\s*min)\s+drive/i;
  const locateRe = /Locate to see ETA/i;
  const wrappers = [...document.querySelectorAll("div[data-index]")]
    .filter((w) => w.querySelector("[data-station]"))
    .sort((a, b) => Number(a.dataset.index) - Number(b.dataset.index));
  const rows = wrappers.map((w) => {
    const holder = w.querySelector("[data-station]");
    const card = holder.querySelector("article") || holder;
    const spanTexts = [...card.querySelectorAll("span")].map((s) =>
      (s.textContent || "").replace(/\s+/g, " ").trim(),
    );
    const distance = card.querySelector("[data-distance]");
    const distanceVisible = Boolean(distance?.getClientRects().length && getComputedStyle(distance).visibility !== "hidden");
    const pill = card.querySelector(".pill");
    const links = [...card.querySelectorAll("a")];
    const mapsLink = links.find((a) => /open in maps/i.test(a.textContent || ""));
    const sourceLink = links.find((a) => /^Start\/check/i.test((a.textContent || "").trim()));
    const details = [...card.querySelectorAll("button")].find((b) => b.hasAttribute("aria-expanded"));
    return {
      index: Number(w.dataset.index),
      id: holder.getAttribute("data-station") || "",
      name: card.querySelector("h3")?.textContent.trim() || "",
      driveFact: spanTexts.find((t) => driveRe.test(t)) || "",
      hasDriveFact: spanTexts.some((t) => driveRe.test(t)),
      hasLocateFact: spanTexts.some((t) => locateRe.test(t)),
      distanceFact: distanceVisible ? distance.getAttribute("data-distance") : "",
      hasDistanceFact: distanceVisible,
      pillClass: pill ? [...pill.classList].find((c) => c.startsWith("pill-")) || "" : "",
      pillLabel: pill ? (pill.textContent || "").replace(/\s+/g, " ").trim() : "",
      pillTitle: pill?.getAttribute("title") || "",
      mapsHref: mapsLink ? mapsLink.href : "",
      sourceHref: sourceLink ? sourceLink.href : "",
      hasDetails: Boolean(details),
    };
  });
  return {
    cards: rows.length,
    driveFactCards: rows.filter((r) => r.hasDriveFact).length,
    locateFactCards: rows.filter((r) => r.hasLocateFact).length,
    distanceFactCards: rows.filter((r) => r.hasDistanceFact).length,
    first: rows[0] || null,
    rows,
    headerH1: document.querySelector("section h1")?.textContent.replace(/\s+/g, " ").trim() || "",
    overflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
  };
}

async function main() {
  const logs = [];
  const requests = [];
  const responses = [];
  const failures = [];
  const browser = await chromium.launch({ headless: true, executablePath: chromePath });
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    isMobile: true,
    geolocation: userLocation,
  });
  await context.grantPermissions(["geolocation"], { origin: baseUrl });
  const page = await context.newPage();

  // MapLibre's internal animation loop throws under headless Chrome during the locate
  // flyTo (null transform matrices / double-run render loop). The map is a backdrop
  // enhancement per docs/spa-dom-contract.md — the panel contract this script protects
  // is unaffected — so those specific internal errors are recorded transparently but
  // excluded from the console gate. Everything else stays fatal.
  const mapLibreInternalErrors = [];
  page.on("console", (msg) => {
    const line = `${msg.type()}: ${msg.text()}`;
    // Same MapLibre-internal policy as the pageerror gate below: the flyTo crash also
    // surfaces as a console error whose stack frames all live in the maplibre chunk.
    // Excluded ONLY when every /assets/*.js frame is maplibre-*; any app-chunk frame
    // (index-*.js etc.) keeps the error fatal. Recorded in mapLibreInternalErrors.
    if (msg.type() === "error") {
      const bundles = msg.text().match(/\/assets\/[\w.-]+\.js/g) || [];
      if (bundles.length && bundles.every((b) => b.startsWith("/assets/maplibre-"))) {
        mapLibreInternalErrors.push(line);
        return;
      }
    }
    logs.push(line);
  });
  page.on("pageerror", (err) => {
    if (/maplibre/i.test(err.stack || "")) {
      mapLibreInternalErrors.push(err.message);
      return;
    }
    logs.push(`pageerror: ${err.message}`);
  });
  page.on("request", (request) => {
    const url = request.url();
    if (url.includes("/api/chargers")) requests.push(url);
  });
  page.on("response", (response) => {
    const url = response.url();
    if (url.includes("/api/chargers")) responses.push({ url, status: response.status() });
  });

  await page.goto(`${baseUrl}/`, { waitUntil: "domcontentloaded", timeout: 20000 });
  // Default open = national leads-only overview; wait for the virtualized list to paint.
  await page.waitForSelector("[data-station] article", { timeout: 25000 });
  const before = await page.evaluate(collectCards);

  // The locate fetch is the first successful non-overview /api/chargers call carrying lat/lon.
  const locatedResponsePromise = page.waitForResponse(
    (response) => {
      if (!response.url().includes("/api/chargers") || response.status() !== 200) return false;
      const url = new URL(response.url());
      return (
        url.searchParams.has("lat") &&
        url.searchParams.has("lon") &&
        url.searchParams.get("overview") !== "true"
      );
    },
    { timeout: 45000 },
  );

  await page.click('button[aria-label="Use my location"]');
  const locatedResponse = await locatedResponsePromise;
  const locatedUrl = new URL(locatedResponse.url());
  const locatedJson = await locatedResponse.json();

  // Intelligence-first render keeps instant leads (no distance) up while the live fetch
  // runs; after the located response lands every rendered card must gain drive facts.
  // Match at SPAN level (same as collectCards): holder.textContent concatenates sibling
  // spans without whitespace ("up to 22 kW23 km · 44 min drive"), which destroys the
  // leading \b for integer-km distances and made this wait miss real drive facts.
  await page.waitForFunction(
    () => {
      const holders = [...document.querySelectorAll("[data-station]")];
      if (!holders.length) return false;
      const driveRe = /\b\d+(?:\.\d+)?\s*(?:km|m)\s*·\s*(?:\d+h(?:\s*\d+m)?|\d+\s*min)\s+drive/i;
      return holders.every((h) => {
        if (/Locate to see ETA/i.test(h.textContent || "")) return false;
        return [...h.querySelectorAll("span")].some((s) =>
          driveRe.test((s.textContent || "").replace(/\s+/g, " ").trim()),
        );
      });
    },
    null,
    { timeout: 30000 },
  );
  const after = await page.evaluate(collectCards);
  await page.screenshot({ path: screenshot, fullPage: true });

  await browser.close();

  const apiStations = locatedJson.stations || [];
  const apiLeads = locatedJson.leads || [];
  const apiRows = [...apiStations, ...apiLeads];
  const apiDistanceRows = apiRows.filter(
    (row) => Number.isFinite(Number(row.distance_km)) && Number.isFinite(Number(row.drive_eta_min_est)),
  );
  const stationOrderViolations = safetyDistanceViolations(
    apiStations,
    apiSafetyTier,
    (row) => Number(row.distance_km),
  );
  const leadOrderViolations = safetyDistanceViolations(
    apiLeads,
    apiSafetyTier,
    (row) => Number(row.distance_km),
  );

  // The frontend intentionally merges live rows and leads, reclassifies them into one
  // safety ladder, and sorts by geometric distance from the chosen origin. Filters may
  // remove rows, but the remaining rendered subset must still satisfy that ladder.
  const apiById = new Map(apiRows.map((row) => [String(row.id), row]));
  const unknownDomIds = after.rows.filter((row) => !apiById.has(row.id)).map((row) => row.id);
  const matchedDomRows = after.rows
    .filter((dom) => apiById.has(dom.id))
    .map((dom) => ({ dom, api: apiById.get(dom.id) }));
  const domOrderViolations = safetyDistanceViolations(
    matchedDomRows,
    (row) => frontendSafetyTier(row.api),
    (row) => haversineKm(row.api, userLocation),
  );
  const failOpenDomRows = matchedDomRows.filter(
    (row) => row.dom.pillClass === "pill-go" && !isRouteReady(row.api),
  );
  const renderedRouteReady = matchedDomRows.filter((row) => isRouteReady(row.api));
  const routeReadyPrefixBroken = renderedRouteReady.length > 0 && !isRouteReady(matchedDomRows[0]?.api);
  const malformedApiGoRows = apiRows.filter((row) => decisionLevel(row) === "go" && !isRouteReady(row));

  const queryLat = Number(locatedUrl.searchParams.get("lat"));
  const queryLon = Number(locatedUrl.searchParams.get("lon"));
  const unexpectedLogs = logs.filter((line) => /^error:|^pageerror:/.test(line));
  const first = after.first;

  // --- Before locating: the app must not claim any distance/ETA it cannot know. ---
  if (!before.cards) failures.push("overview render has no station cards before locating");
  if (before.driveFactCards !== 0) failures.push(`distance/drive facts were visible before locating: ${before.driveFactCards}`);
  if (before.distanceFactCards !== 0) failures.push(`distance facts were visible before locating: ${before.distanceFactCards}`);
  if (before.locateFactCards !== before.cards) {
    failures.push(`route facts did not start as 'Locate to see ETA' before locating: ${before.locateFactCards}/${before.cards}`);
  }

  // --- The located request must be keyed by the user's real (mocked) position. ---
  if (!Number.isFinite(queryLat) || !Number.isFinite(queryLon)) failures.push("located API request did not include numeric lat/lon");
  if (Math.abs(queryLat - userLocation.latitude) > 0.0001 || Math.abs(queryLon - userLocation.longitude) > 0.0001) {
    failures.push(`located API query lat/lon did not match mocked user location: ${queryLat},${queryLon}`);
  }
  if (locatedUrl.searchParams.get("overview") === "true") failures.push("located API request stayed in overview mode");

  // --- Every located API row must expose distance + drive-ETA estimates. ---
  if (!apiRows.length) failures.push("located API response returned no stations/leads");
  if (apiDistanceRows.length !== apiRows.length) {
    failures.push(`not all located API rows had distance_km and drive_eta_min_est: ${apiDistanceRows.length}/${apiRows.length}`);
  }
  if (stationOrderViolations.length) {
    failures.push(`located API stations violate safety-tier then distance order: ${stationOrderViolations.slice(0, 2).map((v) => `${v.before.name} -> ${v.after.name}`).join(" | ")}`);
  }
  if (leadOrderViolations.length) {
    failures.push(`located API leads violate safety-tier then distance order: ${leadOrderViolations.slice(0, 2).map((v) => `${v.before.name} -> ${v.after.name}`).join(" | ")}`);
  }
  if (malformedApiGoRows.length) {
    failures.push(`located API exposes Go now without live availability (fail-open): ${malformedApiGoRows.slice(0, 3).map((row) => row.name || row.id).join(", ")}`);
  }

  // --- Rendered cards must surface those facts, ordered by safety then proximity. ---
  if (!after.cards) failures.push("located render has no cards");
  if (after.driveFactCards !== after.cards) {
    failures.push(`not all rendered cards exposed 'X km · Y min drive' facts after locating: ${after.driveFactCards}/${after.cards}`);
  }
  if (after.distanceFactCards !== after.cards) {
    failures.push(`not all rendered cards exposed the inline distance fact after locating: ${after.distanceFactCards}/${after.cards}`);
  }
  const mismatchedDistance = after.rows.filter((row) => row.hasDistanceFact && !row.driveFact.startsWith(`${row.distanceFact} ·`));
  if (mismatchedDistance.length) failures.push(`inline distance disagrees with its drive estimate: ${mismatchedDistance.map((row) => `${row.name}: ${row.distanceFact} vs ${row.driveFact}`).join(" | ")}`);
  if (after.locateFactCards !== 0) failures.push(`route facts stayed in 'Locate to see ETA' mode after locating: ${after.locateFactCards}`);
  if (!/^\d+ (?:in view|nearby)$/i.test(after.headerH1)) failures.push(`header did not switch to live 'in view/nearby' mode after locating: ${after.headerH1}`);
  if (first && !first.hasDriveFact) failures.push(`first card lacks distance/ETA drive fact: ${first.driveFact || "(none)"}`);
  if (unknownDomIds.length) failures.push(`rendered cards not present in located API response: ${unknownDomIds.slice(0, 3).join(", ")}`);
  if (domOrderViolations.length) {
    failures.push(`rendered cards violate safety-tier then ascending-distance order: ${domOrderViolations.slice(0, 2).map((v) => `${v.before.name} -> ${v.after.name}`).join(" | ")}`);
  }
  if (routeReadyPrefixBroken) failures.push("rendered route-ready card appears below a backup tier");
  if (failOpenDomRows.length) {
    failures.push(`rendered Go now cards are not truly route-ready (fail-open): ${failOpenDomRows.slice(0, 3).map((row) => row.dom.name || row.dom.id).join(", ")}`);
  }

  // --- The nearest row in the safest available tier keeps its decision + handoff. ---
  if (!first) {
    failures.push("no first card to inspect after locating");
  } else {
    if (!/^pill-(go|check|verify)$/.test(first.pillClass)) {
      failures.push(`first located card lacks a go/check/verify decision pill: class=${first.pillClass || "missing"}`);
    }
    if (!first.pillLabel) failures.push("first located card decision pill has no label");
    if (!first.mapsHref || !/^https?:\/\//i.test(first.mapsHref)) failures.push("first card lost its Open in Maps link after locating");
    if (first.sourceHref && !/^https?:\/\//i.test(first.sourceHref)) failures.push("first card provider link is not usable after locating");
    if (!first.hasDetails) failures.push("first card lost its source Details disclosure after locating");
  }

  // --- Shell hygiene. ---
  if (after.overflowX) failures.push("located mobile viewport has horizontal overflow");
  if (unexpectedLogs.length) failures.push(`browser console contains errors: ${unexpectedLogs.join(" | ")}`);
  if (!responses.some((r) => r.url.includes("lat=") && r.url.includes("lon=") && r.status === 200)) {
    failures.push("no successful located /api/chargers response observed");
  }

  console.log(JSON.stringify({
    ok: failures.length === 0,
    baseUrl,
    userLocation,
    screenshot,
    before: {
      cards: before.cards,
      driveFactCards: before.driveFactCards,
      locateFactCards: before.locateFactCards,
      distanceFactCards: before.distanceFactCards,
      headerH1: before.headerH1,
      first: before.first,
    },
    after: {
      cards: after.cards,
      driveFactCards: after.driveFactCards,
      locateFactCards: after.locateFactCards,
      distanceFactCards: after.distanceFactCards,
      headerH1: after.headerH1,
      overflowX: after.overflowX,
      first,
      sample: after.rows.slice(0, 6).map((r) => ({ id: r.id, name: r.name, driveFact: r.driveFact, pill: `${r.pillClass}:${r.pillLabel}` })),
    },
    locatedRequest: {
      url: locatedResponse.url(),
      lat: locatedUrl.searchParams.get("lat"),
      lon: locatedUrl.searchParams.get("lon"),
      overview: locatedUrl.searchParams.get("overview"),
      status: locatedResponse.status(),
    },
    api: {
      stations: apiStations.length,
      leads: apiLeads.length,
      distanceRows: apiDistanceRows.length,
      stationsSafetyDistanceSorted: stationOrderViolations.length === 0,
      leadsSafetyDistanceSorted: leadOrderViolations.length === 0,
      malformedGoRows: malformedApiGoRows.length,
      firstRendered: matchedDomRows[0]?.api
        ? {
            name: matchedDomRows[0].api.name,
            decision: matchedDomRows[0].api.decision?.level || null,
            safetyTier: frontendSafetyTier(matchedDomRows[0].api),
            distance_km: matchedDomRows[0].api.distance_km,
            drive_eta_min_est: matchedDomRows[0].api.drive_eta_min_est,
          }
        : null,
    },
    order: {
      domCards: after.rows.length,
      matchedApiRows: matchedDomRows.length,
      stationApiViolations: stationOrderViolations.slice(0, 3),
      leadApiViolations: leadOrderViolations.slice(0, 3),
      domViolations: domOrderViolations.slice(0, 3),
      renderedRouteReady: renderedRouteReady.length,
      failOpenRendered: failOpenDomRows.length,
    },
    appGaps: APP_GAPS,
    requests,
    responses,
    logs,
    mapLibreInternalErrors,
    failures,
  }, null, 2));
  process.exit(failures.length ? 1 : 0);
}

main().catch((err) => {
  console.error(JSON.stringify({ ok: false, baseUrl, error: `${err.name}: ${err.message}` }, null, 2));
  process.exit(1);
});
