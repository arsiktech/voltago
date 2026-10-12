#!/usr/bin/env node
"use strict";

// Hermetic MapLibre 6 upgrade contract against the production build. The basemap and
// charger responses are fixtures; the bundled map engine, worker, clustering, image
// resolver, theme replacement and attribution sanitizer are real. No provider is called.
// Usage: node scripts/qa_maplibre_runtime.js [baseUrl]
const { chromium } = require("playwright");
const { unexpectedBrowserErrors, isChromiumReadPixelsDiagnostic, setFilter } = require("./qa_browser_harness");

const baseUrl = (process.argv[2] || process.env.VOLTA_QA_BASE_URL || "http://127.0.0.1:8773").replace(/\/$/, "");
const origin = new URL(baseUrl).origin;
const chromePath = process.env.VOLTA_QA_CHROME
  || (process.platform === "darwin" ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" : undefined);
const TIMEOUT = 20000;
const checks = [];
const logs = [];
const workers = [];
const externalRequests = [];

const leads = [0, 1, 2].map((index) => ({
  id: `map-qa-${index}`,
  name: `Map QA charger ${index + 1}`,
  lat: 40.4168 + index * 0.004,
  lon: -3.7038 + index * 0.004,
  city: "Madrid",
  lead: true,
  reliability: "official_directory",
  price_min: index === 0 ? 0 : 0.2,
  is_free: index === 0,
  verification: { location_verified: true, price_verified: true, live_verified: false },
  decision: { level: "verify", label: "Verify first" },
  connector_summary: { total: 2, max_power_kw: 22 },
  parking: { kind: "unknown", label: "Parking unknown", paid: null },
  primary_source: { name: "QA public source", kind: "directory", url: `https://example.org/charger/${index}`, action: "Verify at source" },
  source_url: `https://example.org/charger/${index}`,
  sources: [{ name: "QA public source", kind: "directory", url: `https://example.org/charger/${index}` }],
  charge_claim: { type: index === 0 ? "free" : "cheap", source: "QA public source", url: `https://example.org/charger/${index}`, summary: "Source-reported charging tariff; not live availability." },
  parking_claim: { kind: "unknown", source: "QA public source", url: `https://example.org/charger/${index}`, summary: "Parking cost is unknown." },
}));

// Deliberately distant island fixtures prove region selection scopes the list
// without depending on a rendered map or successful WebGL initialization.
leads.push(
  { ...leads[0], id: "map-qa-canary", name: "Canary QA charger", lat: 28.12, lon: -15.43 },
  { ...leads[0], id: "map-qa-balearic", name: "Balearic QA charger", lat: 39.57, lon: 2.65 },
);

const payload = {
  stations: [],
  leads,
  cache: { stale: false },
  meta: { mode: "overview", coverage: { mode: "overview", live_route_ready: 0, lead_count: leads.length } },
};
const style = {
  version: 8,
  sources: {
    "qa-road-shields": {
      type: "geojson",
      data: {
        type: "FeatureCollection",
        features: [{}, { ref_length: null }, { ref_length: "3" }, { ref_length: 3 }, { ref_length: 7 }, { ref_length: 3, network: "us-interstate" }, { ref_length: 3, network: "us-highway" }].map((properties, index) => ({
          type: "Feature",
          geometry: { type: "LineString", coordinates: [[-7 + index * 0.2, 41], [-6.95 + index * 0.2, 41.1]] },
          properties: { network: "es-road", ...properties, fixture_id: index },
        })),
      },
    },
    "qa-attribution": {
      type: "geojson",
      data: { type: "FeatureCollection", features: [] },
      // This string is local test data. If unsanitized, either the link or image can
      // increment the sentinel without contacting any external endpoint.
      attribution: '<a href="javascript:window.__qaAttributionExecuted++">QA attribution</a><img src="data:image/png;base64,broken" onerror="window.__qaAttributionExecuted++">',
    },
  },
  layers: [
    { id: "qa-attribution", type: "circle", source: "qa-attribution" },
    ...["highway-shield-non-us", "highway-shield-us-interstate", "road_shield_us"].map((id) => ({
      id, type: "line", source: "qa-road-shields",
      filter: ["all", ["<=", ["get", "ref_length"], 6], ["match", ["geometry-type"], ["LineString", "MultiLineString"], true, false],
        id === "highway-shield-non-us"
          ? ["match", ["get", "network"], ["us-highway", "us-interstate", "us-state"], false, true]
          : ["match", ["get", "network"], id === "road_shield_us" ? ["us-highway", "us-state"] : ["us-interstate"], true, false]],
    })),
  ],
};

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function fixtureRoutes(page, ready = Promise.resolve(), respondStyle) {
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.hostname === "tiles.openfreemap.org") {
      if (respondStyle) await respondStyle(route, url);
      else await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(style) });
    } else if (url.origin !== origin) {
      externalRequests.push(url.href);
      await route.abort();
    } else if (url.pathname === "/api/chargers") {
      await ready;
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(payload) });
    } else if (url.pathname === "/api/geocode") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ results: [{ name: "Madrid", label: "Madrid, Spain", lat: 40.4168, lon: -3.7038 }] }) });
    } else {
      await route.continue();
    }
  });
}

