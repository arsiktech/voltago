#!/usr/bin/env node
/*
 * Contract: the map is an enhancement, never a dependency.
 *
 * Port of the retired static-shell check (Leaflet blocked at unpkg.com) to the React SPA
 * (MapLibre, bundled same-origin; basemap tiles from tiles.openfreemap.org):
 *
 *  - Scenario "tiles-blocked": abort every basemap-tile request before load. The panel,
 *    station cards, Open in Maps plus Details provenance, parking + proof facts,
 *    filter chips and search must all keep working.
 *  - Scenario "map-chunk-blocked": additionally abort the same-origin maplibre-*.js/.css
 *    lazy chunk — the SPA analogue of the old "Leaflet CDN blocked". The panel must still
 *    render and stay interactive (Suspense fallback null is fine; a white screen is a bug).
 */
const { chromium } = require("playwright");
const { openFilters, closeFilters } = require("./qa_browser_harness");

const baseUrl = (process.argv[2] || process.env.VOLTA_QA_BASE_URL || "http://127.0.0.1:8773").replace(/\/$/, "");
const screenshot = process.env.VOLTA_QA_CDN_BLOCKED_SCREENSHOT || "/tmp/volta-cdn-blocked-ui.png";
const screenshotChunkBlocked = screenshot.replace(/\.png$/i, "") + "-chunk-blocked.png";
const screenshotOffline = screenshot.replace(/\.png$/i, "") + "-offline-cached.png";
const chromePath = process.env.VOLTA_QA_CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

const CARD_SEL = "div[data-station] article.card";
const TILE_RE = /^https?:\/\/tiles\.openfreemap\.org\//i;
const MAP_CHUNK_RE = /\/assets\/maplibre-[^/]+\.(js|css)(\?|$)/i;

