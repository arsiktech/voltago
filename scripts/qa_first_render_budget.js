#!/usr/bin/env node
/*
 * First-useful-render budget for the Volta SPA (mobile viewport).
 *
 * Contract (ported from the retired static shell):
 *  1. Page title identifies Volta.
 *  2. domcontentloaded lands within a tight budget.
 *  3. The first rendered station card ([data-station] h3) paints within the budget —
 *     the national overview must paint leads without waiting on any live fetch.
 *  4. At least N cards are rendered and no skeleton loaders remain once cards exist.
 *  5. The intelligence layer request (/api/chargers?…overview=true, successor of /api/intel)
 *     is observed before the first useful render, and no first-render API call errors.
 *  6. The header mode label + live status row explain the data state, the live/leads count
 *     badge is present, and the "Data honesty" footer disclosure exists.
 *  7. Entering live mode (mocked geolocation → "Use my location") keeps cards on screen
 *     within an instant budget — the intelligence-first layer must paint leads well before
 *     a full live fetch could finish — and a live /api/chargers request (lat/lon, non-
 *     overview) actually starts (successor of the old background live-refresh assertion).
 *  8. No console/page errors.
 *
 * RESOLVED APP GAP (2026-07-03): entering live mode used to throw an uncaught MapLibre
 * TypeError ("Cannot read properties of null (reading '0')" in _calcMatrices) and wedge
 * the camera at NaN zoom until reload. Root cause (proven by minimal repro, NOT the
 * suspected competing double-flyTo): camera calls passing `duration: cond ? 0 : undefined`
 * hand MapLibre a present-but-undefined duration key, which flyTo coerces
 * (`if ('duration' in options) options.duration = +options.duration` → NaN) into a NaN
 * ease — the very first frame does setZoom(NaN), the projection matrix goes singular, and
 * mat4.invert returns null. flyTo({center, zoom, speed}) from the identical start state is
 * clean; adding `duration: undefined` alone crashes. Fixed in MapView.tsx by spreading the
 * duration key in only when it has a value (reduced-motion 0). This script waits for the
 * map canvas plus a short settle before clicking so the fly actually runs (MapView's
 * readyRef gate skips flys before `load`), keeping the capture deterministic.
 * Assertion 8 below guards the regression.
 */
const { chromium } = require("playwright");

const baseUrl = (process.argv[2] || process.env.VOLTA_QA_BASE_URL || "http://127.0.0.1:8773").replace(/\/$/, "");
const budgetMs = Number(process.env.VOLTA_QA_FIRST_RENDER_BUDGET_MS || "4000");
const domBudgetMs = Number(process.env.VOLTA_QA_DOM_BUDGET_MS || "2000");
const liveInstantBudgetMs = Number(process.env.VOLTA_QA_LIVE_INSTANT_BUDGET_MS || "4000");
const minCards = Number(process.env.VOLTA_QA_MIN_FIRST_RENDER_CARDS || "1");
const chromePath = process.env.VOLTA_QA_CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