function record(page, scenario) {
  page.on("console", (message) => logs.push(`${message.type()}: [${scenario}] ${message.text()}`));
  page.on("pageerror", (error) => logs.push(`pageerror: [${scenario}] ${error.stack || error.message}`));
  page.on("worker", (worker) => workers.push(worker.url()));
}

async function waitForClusters(page, theme) {
  await page.waitForFunction((expectedTheme) => {
    const map = window.__qaMap;
    if (!map?.isStyleLoaded() || !map.getLayer("clusters")) return false;
    return map.queryRenderedFeatures({ layers: ["clusters"] }).some((feature) => {
      const properties = feature.properties;
      const tone = properties.go > 0 ? "go" : properties.free > 0 ? "free" : "neutral";
      return properties.point_count === 3
        && map.hasImage(`cluster:${expectedTheme}:${tone}:${properties.point_count_abbreviated}`);
    });
  }, theme, { timeout: TIMEOUT });
}

async function waitForRoadShields(page) {
  await page.waitForFunction(() => [["highway-shield-non-us", 3], ["highway-shield-us-interstate", 5], ["road_shield_us", 6]].every(([id, expected]) => {
    const ids = new Set(window.__qaMap.queryRenderedFeatures({ layers: [id] }).map((feature) => feature.properties.fixture_id));
    return ids.size === 1 && ids.has(expected);
  }), null, { timeout: TIMEOUT });
}

async function captureMap(page, mapChunk) {
  await page.evaluate(async (chunk) => {
    const module = await import(new URL(chunk, location.origin).href);
    const MapClass = Object.values(module).find((value) => typeof value === "function"
      && typeof value.prototype?.getSource === "function" && typeof value.prototype?.getCanvas === "function");
    if (!MapClass) throw new Error("Production map chunk did not export the real Map class");
    const addSource = MapClass.prototype.addSource;
    MapClass.prototype.addSource = function (...args) {
      window.__qaMap = this;
      return addSource.apply(this, args);
    };
  }, mapChunk);
}

