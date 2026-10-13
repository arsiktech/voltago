#!/usr/bin/env node
/* Deterministic near-home first-use contract for the Volta React SPA.
 *
 * Protects the one job a fresh install must make obvious and usable at 390x844:
 * find a charger near home without granting location, then hand the
 * safest nearby result to Maps. Charger/geocode APIs are intercepted so this
 * contract tests the UI and query semantics, not today's upstream availability.
 *
 * Contract:
 *  - cold open exposes a visible >=44px "Use my location" CTA and address combobox;
 *  - no geolocation call happens before an explicit user action;
 *  - denied GPS leaves a clear address fallback;
 *  - keyboard suggestions reset across queries, dismiss without selecting, and reopen;
 *  - explicit location results replace the origin once; a later place selection survives
 *    unrelated renders; late callbacks cannot overwrite a newer city, region or locate;
 *  - choosing a place sends lat + lon + bbox, stays non-overview, and preserves the
 *    broad default price envelope;
 *  - near-home rows sort by safety tier, then distance within a tier;
 *  - the Live ready and Open now summaries count only route-ready Go-now rows,
 *    even when coverage omits its count and a Check-first row still carries a
 *    positive availability signal;
 *  - the mobile results scroller is usable and either the first card or direct
 *    Maps CTA is actionable inside the viewport;
 *  - Maps handoffs are safe new-tab links;
 *  - an empty nearby result offers Search wider before weaker-data/price recovery,
 *    and Search wider preserves the active price envelope.
 *
 * Usage: node scripts/qa_near_home_first_use.js <baseUrl>
 */
const { chromium } = require("playwright");
const { openFilters, closeFilters, setFilter } = require("./qa_browser_harness");
const { installDeterministicBasemap } = require("./qa_browser_harness");

const baseUrl = (process.argv[2] || process.env.VOLTA_QA_BASE_URL || "http://127.0.0.1:8773").replace(/\/$/, "");
const chromePath = process.env.VOLTA_QA_CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const screenshot = process.env.VOLTA_QA_NEAR_HOME_SCREENSHOT || "/tmp/volta-near-home-first-use.png";
const viewport = { width: 390, height: 844 };
const home = { lat: 39.47, lon: -0.377 };
const homeBbox = {
  latitude_sw: 39.4,
  longitude_sw: -0.45,
  latitude_ne: 39.54,
  longitude_ne: -0.3,
};

const guard = setTimeout(() => {
  console.error(JSON.stringify({ ok: false, baseUrl, error: "TimeoutError: global 85s guard hit" }));
  process.exit(1);
}, 85000);
guard.unref();

function mapsHref(id, lat, lon) {
  return `https://www.google.com/maps/search/?api=1&query=${lat},${lon}&qa=${id}`;
}

function baseStation({ id, name, lat, lon, lead, reliability, decision, available = 0, score = 0 }) {
  const sourceUrl = `https://example.com/volta-qa/${id}`;
  return {
    id,
    name,
    city: "Valencia",
    address: `${name} street`,
    lat,
    lon,
    lead,
    reliability,
    price_min: id === "go-near" ? 0 : 0.2,
    current_price_min: id === "go-near" ? 0 : 0.2,
    price_label: id === "go-near" ? "Free" : "EUR0.20/kWh",
    is_free: id === "go-near",
    is_cheap: true,
    tesla_ok: true,
    fast: false,
    max_power_kw: 22,
    status: lead ? "UNKNOWN" : available > 0 ? "AVAILABLE" : "CHARGING",
    available,
    total_evse: lead ? 2 : 2,
    connector_summary: lead
      ? { total: 2, max_power_kw: 22 }
      : { available, busy: Math.max(0, 2 - available), out_of_service: 0, total: 2, max_power_kw: 22 },
    connector_labels: ["Type 2"],
    verification: {
      live_verified: !lead,
      price_verified: reliability !== "community",
      location_verified: reliability !== "community",
      route_checked: false,
      level: lead ? reliability : "live",
    },
    decision: {
      level: decision,
      label:
        decision === "go"
          ? "Go now"
          : decision === "check"
            ? "Check first"
            : decision === "avoid"
              ? "Avoid"
              : "Verify first",
      reason: `Deterministic QA ${decision} row.`,
    },
    recommendation_score: score,
    source: lead ? "QA public directory" : "QA live OCPI",
    source_label: lead ? "QA public directory" : "QA live OCPI",
    source_url: sourceUrl,
    primary_source: {
      name: lead ? "QA public directory" : "QA live OCPI",
      url: sourceUrl,
      kind: lead ? "station_page" : "live_map",
      quality: reliability === "community" ? "approximate" : "exact",
    },
    sources: [{ name: "QA public source", url: sourceUrl, evidence: "Deterministic browser contract." }],
    source_count: 1,
    charge_claim: {
      type: "charging_price",
      source: "QA public source",
      url: sourceUrl,
      price_min: id === "go-near" ? 0 : 0.2,
      summary: id === "go-near" ? "Free charging." : "Charging at EUR0.20/kWh.",
    },
    parking_claim: {
      type: "parking",
      source: "QA public source",
      url: sourceUrl,
      summary: "Public parking; verify onsite signs.",
    },
    parking: {
      kind: "street_or_public",
      label: "Public parking",
      paid: false,
      note: "Verify onsite signs.",
    },
    access: { level: "public", label: "Public access", note: "Publicly accessible QA fixture." },
    navigation: { google: mapsHref(id, lat, lon) },
    freshness_minutes: lead ? null : 1,
  };
}