const CARD_TITLE = "[data-station] h3";
const isChargersUrl = (url) => url.includes("/api/chargers");
const isOverviewUrl = (url) => isChargersUrl(url) && url.includes("overview=true");
const isCompactOverviewUrl = (url) => isOverviewUrl(url) && url.includes("compact=true");
const isLiveUrl = (url) => isChargersUrl(url) && !url.includes("overview=true") && url.includes("lat=");
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const logs = [];
  const apiRequests = [];
  const apiResponses = [];
  const failures = [];

  const browser = await chromium.launch({ headless: true, executablePath: chromePath });
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
    geolocation: { latitude: 39.47, longitude: -0.377 },
    permissions: ["geolocation"],
  });
  const page = await context.newPage();
  page.on("console", (msg) => logs.push(`${msg.type()}: ${msg.text()}`));
  page.on("pageerror", (err) => logs.push(`pageerror: ${err.message}`));
  page.on("request", (request) => {
    const url = request.url();
    if (url.includes("/api/chargers") || url.includes("/api/intel")) apiRequests.push(url);
  });
  page.on("response", (response) => {
    const url = response.url();
    if (url.includes("/api/chargers") || url.includes("/api/intel")) {
      apiResponses.push({ url, status: response.status() });
    }
  });

  // --- Phase 1: cold open → national overview first useful render ---
  const startedAt = Date.now();
  await page.goto(`${baseUrl}/`, { waitUntil: "domcontentloaded", timeout: Math.max(5000, domBudgetMs + 3000) });
  const domContentLoadedMs = Date.now() - startedAt;

  let firstCardMs = null;
  let firstCardRendered = true;
  try {
    await page.waitForSelector(CARD_TITLE, { timeout: budgetMs + 5000 });
    firstCardMs = Date.now() - startedAt;
  } catch {
    firstCardRendered = false;
    failures.push(`first station card ([data-station] h3) did not render within ${budgetMs + 5000}ms`);
  }
  const requestsAtFirstRender = apiRequests.slice();

  const state = await page.evaluate(() => {
    const clean = (s) => (s || "").replace(/\s+/g, " ").trim();
    const h1 = document.querySelector("section h1");
    const liveDot = document.querySelector("span.live-dot");
    const honesty = document.querySelector('button[aria-controls="data-honesty-panel"]');
    return {
      title: document.title,
      cards: document.querySelectorAll("[data-station]").length,
      skeletons: document.querySelectorAll(".skeleton").length,
      heading: clean(h1 && h1.textContent),
      modeLabel: clean(h1 && h1.previousElementSibling && h1.previousElementSibling.textContent),
      liveStatus: clean(liveDot && liveDot.parentElement && liveDot.parentElement.textContent),
      countBadge: clean((document.querySelector("section .badge") || {}).textContent),
      honestyFooter: clean(honesty && honesty.textContent),
      firstCard: clean((document.querySelector("[data-station] h3") || {}).textContent),
      firstCardPill: clean((document.querySelector("[data-station] .pill") || {}).textContent),
    };
  });

  if (!/Volta/i.test(state.title)) failures.push("page title did not identify Volta");
  if (domContentLoadedMs > domBudgetMs) failures.push(`domcontentloaded took ${domContentLoadedMs}ms > ${domBudgetMs}ms`);
  if (firstCardMs !== null && firstCardMs > budgetMs) failures.push(`first station card took ${firstCardMs}ms > ${budgetMs}ms`);
  if (state.cards < minCards) failures.push(`rendered ${state.cards} cards < minimum ${minCards}`);
  if (firstCardRendered && state.skeletons !== 0) failures.push(`skeletons still visible after first card: ${state.skeletons}`);
  if (!requestsAtFirstRender.some(isOverviewUrl)) {
    failures.push("no intelligence-layer request (/api/chargers overview=true) observed before first useful render");
  }
  if (!requestsAtFirstRender.some(isCompactOverviewUrl)) {
    failures.push("first-use overview did not request compact=true; ingestion-only bulk would delay parsing");
  }
  if (state.modeLabel !== "Explore Spain") {
    failures.push(`mode label did not explain data state: "${state.modeLabel}"`);
  }
  if (!/Saved chargers · not live|Refreshing known chargers/i.test(state.liveStatus)) {
    failures.push(`overview trust status row missing/unexplained: "${state.liveStatus}"`);
  }
  if (!/\d+ leads · not live/.test(state.countBadge)) failures.push(`overview not-live count badge missing: "${state.countBadge}"`);
  if (!state.honestyFooter) failures.push("Data honesty footer disclosure missing after first render");
  if (firstCardRendered && !state.firstCardPill) failures.push("first card is missing its decision pill (.pill)");

  // --- Phase 2: enter live mode — intelligence-first leads must stay painted instantly ---
  // The map backdrop must be up before the click: if "Use my location" beats MapLibre's
  // `load` event, MapView's flyTarget effect skips the fly entirely and the run would
  // false-green the known fly crash (see header). A real user sees the map, then taps.
  let mapCanvasMs = null;
  let liveCardsMs = null;
  let liveState = null;
  if (firstCardRendered) {
    try {
      await page.waitForSelector(".maplibregl-canvas", { timeout: 5000 });
      mapCanvasMs = Date.now() - startedAt;
      await sleep(900); // past MapLibre `load` (~400-500ms here) so the live-mode fly really runs
    } catch {
      failures.push("map canvas (.maplibregl-canvas) never appeared on a healthy server — backdrop missing");
    }
    const liveStartedAt = Date.now();
    await page.click('button[aria-label="Use my location"]');
    try {
      await page.waitForFunction(
        () => {
          const h1 = document.querySelector("section h1");
          return !!(h1 && /\b(?:in view|nearby)\b/.test(h1.textContent || "") && document.querySelector("[data-station] h3"));
        },
        { timeout: liveInstantBudgetMs + 4000 },
      );
      liveCardsMs = Date.now() - liveStartedAt;
    } catch {
      failures.push(`live mode did not keep station cards painted within ${liveInstantBudgetMs + 4000}ms of "Use my location"`);
    }
    if (liveCardsMs !== null && liveCardsMs > liveInstantBudgetMs) {
      failures.push(`live-mode instant leads took ${liveCardsMs}ms > ${liveInstantBudgetMs}ms (should paint well before a live fetch finishes)`);
    }
    // The live refresh request itself must start (it may finish later — not awaited here).
    const livePollDeadline = Date.now() + 3000;
    while (!apiRequests.some(isLiveUrl) && Date.now() < livePollDeadline) await sleep(100);
    if (!apiRequests.some(isLiveUrl)) failures.push("no live /api/chargers request (lat/lon, non-overview) started after entering live mode");

    liveState = await page.evaluate(() => {
      const clean = (s) => (s || "").replace(/\s+/g, " ").trim();
      const h1 = document.querySelector("section h1");
      return {
        heading: clean(h1 && h1.textContent),
        modeLabel: clean(h1 && h1.previousElementSibling && h1.previousElementSibling.textContent),
        cards: document.querySelectorAll("[data-station]").length,
        instantNotice: clean(
          (Array.from(document.querySelectorAll('div[role="status"]')).find((el) =>
            /live status is loading/i.test(el.textContent || ""),
          ) || {}).textContent,
        ),
      };
    });
    if (liveState.cards < minCards) failures.push(`live mode rendered ${liveState.cards} cards < minimum ${minCards}`);
  }

  // Let the post-fly error cascade (if any) land before judging console cleanliness.
  await sleep(1000);

  const badResponses = apiResponses.filter((r) => r.status >= 400);
  if (badResponses.length) failures.push(`first-render API returned error status: ${badResponses.map((r) => `${r.status} ${r.url}`).join(", ")}`);
  const unexpectedLogs = logs.filter((line) => /^error:|^pageerror:/.test(line));
  if (unexpectedLogs.length) {
    const shown = unexpectedLogs.slice(0, 10);
    const more = unexpectedLogs.length - shown.length;
    failures.push(`browser console contains errors: ${shown.join(" | ")}${more > 0 ? ` | +${more} more` : ""}`);
  }

  await browser.close();

  console.log(JSON.stringify({
    ok: failures.length === 0,
    baseUrl,
    budgetMs,
    domBudgetMs,
    liveInstantBudgetMs,
    domContentLoadedMs,
    firstCardMs,
    mapCanvasMs,
    liveCardsMs,
    apiRequests,
    apiResponses,
    state,
    liveState,
    logs,
    failures,
  }, null, 2));
  process.exit(failures.length ? 1 : 0);
}

main().catch((err) => {
  console.error(JSON.stringify({ ok: false, baseUrl, error: `${err.name}: ${err.message}` }, null, 2));
  process.exit(1);
});