async function supportedMap(browser, mapChunk) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, reducedMotion: "reduce", serviceWorkers: "block" });
  const page = await context.newPage();
  record(page, "supported");
  await page.addInitScript(() => {
    localStorage.setItem("volta-theme", "light");
    window.__qaAttributionExecuted = 0;
    Object.defineProperty(navigator, "geolocation", { configurable: true, value: {
      getCurrentPosition(success, error) {
        window.__qaResolveLocation = () => success({ coords: { latitude: 40.4168, longitude: -3.7038 } });
        window.__qaRejectLocation = () => error({ code: 1 });
      },
    } });
  });
  let releaseData;
  const ready = new Promise((resolve) => { releaseData = resolve; });
  await fixtureRoutes(page, ready);
  await page.goto(`${baseUrl}/`, { waitUntil: "domcontentloaded", timeout: TIMEOUT });
  // Capture the actual map through a public method; no engine API or worker is mocked.
  // Holding API responses keeps App's map activation deferred until capture is ready.
  await captureMap(page, mapChunk);
  releaseData();
  await page.locator("[data-station]").first().waitFor({ timeout: TIMEOUT });
  await waitForClusters(page, "light");
  assert(workers.some((url) => /\/assets\/maplibre-gl-worker-[^/]+\.js/.test(url)), "Bundled MapLibre worker never started");
  checks.push({ name: "real worker renders resolved cluster image", ok: true });

  const mainland = await page.evaluate(() => {
    const map = window.__qaMap;
    const canvas = map.getCanvas().getBoundingClientRect();
    const panel = document.querySelector("section").getBoundingClientRect();
    return { center: map.getCenter().toArray(), canvas: { left: canvas.left, right: canvas.right, top: canvas.top }, panelRight: panel.right, viewport: innerWidth };
  });
  assert(mainland.canvas.left >= mainland.panelRight, "Map camera includes area hidden beneath the desktop list");
  assert(mainland.canvas.right <= mainland.viewport, "Map extends beyond the viewport");
  assert(mainland.center[1] > 35 && mainland.center[1] < 45, "Initial map camera is not centered on mainland Spain");
  const regions = page.getByRole("navigation", { name: "Explore a region" });
  const regionCameras = {};
  for (const [name, latitude, longitude] of [["Canary Islands", 28.5, -15.8], ["Balearic Islands", 39.4, 2.75], ["Mainland", 39.9, -3.1]]) {
    const region = regions.getByRole("button", { name, exact: true });
    await region.click();
    await page.waitForFunction(({ latitude, longitude }) => {
      const map = window.__qaMap;
      const center = map.getCenter();
      return !map.isMoving() && Math.abs(center.lat - latitude) < 1 && Math.abs(center.lng - longitude) < 1;
    }, { latitude, longitude }, { timeout: TIMEOUT });
    assert(await region.getAttribute("aria-pressed") === "true", `${name} region did not become the active camera`);
    assert(await regions.locator('button[aria-pressed="true"]').count() === 1, "Region controls show multiple active cameras");
    regionCameras[name] = await page.evaluate(() => ({ center: window.__qaMap.getCenter().toArray(), zoom: window.__qaMap.getZoom() }));
  }
  async function selectSettledRegion(name) {
    await regions.getByRole("button", { name, exact: true }).click();
    // React may not have started the region's camera effect when click resolves.
    // A stationary previous camera is not proof that the requested region settled.
    await page.waitForFunction(({ center, zoom }) => {
      const map = window.__qaMap;
      const actual = map.getCenter();
      return !map.isMoving() && Math.abs(actual.lng - center[0]) < 0.001
        && Math.abs(actual.lat - center[1]) < 0.001 && Math.abs(map.getZoom() - zoom) < 0.001;
    }, regionCameras[name], { timeout: TIMEOUT });
  }
  await waitForClusters(page, "light");
  checks.push({ name: "separate map canvas fits mainland and both island regions", ok: true });

  await waitForRoadShields(page);
  assert(!logs.some((line) => /Expected value to be of type number/.test(line)), "Basemap shield guard still evaluates missing or invalid reference lengths");
  checks.push({ name: "road shield guards preserve valid labels and skip unknown lengths without warnings", ok: true });

  const sanitizer = await page.evaluate(() => {
    const attribution = document.querySelector(".maplibregl-ctrl-attrib-inner");
    const unsafe = attribution && [...attribution.querySelectorAll("*")].some((element) =>
      [...element.attributes].some((attribute) => /^on/i.test(attribute.name)
        || ((attribute.name === "href" || attribute.name === "src") && /^javascript:/i.test(attribute.value))));
    [...(attribution?.querySelectorAll("a") || [])].find((link) => link.textContent === "QA attribution")?.click();
    return { text: attribution?.textContent || "", unsafe, executed: window.__qaAttributionExecuted };
  });
  assert(sanitizer.text.includes("QA attribution"), "Fixture source attribution was not presented");
  assert(!sanitizer.unsafe && sanitizer.executed === 0, "MapLibre attribution left executable fixture content");
  checks.push({ name: "source attribution sanitizer removes executable attributes", ok: true });

  await page.getByRole("button", { name: "Switch to dark mode", exact: true }).click();
  await waitForClusters(page, "dark");
  await waitForRoadShields(page);
  checks.push({ name: "theme replacement regenerates dark cluster image", ok: true });

  const cluster = await page.evaluate(() => {
    const map = window.__qaMap;
    const feature = map.queryRenderedFeatures({ layers: ["clusters"] })[0];
    const point = map.project(feature.geometry.coordinates);
    const rect = map.getCanvas().getBoundingClientRect();
    return { x: rect.left + point.x, y: rect.top + point.y, zoom: map.getZoom() };
  });
  await page.mouse.click(cluster.x, cluster.y);
  await page.waitForFunction((before) => window.__qaMap.getZoom() > before + 1, cluster.zoom, { timeout: TIMEOUT });
  await page.waitForFunction(() => window.__qaMap.isStyleLoaded()
    && window.__qaMap.queryRenderedFeatures({ layers: ["station-price"] }).length > 0, null, { timeout: TIMEOUT });
  checks.push({ name: "cluster click expands to real station price pins", ok: true });

  const point = await page.evaluate(() => {
    const map = window.__qaMap;
    const feature = map.queryRenderedFeatures({ layers: ["station-dot"] })[0];
    const projected = map.project(feature.geometry.coordinates);
    const rect = map.getCanvas().getBoundingClientRect();
    return { x: rect.left + projected.x, y: rect.top + projected.y, id: feature.properties.id };
  });
  await page.mouse.click(point.x, point.y);
  await page.locator(".volta-popup").waitFor({ timeout: TIMEOUT });
  await page.getByRole("button", { name: "Switch to light mode", exact: true }).click();
  await page.waitForFunction((selected) => {
    const map = window.__qaMap;
    return map.isStyleLoaded() && map.getLayer("station-selected")
      && JSON.stringify(map.getFilter("station-selected")).includes(selected)
      && map.queryRenderedFeatures({ layers: ["station-price"] }).length > 0
      && document.querySelector(".volta-popup")?.textContent.includes("Source-backed lead");
  }, point.id, { timeout: TIMEOUT });
  assert(await page.locator(`[data-station="${point.id}"]`).count() > 0, "Selected map station is missing from the accessible list");
  assert(await page.locator(".pill-go").count() === 0, "Source-backed fixture was promoted to Go now");
  // Normal-motion camera travel must not let a pending auto-area timer win over
  // the driver's newer region choice. Reduced-motion-only checks miss this race.
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await selectSettledRegion("Mainland");
  await page.evaluate(() => window.__qaMap.jumpTo({ center: [-3.7038, 40.4168], zoom: 10 }));
  const canvas = await page.locator(".maplibregl-canvas").boundingBox();
  assert(canvas, "Map canvas is missing before pending-pan regression");
  const x = canvas.x + canvas.width / 2;
  const y = canvas.y + canvas.height / 2;
  const areaRequests = [];
  const recordAreaRequest = (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/api/chargers" && url.searchParams.get("overview") !== "true") areaRequests.push(url.href);
  };
  page.on("request", recordAreaRequest);
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + 65, y + 15, { steps: 12 });
  await page.mouse.up();
  await page.getByRole("button", { name: "Search this area", exact: true }).waitFor({ timeout: TIMEOUT });
  const canary = regions.getByRole("button", { name: "Canary Islands", exact: true });
  await canary.click();
  // Observe beyond the old 1000ms auto-area delay and the 800ms fly + 250ms emit.
  await page.waitForTimeout(1800);
  const finalRegion = await page.evaluate(() => ({ center: window.__qaMap.getCenter().toArray(), moving: window.__qaMap.isMoving() }));
  assert(!finalRegion.moving && Math.abs(finalRegion.center[0] + 15.8) < 1 && Math.abs(finalRegion.center[1] - 28.5) < 1, "Pending pan displaced the Canary camera");
  assert(await canary.getAttribute("aria-pressed") === "true", "Pending auto-area search overwrote the explicit region selection");
  assert(await page.getByText("Explore Spain", { exact: true }).isVisible(), "Pending pan changed the region overview into a live area search");
  assert(areaRequests.length === 0, "Pending pan issued a stale live query after the region choice");
  assert(await page.locator('[data-station="map-qa-canary"]').count() === 1 && await page.locator("[data-station]").count() === 1, "Region choice did not retain the Canary list scope");
  page.off("request", recordAreaRequest);
  checks.push({ name: "explicit region cancels pending pan search with normal motion", ok: true });

  // Click Locate at the real moveend event, before MapView's 250ms viewport
  // debounce, then repeat after that report but before App's auto-area timer.
  for (const timing of ["before viewport report", "after viewport report"]) {
    await selectSettledRegion("Mainland");
    await page.evaluate(() => {
      window.__qaMap.jumpTo({ center: [-3.7038, 40.4168], zoom: 10 });
      window.__qaResolveLocation = null;
    });
    const liveRequests = [];
    const recordLive = request => {
      const url = new URL(request.url());
      if (url.pathname === "/api/chargers" && url.searchParams.get("overview") !== "true") liveRequests.push(url.href);
    };
    page.on("request", recordLive);
    if (timing === "before viewport report") {
      await page.evaluate(() => window.__qaMap.once("moveend", () => {
        document.querySelector('button[aria-label="Use my location"]').click();
      }));
    }
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x + 65, y + 15, { steps: 12 });
    await page.mouse.up();
    if (timing === "after viewport report") {
      await page.getByRole("button", { name: "Search this area", exact: true }).waitFor({ timeout: TIMEOUT });
      await page.getByRole("button", { name: "Use my location", exact: true }).click();
    }
    await page.waitForFunction(() => typeof window.__qaResolveLocation === "function", null, { timeout: TIMEOUT });
    await page.waitForTimeout(1400); // Beyond both pending map/area callbacks.
    assert(liveRequests.length === 0, `An older pan started a query after Locate (${timing})`);
    await page.evaluate(() => window.__qaResolveLocation());
    await page.getByText("Near you", { exact: true }).waitFor({ timeout: TIMEOUT });
    assert(liveRequests.every(href => {
      const params = new URL(href).searchParams;
      return Number(params.get("lat")) === 40.4168 && Number(params.get("lon")) === -3.7038;
    }), `An older pan displaced the newer GPS origin (${timing})`);
    page.off("request", recordLive);
    checks.push({ name: `newer location request owns origin ${timing}`, ok: true });
  }

  // Pending/denied GPS must retain a manually explored island scope; Mainland
  // is not a valid fallback merely because an automatic map search was cancelled.
  await selectSettledRegion("Canary Islands");
  await page.evaluate(() => window.__qaMap.jumpTo({ center: [-15.43, 28.12], zoom: 10 }));
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + 30, y + 10, { steps: 8 });
  await page.mouse.up();
  await page.getByRole("button", { name: "Search this area", exact: true }).waitFor({ timeout: TIMEOUT });
  const visibleIds = () => page.locator("[data-station]").evaluateAll(rows => rows.map(row => row.dataset.station));
  const islandIds = await visibleIds();
  assert(islandIds.length === 1 && islandIds[0] === "map-qa-canary", "Manual island scope fixture is missing");
  await page.getByRole("button", { name: "Use my location", exact: true }).click();
  await page.waitForTimeout(1400);
  assert(JSON.stringify(await visibleIds()) === JSON.stringify(islandIds), "Pending GPS discarded the manually explored island list");
  await page.evaluate(() => window.__qaRejectLocation());
  await page.getByText("Location is unavailable", { exact: true }).waitFor({ timeout: TIMEOUT });
  assert(JSON.stringify(await visibleIds()) === JSON.stringify(islandIds), "Denied GPS discarded the manually explored island list");
  checks.push({ name: "pending and denied GPS preserve the manually explored island list", ok: true });

  // Reverse the order: a newer real drag must cancel pending GPS immediately,
  // before the first moveend viewport debounce has a chance to report it.
  await page.getByRole("button", { name: "Use my location", exact: true }).click();
  await page.evaluate(() => window.__qaMap.once("dragstart", () => window.__qaResolveLocation()));
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + 30, y + 10, { steps: 8 });
  await page.mouse.up();
  assert(!(await page.getByText("Near you", { exact: true }).count()), "Older GPS overrode the newer drag before its viewport report");
  await page.getByRole("button", { name: "Search this area", exact: true }).waitFor({ timeout: TIMEOUT });
  await page.getByText("Near map center", { exact: true }).waitFor({ timeout: TIMEOUT });
  assert(JSON.stringify(await visibleIds()) === JSON.stringify(islandIds), "Older GPS displaced the newer island drag");
  checks.push({ name: "newer map gesture cancels GPS before the viewport debounce", ok: true });

  const version = await page.evaluate(() => window.__qaMap.version);
  checks.push({ name: "theme replacement preserves selection and source-backed authority", ok: true });
  await context.close();
  return version;
}