// Console/pageerror lines that are direct artifacts of our own request blocking.
const COMMON_NOISE = [
  /ERR_BLOCKED_BY_CLIENT/i,
  /tiles\.openfreemap\.org/i,
  /AJAXError/i,
  /Failed to load resource/i,
  /Failed to fetch/i, // MapLibre tile fetch rejections (map "error" events are unhandled by design)
];
const CHUNK_NOISE = [
  /dynamically imported module/i,
  /Importing a module script failed/i,
  /Unable to preload CSS/i,
  /\/assets\/(maplibre|MapView)-[^\s'"]+/i,
  /error boundary/i, // React's advisory if the lazy chunk failure escapes — structural checks carry the verdict
];

function isExpectedNoise(line, blockChunk) {
  if (COMMON_NOISE.some((re) => re.test(line))) return true;
  if (blockChunk && CHUNK_NOISE.some((re) => re.test(line))) return true;
  return false;
}

/** Load the app with map assets blocked and assert the panel keeps protecting the user. */
async function runScenario(browser, { key, blockChunk, shot, full }) {
  const logs = [];
  const failures = [];
  const fail = (msg) => failures.push(`[${key}] ${msg}`);

  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
    serviceWorkers: "block",
  });
  const page = await context.newPage();
  page.on("console", (msg) => logs.push(`${msg.type()}: ${msg.text()}`));
  page.on("pageerror", (err) => logs.push(`pageerror: ${err.message}`));

  // Block BEFORE navigation, like the old shell test did for unpkg.
  await page.route(TILE_RE, (route) => route.abort("blockedbyclient"));
  if (blockChunk) await page.route(MAP_CHUNK_RE, (route) => route.abort("blockedbyclient"));

  await page.goto(`${baseUrl}/`, { waitUntil: "domcontentloaded", timeout: 15000 });
  // Healthy loads paint cards in a few seconds (instant leads); keep the old 30s ceiling
  // for the primary scenario, a shorter one where a white screen would just burn time.
  const cardsAppeared = await page
    .waitForSelector(CARD_SEL, { timeout: blockChunk ? 15000 : 30000 })
    .then(() => true)
    .catch(() => false);

  const snap = await page.evaluate((cardSel) => {
    const cards = [...document.querySelectorAll(cardSel)];
    const hasLink = (card, text) =>
      [...card.querySelectorAll("a")].some((a) => a.textContent.trim().toLowerCase().includes(text));
    const first = cards[0] || null;
    return {
      title: document.title,
      rootHasContent: ((document.querySelector("#root") || {}).innerText || "").trim().length > 0,
      panel: !!document.querySelector("section"),
      headerH1: (document.querySelector("section h1") || {}).innerText || "",
      cards: cards.length,
      skeletons: document.querySelectorAll(".skeleton").length,
      mapsLinks: cards.filter((c) => hasLink(c, "open in maps")).length,
      detailsButtons: cards.filter((c) =>
        [...c.querySelectorAll("button")].some((b) => b.hasAttribute("aria-expanded") && /Details/.test(b.textContent)),
      ).length,
      searchInput: !!document.querySelector('input[inputmode="search"]'),
      filterChips: document.querySelectorAll('div[role="group"][aria-label="Filters"] dialog button.chip[aria-labelledby][aria-pressed]').length,
      mapCanvas: !!document.querySelector(".maplibregl-canvas"),
      firstCardText: first ? first.innerText.slice(0, 600) : "",
      firstCardBadges: first ? [...first.querySelectorAll(".badge")].map((b) => b.textContent.trim()) : [],
      firstCardPillTitle: first ? (first.querySelector(".pill") || {}).getAttribute?.("title") || "" : "",
    };
  }, CARD_SEL);

  // --- Shared contract: the panel is the product; it must survive any map outage. ---
  if (!snap.rootHasContent)
    fail(
      blockChunk
        ? "app white-screened (#root is empty): the lazy MapView/maplibre chunk failure escaped Suspense — no error boundary guards the map import"
        : "app white-screened (#root is empty) with basemap tiles blocked",
    );
  if (!snap.panel) fail("glass panel <section> did not render");
  if (!cardsAppeared || snap.cards <= 0) fail("station cards did not render");
  if (snap.skeletons !== 0) fail("skeleton placeholders remained after data load");
  if (!/(public chargers|free chargers|chargers ≤ €\d+\.\d{2}\/kWh|nearby)/i.test(snap.headerH1)) fail(`header count line missing (h1="${snap.headerH1}")`);
  if (!snap.searchInput) fail("search input is missing");
  if (snap.filterChips !== 6) fail(`filter chips missing (found ${snap.filterChips})`);

  // Functional: filter chips must still toggle (map-independent interactivity).
  if (snap.cards > 0 && snap.filterChips > 0) {
    try {
      const dialog = await openFilters(page);
      const sourceChip = dialog.getByRole("button", { name: "Source checked", exact: true });
      const before = await sourceChip.getAttribute("aria-pressed", { timeout: 3000 });
      await sourceChip.click({ timeout: 3000 });
      const after = await sourceChip.getAttribute("aria-pressed");
      if (before === after) fail("filter chip did not toggle aria-pressed");
      await sourceChip.click({ timeout: 3000 }); // restore default
      const restored = await sourceChip.getAttribute("aria-pressed");
      if (restored !== before) fail("filter chip did not restore its default state");
      await closeFilters(page);
      const cardsAfter = await page.locator(CARD_SEL).count();
      if (cardsAfter <= 0) fail("list emptied after a filter round-trip");
    } catch (e) {
      fail(`filter toggle interaction failed: ${e.message.split("\n")[0]}`);
    }
  }

  if (full) {
    // --- Old-contract card facts, on the SPA surface. ---
    if (!/Volta/i.test(snap.title)) fail("page title did not load");
    if (snap.cards > 0 && snap.mapsLinks !== snap.cards)
      fail(`not every card has an Open in Maps link (${snap.mapsLinks}/${snap.cards})`);
    if (snap.cards > 0 && snap.detailsButtons !== snap.cards)
      fail(`not every card has a source Details disclosure (${snap.detailsButtons}/${snap.cards})`);
    if (!/(parking|street\/municipal)/i.test(snap.firstCardText)) fail("first card is missing its parking fact");
    if (!snap.firstCardBadges.some((b) => /(Live|Official|Directory|Community)/i.test(b)))
      fail("first card is missing its provenance tier badge (proof)");
    if (!snap.firstCardPillTitle) fail("first card is missing its decision pill reason");

    // Functional: the home-area combobox must still accept and clear an address query.
    // It no longer filters the charger list locally; selecting a geocode result sets the
    // distance origin, so a nonsense address must not erase already-known safe options.
    try {
      const search = page.locator('input[aria-label="Enter home address or city"]');
      await search.fill("qqqqzzzz-no-such-home");
      if ((await search.inputValue()) !== "qqqqzzzz-no-such-home") fail("home-area search did not accept text");
      await page.click('button[aria-label="Clear search"]', { timeout: 3000 });
      if ((await search.inputValue()) !== "") fail("home-area search did not clear");
      await page.waitForSelector(CARD_SEL, { timeout: 6000 });
    } catch (e) {
      fail(`home-area search/clear round-trip failed: ${e.message.split("\n")[0]}`);
    }
  }

  await page.screenshot({ path: shot, fullPage: true }).catch(() => {});

  const unexpectedLogs = logs.filter(
    (line) => /^(error:|pageerror:)/.test(line) && !isExpectedNoise(line, blockChunk),
  );
  if (unexpectedLogs.length) fail(`browser console contains errors: ${unexpectedLogs.slice(0, 5).join(" | ")}`);

  await context.close();
  return { snap, failures, logs: logs.slice(-40) };
}

/** Prime the installed shell once online, then prove an offline reload exposes only stale,
 * source-backed overview leads. Live API responses are intentionally never cached. */