// Deliberately scrambled upstream order. The SPA must produce EXPECTED_ORDER from
// safety tier first and geodesic distance second, not preserve this response order.
const nearbyStations = [
  baseStation({
    id: "avoid-nearest",
    name: "Avoid nearest",
    lat: 39.47001,
    lon: -0.377,
    lead: false,
    reliability: "live",
    decision: "avoid",
    available: 0,
    score: 99,
  }),
  baseStation({
    id: "live-check",
    name: "Live check close",
    lat: 39.4702,
    lon: -0.377,
    lead: false,
    reliability: "live",
    decision: "check",
    // A stale/ambiguous row can retain positive connector availability while the
    // backend correctly demotes its decision. It must not inflate "open now".
    available: 1,
    score: 100,
  }),
  baseStation({
    id: "go-far",
    name: "Go far",
    lat: 39.49,
    lon: -0.377,
    lead: false,
    reliability: "live",
    decision: "go",
    available: 1,
    score: 500,
  }),
  baseStation({
    id: "go-near",
    name: "Go near free",
    lat: 39.471,
    lon: -0.377,
    lead: false,
    reliability: "live",
    decision: "go",
    available: 1,
    score: 1,
  }),
];

const nearbyLeads = [
  baseStation({
    id: "official-far",
    name: "Official farther backup",
    lat: 39.48,
    lon: -0.377,
    lead: true,
    reliability: "official",
    decision: "verify",
    score: 900,
  }),
  baseStation({
    id: "community-nearest",
    name: "Community nearest lead",
    lat: 39.47005,
    lon: -0.377,
    lead: true,
    reliability: "community",
    decision: "verify",
    score: 999,
  }),
  baseStation({
    id: "directory-near",
    name: "Directory close backup",
    lat: 39.4701,
    lon: -0.377,
    lead: true,
    reliability: "public directory",
    decision: "verify",
    score: 2,
  }),
];

const EXPECTED_ORDER = [
  "go-near",
  "go-far",
  "live-check",
  "directory-near",
  "official-far",
  "community-nearest",
  "avoid-nearest",
];

function response(stations, leads) {
  return {
    stations,
    leads,
    meta: {
      price_cap_eur_kwh: 0.25,
      coverage: {
        mode: "standard",
        minimum_live_route_ready: 1,
        lead_count: leads.length,
        summary: "Deterministic near-home QA coverage.",
      },
    },
    cache: { hit: false, stale: false, age_seconds: 0, ttl_seconds: 60 },
  };
}

const initialLead = baseStation({
  id: "overview-home-lead",
  name: "Overview home lead",
  lat: 39.47,
  lon: -0.377,
  lead: true,
  reliability: "official",
  decision: "verify",
  score: 1,
});

function safeExternal(link) {
  const rel = String(link?.rel || "").toLowerCase().split(/\s+/).filter(Boolean);
  return link?.target === "_blank" && rel.includes("noopener") && rel.includes("noreferrer");
}

function bboxNumbers(url) {
  const raw = new URL(url).searchParams.get("bbox") || "";
  const values = raw.split(",").map(Number);
  return values.length === 4 && values.every(Number.isFinite) ? values : null;
}