async function pendingThemeRecovery(browser, mapChunk) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, reducedMotion: "reduce", serviceWorkers: "block" });
  const page = await context.newPage();
  record(page, "pending-theme");
  await page.addInitScript(() => localStorage.setItem("volta-theme", "light"));
  let releaseData, releaseLight, sawLight, sawDark;
  const ready = new Promise((resolve) => { releaseData = resolve; });
  const heldLight = new Promise((resolve) => { releaseLight = resolve; });
  const lightRequested = new Promise((resolve) => { sawLight = resolve; });
  const darkResponded = new Promise((resolve) => { sawDark = resolve; });
  await fixtureRoutes(page, ready, async (route, url) => {
    if (url.pathname.endsWith("/positron")) {
      sawLight();
      await heldLight;
      await route.fulfill({ status: 503, contentType: "text/plain", body: "Initial style unavailable" });
    } else {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
        ...style, layers: [{ id: "qa-dark-ready", type: "background" }, ...style.layers],
      }) });
      sawDark();
    }
  });
  await page.goto(`${baseUrl}/`, { waitUntil: "domcontentloaded", timeout: TIMEOUT });
  await captureMap(page, mapChunk);
  releaseData();
  await lightRequested;
  await page.getByRole("button", { name: "Switch to dark mode", exact: true }).click();
  await darkResponded;
  releaseLight();
  await page.waitForFunction(() => window.__qaMap?.isStyleLoaded() && window.__qaMap.getLayer("qa-dark-ready"), null, { timeout: TIMEOUT });
  await waitForClusters(page, "dark");
  await waitForRoadShields(page);
  assert(await page.getByRole("button", { name: "Switch to light mode", exact: true }).isVisible(), "Late failed initial style changed the selected theme");
  checks.push({ name: "new theme replaces a pending initial style even when the old request fails", ok: true });
  await context.close();
}