async function runOfflineScenario(browser) {
  const failures = [];
  const logs = [];
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
    serviceWorkers: "allow",
  });
  const page = await context.newPage();
  page.on("console", (msg) => logs.push(`${msg.type()}: ${msg.text()}`));
  page.on("pageerror", (err) => logs.push(`pageerror: ${err.message}`));

  await page.goto(`${baseUrl}/`, { waitUntil: "domcontentloaded", timeout: 20000 });
  await page.waitForSelector(CARD_SEL, { timeout: 30000 });
  await page.evaluate(() => navigator.serviceWorker.ready);
  await page.waitForFunction(() => Boolean(navigator.serviceWorker.controller), null, { timeout: 15000 });

  // Installation precaches the complete hashed build and default national overview. A normal
  // first visit must therefore be enough; no second online priming reload is allowed here.
  await context.setOffline(true);
  await page.reload({ waitUntil: "domcontentloaded", timeout: 20000 });
  await page.waitForSelector(CARD_SEL, { timeout: 30000 });
  await page.waitForFunction(
    () => /Cached evidence · not live/.test(document.querySelector("section .live-dot")?.parentElement?.textContent || ""),
    null,
    { timeout: 15000 },
  );

  const snap = await page.evaluate((cardSel) => {
    const section = document.querySelector("section");
    const status = section?.querySelector(".live-dot")?.parentElement;
    const roleStatuses = [...document.querySelectorAll('[role="status"]')].map((el) => (el.textContent || "").replace(/\s+/g, " ").trim());
    const cards = [...document.querySelectorAll(cardSel)];
    return {
      controlled: Boolean(navigator.serviceWorker.controller),
      cards: cards.length,
      h1: (section?.querySelector("h1")?.textContent || "").replace(/\s+/g, " ").trim(),
      status: (status?.textContent || "").replace(/\s+/g, " ").trim(),
      warning: roleStatuses.find((text) => /Showing cached data|Offline/.test(text)) || "",
      goNowCards: document.querySelectorAll(`${cardSel} .pill-go`).length,
      liveAvailabilityCards: cards.filter((card) => /\d+ free/.test(card.textContent || "")).length,
      notLiveCards: cards.filter((card) => /Not live/.test(card.textContent || "")).length,
    };
  }, CARD_SEL);

  if (!snap.controlled) failures.push("[offline-cached] page is not controlled by the service worker");
  if (snap.cards <= 0) failures.push("[offline-cached] no cached source-backed cards rendered");
  if (!/Cached evidence · not live/.test(snap.status)) failures.push(`[offline-cached] trust status drifted: "${snap.status}"`);
  if (!/Showing cached data/.test(snap.warning) || !/Nothing is live/.test(snap.warning)) {
    failures.push(`[offline-cached] explicit offline/stale warning missing: "${snap.warning}"`);
  }
  if (snap.goNowCards !== 0) failures.push(`[offline-cached] ${snap.goNowCards} cached cards claim Go now`);
  if (snap.liveAvailabilityCards !== 0) failures.push(`[offline-cached] ${snap.liveAvailabilityCards} cached cards claim live free bays`);
  if (snap.notLiveCards !== snap.cards) failures.push(`[offline-cached] only ${snap.notLiveCards}/${snap.cards} cached cards say Not live`);

  await page.screenshot({ path: screenshotOffline, fullPage: true }).catch(() => {});
  await context.setOffline(false);
  const unexpectedLogs = logs.filter(
    (line) => /^(error:|pageerror:)/.test(line) && !isExpectedNoise(line, false),
  );
  if (unexpectedLogs.length) failures.push(`[offline-cached] browser console contains errors: ${unexpectedLogs.slice(0, 5).join(" | ")}`);
  await context.close();
  return { snap, failures, logs: logs.slice(-40) };
}

async function main() {
  const watchdog = setTimeout(() => {
    console.log(JSON.stringify({ ok: false, baseUrl, failures: ["script watchdog hit 110s"] }, null, 2));
    process.exit(1);
  }, 110000);
  watchdog.unref();

  const browser = await chromium.launch({ headless: true, executablePath: chromePath });

  // 1) The modern "CDN blocked": basemap tile host unreachable.
  const tiles = await runScenario(browser, { key: "tiles-blocked", blockChunk: false, shot: screenshot, full: true });
  // 2) The old contract's core: the map *library* itself unreachable.
  const chunk = await runScenario(browser, {
    key: "map-chunk-blocked",
    blockChunk: true,
    shot: screenshotChunkBlocked,
    full: false,
  });
  const offline = await runOfflineScenario(browser);

  await browser.close();

  const failures = [...tiles.failures, ...chunk.failures, ...offline.failures];
  console.log(
    JSON.stringify(
      {
        ok: failures.length === 0,
        baseUrl,
        screenshot,
        screenshotChunkBlocked,
        screenshotOffline,
        result: { tilesBlocked: tiles.snap, mapChunkBlocked: chunk.snap, offlineCached: offline.snap },
        logs: { tilesBlocked: tiles.logs, mapChunkBlocked: chunk.logs, offlineCached: offline.logs },
        failures,
      },
      null,
      2,
    ),
  );
  process.exit(failures.length ? 1 : 0);
}

main().catch((err) => {
  console.error(JSON.stringify({ ok: false, baseUrl, error: `${err.name}: ${err.message}` }, null, 2));
  process.exit(1);
});