async function main() {
  const failures = [];
  const logs = [];
  const chargerRequests = [];
  const geocodeRequests = [];
  const browser = await chromium.launch({ headless: true, executablePath: chromePath });
  const context = await browser.newContext({
    viewport,
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
    serviceWorkers: "block",
    reducedMotion: "reduce",
  });
  const page = await context.newPage();

  page.on("console", (msg) => logs.push(`${msg.type()}: ${msg.text()}`));
  page.on("pageerror", (err) => logs.push(`pageerror: ${String(err.stack || err.message)}`));

  // Count (and deterministically deny) geolocation calls without opening a browser
  // permission prompt. A cold-open call is therefore directly observable as a failure.
  await page.addInitScript(() => {
    window.__voltaQaGeoCalls = 0;
    window.__voltaQaDeferLocation = false;
    window.__voltaQaPendingLocations = [];
    const denied = {
      getCurrentPosition(success, error) {
        window.__voltaQaGeoCalls += 1;
        if (window.__voltaQaDeferLocation) {
          window.__voltaQaResolveLocation = (latitude, longitude) => success({ coords: { latitude, longitude } });
          window.__voltaQaPendingLocations.push({ resolve: window.__voltaQaResolveLocation, reject: error });
        } else {
          error?.({ code: 1, message: "Permission denied by deterministic QA" });
        }
      },
      watchPosition(_success, error) {
        window.__voltaQaGeoCalls += 1;
        error?.({ code: 1, message: "Permission denied by deterministic QA" });
        return 1;
      },
      clearWatch() {},
    };
    Object.defineProperty(navigator, "geolocation", { configurable: true, value: denied });
  });

  // The map is an enhancement and not part of this product contract. Keep tile
  // networking out of the deterministic run while leaving the real MapLibre UI mounted.
  await installDeterministicBasemap(page);

  let releaseChangedSearch;
  const changedSearchPending = new Promise((resolve) => { releaseChangedSearch = resolve; });
  await page.route("**/api/geocode?*", async (route) => {
    const url = route.request().url();
    geocodeRequests.push(url);
    if (new URL(url).searchParams.get("q") === "Other city") {
      await changedSearchPending;
      await route.fulfill({ status: 200, contentType: "application/json", body: '{"results":[]}' });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        query: new URL(url).searchParams.get("q") || "",
        results: [
          ...["Home Base", "Home alternative", "Home farther away"].map((name, index) => ({
            name,
            label: `${name}, Valencia`,
            lat: home.lat + index * 0.01,
            lon: home.lon,
            bbox: homeBbox,
          })),
        ],
      }),
    });
  });

  let delayRegionOverview = false;
  let releaseRegionOverview;
  const regionOverviewPending = new Promise((resolve) => { releaseRegionOverview = resolve; });
  let observeRegionRequest;
  const regionRequestSeen = new Promise((resolve) => { observeRegionRequest = resolve; });
  await page.route("**/api/chargers?*", async (route) => {
    const url = route.request().url();
    chargerRequests.push(url);
    const params = new URL(url).searchParams;
    const overview = params.get("overview") === "true";
    const scopedOverview = overview && params.has("bbox");
    if (delayRegionOverview && overview && params.get("available") === "true") {
      observeRegionRequest();
      await regionOverviewPending;
    }
    const body = overview
      ? scopedOverview
        ? response([], nearbyLeads)
        : response([], [initialLead])
      : response(nearbyStations, nearbyLeads);
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });

  await page.goto(`${baseUrl}/`, { waitUntil: "domcontentloaded", timeout: 20000 });
  await page.getByRole("combobox", { name: "Enter home address or city" }).waitFor({ timeout: 15000 });
  await page.getByText("Search a city or use your location to check nearby chargers.", { exact: true }).waitFor({ timeout: 15000 });
  await page.waitForTimeout(250);

  const cold = await page.evaluate(({ width, height }) => {
    const ctas = [...document.querySelectorAll('button[aria-label="Use my location"], button')]
      .filter((button) => (button.textContent || "").trim() === "Use my location" || button.getAttribute("aria-label") === "Use my location")
      .map((button) => {
        const rect = button.getBoundingClientRect();
        const style = getComputedStyle(button);
        return {
          text: (button.textContent || "").replace(/\s+/g, " ").trim(),
          aria: button.getAttribute("aria-label") || "",
          width: Math.round(rect.width * 10) / 10,
          height: Math.round(rect.height * 10) / 10,
          visible:
            style.visibility !== "hidden" &&
            style.display !== "none" &&
            rect.width > 0 &&
            rect.height > 0 &&
            rect.left < width &&
            rect.right > 0 &&
            rect.top < height &&
            rect.bottom > 0,
        };
      });
    const combo = document.querySelector('input[role="combobox"]');
    return {
      ctas,
      combo: combo
        ? {
            visible: combo.getBoundingClientRect().width > 0 && combo.getBoundingClientRect().height > 0,
            label: combo.getAttribute("aria-label"),
            autocomplete: combo.getAttribute("aria-autocomplete"),
          }
        : null,
      geolocationCalls: window.__voltaQaGeoCalls,
    };
  }, viewport);

  const primaryCta = cold.ctas.find((cta) => cta.visible && cta.width >= 44 && cta.height >= 44);
  if (!primaryCta) {
    failures.push(`cold open lacks a visible >=44x44 Use my location CTA: ${JSON.stringify(cold.ctas)}`);
  }
  if (!cold.combo?.visible || cold.combo.label !== "Enter home address or city" || cold.combo.autocomplete !== "list") {
    failures.push(`cold open lacks the address/city combobox contract: ${JSON.stringify(cold.combo)}`);
  }
  if (cold.geolocationCalls !== 0) {
    failures.push(`cold open called geolocation without user action (${cold.geolocationCalls} calls)`);
  }

  // Click the explicit >=44px location control, then require the address fallback.
  const useLocationButtons = page.getByRole("button", { name: "Use my location", exact: true });
  let largeCta = null;
  for (let i = 0; i < (await useLocationButtons.count()); i += 1) {
    const candidate = useLocationButtons.nth(i);
    const box = await candidate.boundingBox();
    if (box && box.width >= 44 && box.height >= 44) {
      largeCta = candidate;
      break;
    }
  }
  if (largeCta) await largeCta.click();
  await page.getByText("Location is unavailable", { exact: true }).waitFor({ timeout: 5000 }).catch(() => null);
  const denied = await page.evaluate(() => ({
    geolocationCalls: window.__voltaQaGeoCalls,
    fallbackVisible: [...document.querySelectorAll('[role="status"]')].some((status) => {
      const text = (status.textContent || "").replace(/\s+/g, " ");
      return /Location is unavailable/.test(text) && /home address or city/.test(text);
    }),
    comboVisible: Boolean(document.querySelector('input[role="combobox"]')?.getBoundingClientRect().width),
  }));
  if (denied.geolocationCalls !== 1) failures.push(`explicit location action made ${denied.geolocationCalls} geolocation calls; expected 1`);
  if (!denied.fallbackVisible || !denied.comboVisible) failures.push("denied GPS did not expose the home address/city fallback");

  // Address selection must become the origin for one bounded, broad, non-overview query.
  const combo = page.getByRole("combobox", { name: "Enter home address or city" });
  await combo.fill("Carrer de casa");
  const option = page.getByRole("option", { name: /Home Base/ });
  await option.waitFor({ timeout: 5000 });
  const keyboardOptions = [];
  async function checkActiveOption(index, label) {
    const id = await combo.getAttribute("aria-activedescendant");
    const expected = index < 0 ? null : await page.getByRole("option").nth(index).getAttribute("id");
    const selected = await page.getByRole("option", { selected: true }).count();
    keyboardOptions.push({ label, index, id, selected });
    if (id !== expected || selected !== (index < 0 ? 0 : 1)) {
      failures.push(`${label}: keyboard suggestion should be ${index}, got ${id} (${selected} selected)`);
    }
  }
  await checkActiveOption(0, "initial query");
  await combo.press("ArrowDown");
  await checkActiveOption(1, "ArrowDown");
  // Changing the query must immediately discard the old city's selectable results.
  const changedRequest = page.waitForRequest((req) => req.url().includes("/api/geocode?q=Other"));
  await combo.fill("Other city");
  await changedRequest;
  if (await option.count()) {
    releaseChangedSearch();
    throw new Error("previous-city geocode option remains selectable during a new search");
  }
  await checkActiveOption(-1, "pending changed query");
  const beforeEnter = chargerRequests.length;
  await combo.press("Enter");
  await page.waitForTimeout(100);
  if (chargerRequests.length !== beforeEnter) failures.push("Enter routed to a previous city's result during a pending search");
  releaseChangedSearch();
  await combo.fill("Carrer de casa");
  await option.waitFor({ timeout: 5000 });
  await checkActiveOption(0, "return to cached query");
  await combo.press("ArrowUp");
  await checkActiveOption(2, "ArrowUp wraps");
  await combo.press("ArrowDown");
  await checkActiveOption(0, "ArrowDown wraps");
  const beforeDismissedEnter = chargerRequests.length;
  await combo.press("Escape");
  await page.getByRole("listbox").waitFor({ state: "hidden", timeout: 5000 });
  await combo.press("Enter");
  await page.waitForTimeout(100);
  if (chargerRequests.length !== beforeDismissedEnter || await combo.inputValue() !== "Carrer de casa") {
    throw new Error("Enter selected a hidden suggestion after Escape");
  }
  await combo.press("ArrowDown");
  await option.waitFor({ timeout: 5000 });
  await checkActiveOption(0, "ArrowDown reopens dismissed suggestions");
  await combo.press("Escape");
  await combo.fill("Carrer de casa revised");
  await option.waitFor({ timeout: 5000 });
  await checkActiveOption(0, "typing reopens dismissed suggestions");
  const selectedResponse = page.waitForResponse(
    (res) => {
      if (res.status() !== 200 || !res.url().includes("/api/chargers")) return false;
      const params = new URL(res.url()).searchParams;
      return params.has("lat") && params.has("lon") && params.get("overview") !== "true" && !params.has("fast");
    },
    { timeout: 10000 },
  );
  await combo.press("Enter");
  const selectedUrl = (await selectedResponse).url();
  if (await combo.inputValue()) failures.push("Enter on a home suggestion did not clear the query");
  if (await page.getByRole("option").count()) failures.push("selected suggestions remain interactive after the query clears");
  await page.locator('[data-station="go-near"] article.card').waitFor({ timeout: 10000 });
  await page.waitForTimeout(250);

  const selectedParams = new URL(selectedUrl).searchParams;
  const selectedBbox = bboxNumbers(selectedUrl);
  if (Math.abs(Number(selectedParams.get("lat")) - home.lat) > 0.000001) failures.push(`place query lat is wrong: ${selectedParams.get("lat")}`);
  if (Math.abs(Number(selectedParams.get("lon")) - home.lon) > 0.000001) failures.push(`place query lon is wrong: ${selectedParams.get("lon")}`);
  if (!selectedBbox) failures.push("place query omitted a numeric bbox");
  if (selectedParams.get("overview") === "true") failures.push("place query stayed in national overview mode");
  if (selectedParams.get("max_price") !== "5.0") failures.push(`place query changed the broad price envelope: ${selectedParams.get("max_price")}`);

  // Reveal the intentionally weaker community fixture so every safety tier is present.
  const filterDialog = await openFilters(page);
  const checkedChip = filterDialog.getByRole("button", { name: "Source checked", exact: true });
  if ((await checkedChip.getAttribute("aria-pressed")) === "true") await checkedChip.click();
  await closeFilters(page);
  await page.locator('[data-station="community-nearest"]').waitFor({ timeout: 5000 });
  await page.waitForTimeout(150);

  const ordered = await page.evaluate(() =>
    [...document.querySelectorAll(".station-virtual-row[data-index]")]
      .map((row) => ({
        index: Number(row.getAttribute("data-index")),
        id: row.querySelector("[data-station]")?.getAttribute("data-station") || "",
        pill: (row.querySelector(".pill")?.textContent || "").replace(/\s+/g, " ").trim(),
        badges: [...row.querySelectorAll(".badge")].map((badge) => (badge.textContent || "").replace(/\s+/g, " ").trim()),
      }))
      .sort((a, b) => a.index - b.index),
  );
  const actualOrder = ordered.map((row) => row.id);
  if (JSON.stringify(actualOrder) !== JSON.stringify(EXPECTED_ORDER)) {
    failures.push(`near-home order is not safety-tier then distance: ${actualOrder.join(" -> ")}`);
  }

  // Ensure the fixed-height mobile panel leaves a real decision surface, and that
  // the best result has a safe Maps handoff available without leaving Volta behind.
  const mobile = await page.evaluate(({ width, height }) => {
    const section = document.querySelector("section");
    const scroller = section?.querySelector("[data-result-scroller]") || null;
    const firstCard = document.querySelector('[data-station="go-near"] article.card');
    const hint = [...document.querySelectorAll('[role="status"]')].find((el) => /Go now option/.test(el.textContent || ""));
    const hintMaps = hint?.querySelector("a") || null;
    const mapsLinks = [...(section?.querySelectorAll("a") || [])]
      .filter((link) => /Maps/i.test(link.textContent || ""))
      .map((link) => {
        const rect = link.getBoundingClientRect();
        return {
          text: (link.textContent || "").replace(/\s+/g, " ").trim(),
          href: link.href,
          target: link.target,
          rel: link.rel,
          insideViewport: rect.left >= 0 && rect.right <= width && rect.top >= 0 && rect.bottom <= height,
          height: Math.round(rect.height * 10) / 10,
        };
      });
    const mapRect = document.querySelector(".maplibregl-canvas")?.getBoundingClientRect();
    const panelRect = section?.getBoundingClientRect();
    const cardRect = firstCard?.getBoundingClientRect();
    const hintRect = hintMaps?.getBoundingClientRect();
    const visiblePixels = (rect) => (rect ? Math.max(0, Math.min(rect.bottom, height) - Math.max(rect.top, 0)) : 0);
    return {
      scroller: scroller
        ? {
            clientHeight: scroller.clientHeight,
            scrollHeight: scroller.scrollHeight,
            overflowY: getComputedStyle(scroller).overflowY,
          }
        : null,
      mapAbovePanel: Boolean(mapRect && panelRect && mapRect.height > 0 && mapRect.bottom <= panelRect.top + 1),
      firstCardVisiblePixels: visiblePixels(cardRect),
      directMapsInsideViewport: Boolean(
        hintRect && hintRect.left >= 0 && hintRect.right <= width && hintRect.top >= 0 && hintRect.bottom <= height,
      ),
      driverGuidance: hint?.textContent?.replace(/\s+/g, " ").trim() || "",
      liveBadge: section?.querySelector(".live-dot")?.parentElement?.textContent?.replace(/\s+/g, " ").trim() || "",
      mapsLinks,
    };
  }, viewport);

  if (!mobile.mapAbovePanel) failures.push("mobile map centers results underneath the panel");
  if (!mobile.scroller || mobile.scroller.clientHeight < 44 || mobile.scroller.scrollHeight <= mobile.scroller.clientHeight) {
    failures.push(`mobile result scroller is not usable: ${JSON.stringify(mobile.scroller)}`);
  }
  if (mobile.firstCardVisiblePixels < 44 && !mobile.directMapsInsideViewport) {
    failures.push(
      `neither the first card nor direct Maps CTA is actionable in the 390x844 viewport (card pixels=${mobile.firstCardVisiblePixels})`,
    );
  }
  if (!/^2 Go now options nearby/.test(mobile.driverGuidance)) {
    failures.push(`driver guidance must count only the two route-ready fixtures: ${mobile.driverGuidance}`);
  }
  if (!/2 live ·/.test(mobile.liveBadge)) {
    failures.push(`live badge must derive two route-ready fixtures when coverage omits its count: ${mobile.liveBadge}`);
  }
  if (!mobile.mapsLinks.length) failures.push("near-home result exposes no Maps handoff");
  for (const link of mobile.mapsLinks) {
    if (!safeExternal(link)) failures.push(`unsafe Maps handoff (${link.text || link.href}): target=${link.target} rel=${link.rel}`);
    if (!/google\.[^/]+\/maps|maps\.apple\.com/i.test(link.href || "")) {
      failures.push(`Maps handoff is not a map URL: ${link.href || "(missing)"}`);
    }
  }
  await page.screenshot({ path: screenshot, fullPage: false });

  // Fast is false for every fixture, producing a deterministic nearby empty state.
  // Recovery priority must be radius first; weaker evidence/price come afterwards.
  await setFilter(page, "Fast", true);
  await page.getByText("No charger nearby yet", { exact: true }).waitFor({ timeout: 10000 });
  const recovery = await page.evaluate(() => {
    const empty = [...document.querySelectorAll("section .card")].find((card) => /No charger nearby yet/.test(card.textContent || ""));
    return empty
      ? [...empty.querySelectorAll("button")].map((button) => (button.textContent || "").replace(/\s+/g, " ").trim())
      : [];
  });
  const widerIndex = recovery.indexOf("Search wider area");
  const leadsIndex = recovery.indexOf("Show all leads");
  const priceIndex = recovery.indexOf("Broaden price");
  if (widerIndex < 0 || leadsIndex < 0 || priceIndex < 0 || !(widerIndex < leadsIndex && widerIndex < priceIndex)) {
    failures.push(`nearby empty-state recovery order is wrong: ${recovery.join(" -> ")}`);
  }

  const widerRequest = page.waitForRequest(
    (req) => {
      if (!req.url().includes("/api/chargers")) return false;
      const params = new URL(req.url()).searchParams;
      const bbox = bboxNumbers(req.url());
      return (
        params.has("lat") &&
        params.has("lon") &&
        params.get("overview") !== "true" &&
        params.get("fast") === "true" &&
        bbox &&
        selectedBbox &&
        bbox.join(",") !== selectedBbox.join(",")
      );
    },
    { timeout: 10000 },
  );
  await page.getByRole("button", { name: "Search wider area", exact: true }).click();
  const widerUrl = (await widerRequest).url();
  const widerParams = new URL(widerUrl).searchParams;
  const widerBbox = bboxNumbers(widerUrl);
  if (widerParams.get("max_price") !== "5.0") {
    failures.push(`Search wider changed max_price instead of preserving the broad price envelope: ${widerParams.get("max_price")}`);
  }
  if (widerParams.get("overview") === "true") failures.push("Search wider returned to overview mode");
  if (!widerBbox || !selectedBbox) {
    failures.push("Search wider request lacks comparable bbox coordinates");
  } else {
    const oldHeight = selectedBbox[2] - selectedBbox[0];
    const oldWidth = selectedBbox[3] - selectedBbox[1];
    const newHeight = widerBbox[2] - widerBbox[0];
    const newWidth = widerBbox[3] - widerBbox[1];
    if (!(newHeight > oldHeight && newWidth > oldWidth)) {
      failures.push(`Search wider did not expand bbox: old=${selectedBbox.join(",")} new=${widerBbox.join(",")}`);
    }
  }

  // Keep the user's selected place until GPS actually succeeds. Subsequent UI renders
  // must not replay that result and take ownership away from a newly chosen place.
  await page.evaluate(() => { window.__voltaQaDeferLocation = true; });
  const locationOrigins = [];
  async function locateAt(lat, lon) {
    const beforeCalls = await page.evaluate(() => window.__voltaQaGeoCalls);
    await page.getByRole("button", { name: "Use my location", exact: true }).click();
    await page.getByText("Near Home Base", { exact: true }).waitFor({ timeout: 5000 });
    if (await page.evaluate(() => window.__voltaQaGeoCalls) !== beforeCalls + 1) {
      failures.push("explicit location action did not make exactly one geolocation call");
    }
    const request = page.waitForRequest((req) => {
      const url = new URL(req.url());
      return url.pathname === "/api/chargers" && url.searchParams.get("overview") !== "true"
        && Number(url.searchParams.get("lat")) === lat && Number(url.searchParams.get("lon")) === lon;
    }, { timeout: 10000 });
    await page.evaluate(({ latitude, longitude }) => window.__voltaQaResolveLocation(latitude, longitude), { latitude: lat, longitude: lon });
    locationOrigins.push((await request).url());
    await page.getByText("Near you", { exact: true }).waitFor({ timeout: 5000 });
  }
  await locateAt(home.lat + 0.02, home.lon - 0.02);
  await combo.fill("Carrer de casa");
  await option.waitFor({ timeout: 5000 });
  await combo.press("Enter");
  await page.getByText("Near Home Base", { exact: true }).waitFor({ timeout: 5000 });
  await page.getByRole("button", { name: /Switch to .* mode/ }).click();
  await page.getByText("Near Home Base", { exact: true }).waitFor({ timeout: 5000 });
  if (await page.evaluate(() => window.__voltaQaGeoCalls) !== 2) {
    failures.push("choosing a place or switching theme repeated geolocation without user action");
  }
  await locateAt(home.lat + 0.03, home.lon - 0.03);
  const locationCallsBeforeRaces = await page.evaluate(() => window.__voltaQaGeoCalls);
  if (locationCallsBeforeRaces !== 3) failures.push(`expected denied + two explicit location calls, got ${locationCallsBeforeRaces}`);

  // GPS cannot be physically cancelled. Its callbacks must be ignored after the
  // driver chooses a different origin, including old failures and repeated requests.
  const lateLocationChecks = [];
  async function resolvePending(index, latitude = home.lat + 0.08, longitude = home.lon - 0.08) {
    await page.evaluate(({ index, latitude, longitude }) => {
      const pending = window.__voltaQaPendingLocations[index];
      pending.resolve(latitude, longitude);
      pending.reject?.({ code: 1, message: "Late denied callback" });
    }, { index, latitude, longitude });
    await page.waitForTimeout(100);
  }
  async function pendingLocation() {
    const index = await page.evaluate(() => window.__voltaQaPendingLocations.length);
    await page.getByRole("button", { name: "Use my location", exact: true }).click();
    return index;
  }
  async function checkLateLocation(label, expectedMode, previousRequests) {
    const mode = await page.locator('[data-search-area]').textContent();
    lateLocationChecks.push({ label, mode, addedRequests: chargerRequests.length - previousRequests });
    if (mode.trim() !== expectedMode || chargerRequests.length !== previousRequests) {
      failures.push(`${label}: a late GPS callback replaced the driver's newer origin`);
    }
  }
  const beforePlace = await pendingLocation();
  await combo.fill("Carrer de casa");
  await option.waitFor({ timeout: 5000 });
  await combo.press("Enter");
  await page.getByText("Near Home Base", { exact: true }).waitFor({ timeout: 5000 });
  const placeRequests = chargerRequests.length;
  await resolvePending(beforePlace);
  await checkLateLocation("city selection", "Near Home Base", placeRequests);

  const supersededLocate = await pendingLocation();
  const latestLocate = await pendingLocation();
  const repeatedRequests = chargerRequests.length;
  await resolvePending(supersededLocate);
  await checkLateLocation("newer locate request", "Near Home Base", repeatedRequests);
  // The latest location request still works after ignoring its predecessor.
  await page.evaluate(({ index, latitude, longitude }) => {
    window.__voltaQaPendingLocations[index].resolve(latitude, longitude);
  }, { index: latestLocate, latitude: home.lat + 0.04, longitude: home.lon - 0.04 });
  await page.getByText("Near you", { exact: true }).waitFor({ timeout: 5000 });

  // An uncached region overview must discard the preceding live query's authority
  // immediately, even while React Query retains its previous data and transport waits.
  await setFilter(page, "Fast", false);
  await setFilter(page, "Open now", true);
  await page.locator('[data-station="go-near"] .pill-go').waitFor({ timeout: 10000 });
  delayRegionOverview = true;
  const beforeRegion = await pendingLocation();
  await page.getByRole("navigation", { name: "Explore a region" }).getByRole("button", { name: "Mainland", exact: true }).click();
  await Promise.race([
    regionRequestSeen,
    new Promise((_, reject) => setTimeout(() => reject(new Error("Delayed region overview was never requested")), 10000)),
  ]);
  const pendingOverview = await page.evaluate(() => ({
    mode: document.querySelector('[data-search-area]')?.textContent.trim(),
    goPills: document.querySelectorAll('.pill-go').length,
    liveAvailability: document.querySelectorAll('[data-availability="live"]').length,
    readyGuidance: /Go now options? nearby/.test(document.querySelector('[data-driver-guidance]')?.textContent || ''),
    cards: document.querySelectorAll('[data-station]').length,
  }));
  if (pendingOverview.mode !== "Explore chargers" || pendingOverview.goPills || pendingOverview.liveAvailability || pendingOverview.readyGuidance || pendingOverview.cards) {
    failures.push(`pending region overview retained old live authority under Open now: ${JSON.stringify(pendingOverview)}`);
  }
  releaseRegionOverview();
  await page.getByText("No chargers match these filters", { exact: true }).waitFor({ timeout: 10000 });
  const regionRequests = chargerRequests.length;
  await resolvePending(beforeRegion);
  await checkLateLocation("region selection", "Explore chargers", regionRequests);
  if (await page.getByText("Location is unavailable", { exact: true }).count()) {
    failures.push("a late geolocation error changed the selected region's guidance");
  }
  const finalGeolocationCalls = await page.evaluate(() => window.__voltaQaGeoCalls);

  await browser.close();

  const unexpectedErrors = logs.filter(
    (line) =>
      /^error:|^pageerror:/.test(line) &&
      !/ERR_BLOCKED_BY_CLIENT|tiles\.openfreemap\.org|Failed to load resource|AJAXError|maplibre-|Invalid LngLat|fromInvProjectionMatrix|getCameraFrustum/i.test(line),
  );
  if (unexpectedErrors.length) failures.push(`browser console contains errors: ${unexpectedErrors.join(" | ")}`);

  console.log(JSON.stringify({
    ok: failures.length === 0,
    baseUrl,
    viewport,
    screenshot,
    cold,
    denied,
    selectedUrl,
    keyboardOptions,
    locationOrigins,
    lateLocationChecks,
    finalGeolocationCalls,
    pendingOverview,
    expectedOrder: EXPECTED_ORDER,
    ordered,
    mobile,
    recovery,
    widerUrl,
    geocodeRequests,
    chargerRequests,
    logs,
    failures,
  }, null, 2));
  process.exit(failures.length ? 1 : 0);
}

main().catch(async (err) => {
  console.error(JSON.stringify({ ok: false, baseUrl, error: `${err.name}: ${err.message}` }, null, 2));
  process.exit(1);
});