async function unsupportedMap(browser) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, reducedMotion: "reduce", serviceWorkers: "block" });
  const page = await context.newPage();
  record(page, "no-webgl2");
  await page.addInitScript(() => {
    const getContext = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (kind, ...args) {
      return kind === "webgl2" ? null : getContext.call(this, kind, ...args);
    };
  });
  let releaseData;
  const ready = new Promise((resolve) => { releaseData = resolve; });
  await fixtureRoutes(page, ready);
  await page.goto(`${baseUrl}/`, { waitUntil: "domcontentloaded", timeout: TIMEOUT });
  const regions = page.getByRole("navigation", { name: "Explore a region" });
  assert(await page.locator(".maplibregl-canvas").count() === 0, "Map started before the held decision data arrived");
  await regions.getByRole("button", { name: "Canary Islands", exact: true }).click();
  releaseData();
  await page.locator('[data-station="map-qa-canary"]').waitFor({ timeout: TIMEOUT });
  assert(await page.locator("[data-station]").count() === 1, "Early Canary region choice retained unrelated mainland or Balearic results");
  await regions.getByRole("button", { name: "Balearic Islands", exact: true }).click();
  await page.locator('[data-station="map-qa-balearic"]').waitFor({ timeout: TIMEOUT });
  assert(await page.locator("[data-station]").count() === 1, "Balearic list scope depends on WebGL rather than the chosen region");
  await regions.getByRole("button", { name: "Mainland", exact: true }).click();
  await page.locator('[data-station="map-qa-0"]').waitFor({ timeout: TIMEOUT });
  assert(await page.locator("[data-station]").count() === 3, "Returning to Mainland failed to restore its three fixture rows");
  checks.push({ name: "early region choice and island list scopes work without WebGL", ok: true });
  const fallback = page.getByRole("status").filter({ hasText: "Map unavailable on this device" });
  await fallback.waitFor({ timeout: TIMEOUT });
  assert(await fallback.evaluate((element) => !element.closest('[aria-hidden="true"]')), "Map fallback notice is hidden from assistive technology");
  await page.locator("[data-station]").first().waitFor({ timeout: TIMEOUT });
  await setFilter(page, "Open now", true);
  await page.waitForFunction(() => document.querySelectorAll("[data-station]").length === 0, null, { timeout: TIMEOUT });
  await page.getByRole("button", { name: "Clear all filters", exact: true }).click();
  await page.locator("[data-station]").first().waitFor({ timeout: TIMEOUT });
  await page.getByRole("button", { name: "Source details for Map QA charger 1", exact: true }).click();
  const source = page.locator('[data-station] a[href="https://example.org/charger/0"]').first();
  await source.waitFor({ timeout: TIMEOUT });
  assert(await source.count() > 0, "Source verification handoff is missing without WebGL2");
  assert(await source.getAttribute("target") === "_blank", "Source handoff lost its external-navigation behavior");
  const search = page.getByRole("combobox");
  await search.fill("Madrid");
  await page.getByRole("option").first().waitFor({ timeout: TIMEOUT });
  await search.press("Enter");
  await page.locator("[data-station]").first().waitFor({ timeout: TIMEOUT });
  assert(await page.locator(".pill-go").count() === 0, "Map fallback promoted source-backed fixture to Go now");
  checks.push({ name: "no WebGL2 preserves announced fallback, filtering, source and city search", ok: true });
  await context.close();
}

async function main() {
  const browser = await chromium.launch({ headless: true, ...(chromePath ? { executablePath: chromePath } : {}) });
  let mapVersion;
  let failure;
  try {
    const request = await browser.newContext();
    const response = await request.request.get(`${baseUrl}/.vite/manifest.json`);
    assert(response.ok(), "Production Vite manifest is unavailable");
    const manifest = await response.json();
    const mapChunk = Object.values(manifest).map((entry) => entry.file)
      .find((file) => /^assets\/maplibre-(?!gl-worker-)[^/]+\.js$/.test(file));
    assert(mapChunk, "Production manifest is missing the map engine chunk");
    await request.close();
    mapVersion = await supportedMap(browser, `/${mapChunk}`);
    await pendingThemeRecovery(browser, `/${mapChunk}`);
    await unsupportedMap(browser);
    // Chromium reports synchronous GPU readback as this exact native performance
    // diagnostic on some CI renderers. Keep it in the receipt; app/style warnings,
    // other GPU diagnostics and every rendering assertion remain fatal.
    const unexpected = unexpectedBrowserErrors(logs.filter((line) => !isChromiumReadPixelsDiagnostic(line)), { includeWarnings: true, allowServiceWorkerRegistrationBlocked: true });
    assert(unexpected.length === 0, `Unexpected browser errors: ${unexpected.join(" | ")}`);
    assert(externalRequests.length === 0, `Fixture attempted external requests: ${externalRequests.join(" | ")}`);
  } catch (error) {
    failure = error.stack || String(error);
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify({ ok: !failure, baseUrl, mapVersion, checks, workers, externalRequests, logs, driverPerformanceDiagnostics: logs.filter(isChromiumReadPixelsDiagnostic), ...(failure ? { failure } : {}) }, null, 2));
  if (failure) process.exitCode = 1;
}

const guard = setTimeout(() => { console.error("MapLibre runtime contract exceeded 110 seconds"); process.exit(1); }, 110000);
guard.unref();
main().catch((error) => { console.error(error); process.exitCode = 1; });
