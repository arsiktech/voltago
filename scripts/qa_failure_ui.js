#!/usr/bin/env node
/* Live-failure honesty contract for Volta (React SPA).
 *
 * Ported from the retired static shell (error-card / stale-pins-hidden UI) to the SPA:
 *  1. Enter live mode (mocked geolocation, Valencia) with every non-overview
 *     /api/chargers request forced to HTTP 502. The app must NOT present any card as
 *     live/available: either the error card ("Couldn't reach the live source" + Try again)
 *     or kept instant leads that are ALL labeled "Not live", with a leads/not-live badge and
 *     no route-ready guidance. Overview=true requests stay working (instant leads
 *     are honest by design).
 *  2. Reload with ALL /api/chargers requests 502'd: the error card with a Try again
 *     button and bounded public recovery copy must render, with zero station cards and
 *     honest zero counts. Raw HTTP bodies, request URLs, and driver coordinates stay private.
 *  3. Restore the API and click Try again: the list must recover.
 * Map pins are canvas-drawn (not DOM) and derive from the same rows as the list, so the
 * old "no stale markers" assertion is covered by the list-honesty assertions above.
 *
 * Requires Playwright (installed at the repo root) and a running local server:
 *   node scripts/qa_failure_ui.js http://127.0.0.1:8773
 */
const { chromium } = require("playwright");
const {
  installDeterministicBasemap,
  unexpectedBrowserErrors,
} = require("./qa_browser_harness");

const baseUrl = (process.argv[2] || process.env.VOLTA_QA_BASE_URL || "http://127.0.0.1:8773").replace(/\/$/, "");
const screenshot = process.env.VOLTA_QA_FAILURE_SCREENSHOT || "/tmp/volta-mobile-live-failure.png";
const chromePath = process.env.VOLTA_QA_CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const userLocation = { latitude: 39.47, longitude: -0.377 }; // Valencia (cheat-sheet live-mode flow)

function snapshotPage() {
  const clean = (t) => String(t || "").replace(/\s+/g, " ").trim();
  const cards = [...document.querySelectorAll("[data-station]")];
  const cardInfo = cards.map((el) => {
    const badges = [...el.querySelectorAll(".badge")].map((b) => clean(b.textContent));
    const availabilityRows = [...el.querySelectorAll("[data-availability]")];
    const availability = availabilityRows[0];
    const facts = availability ? [...availability.querySelectorAll("span")].map((fact) => clean(fact.textContent)) : [];
    const visible = Boolean(availability?.getClientRects().length && getComputedStyle(availability).visibility !== "hidden");
    return {
      name: clean(el.querySelector("h3")?.textContent),
      notLive: availabilityRows.length === 1 && visible && availability?.getAttribute("data-availability") === "not-live" && facts.includes("Not live"),
      // Any way a card could present itself as live/available right now:
      claimsLive:
        badges.includes("Live") ||
        !!el.querySelector('[data-availability="live"]') ||
        !!el.querySelector('.pill-go') ||
        facts.some((fact) => /^(?:\d+ free|\d+ in use|\d+ out|Live · status unknown)$/.test(fact)),
    };
  });
  const errorCardEl = [...document.querySelectorAll(".card")].find((c) =>
    /couldn.t reach the live source/i.test(c.textContent || ""),
  );
  const emptyCardEl = [...document.querySelectorAll(".card")].find((c) =>
    /no chargers match these filters|no charger nearby yet/i.test(c.textContent || ""),
  );
  return {
    h1: clean(document.querySelector("section h1")?.textContent),
    h1Count: clean(document.querySelector("section h1 .tabular-nums")?.textContent),
    modeLabel: clean(document.querySelector("section h1")?.previousElementSibling?.textContent),
    cardCount: cards.length,
    notLiveCount: cardInfo.filter((c) => c.notLive).length,
    liveClaimCount: cardInfo.filter((c) => c.claimsLive).length,
    liveClaimSamples: cardInfo.filter((c) => c.claimsLive).slice(0, 3).map((c) => c.name),
    countsBadge:
      [...document.querySelectorAll(".badge")]
        .map((el) => clean(el.textContent))
        .find((text) => /^(?:\d+\s*live\s*·\s*\d+\s*leads|\d+\s*leads\s*·\s*not live)$/i.test(text)) || "",
    readyGuidance: /^\d+ Go now option/.test(clean(document.querySelector("[data-driver-guidance]")?.textContent)),
    readyNotice: /Route-ready charger found/.test(document.body.textContent || ""),
    loadingNotice: [...document.querySelectorAll('[role="status"]')].some((el) =>
      /live status is loading/i.test(el.textContent || ""),
    ),
    errorCard: {
      present: !!errorCardEl,
      text: clean(errorCardEl?.textContent),
      tryAgain: !!(errorCardEl && [...errorCardEl.querySelectorAll("button")].some((b) => /try again/i.test(b.textContent || ""))),
    },
    emptyState: !!emptyCardEl,
  };
}

async function main() {
  const logs = [];
  const failures = [];
  const browser = await chromium.launch({ headless: true, executablePath: chromePath });
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    isMobile: true,
    geolocation: userLocation,
    // This contract owns direct network failure handling. Offline service-worker
    // behavior has its own dedicated contract and must not mask the forced 502s.
    serviceWorkers: "block",
  });
  await context.grantPermissions(["geolocation"], { origin: baseUrl });
  const page = await context.newPage();
  // Tile availability is outside this failure-path contract. Keep the MapLibre backdrop
  // mounted deterministically; qa_cdn_blocked_ui.js owns real basemap-outage behavior.
  await installDeterministicBasemap(page);
  page.on("console", (msg) => logs.push(`${msg.type()}: ${msg.text()}`));
  page.on("pageerror", (err) => {
    const frame = (err.stack || "").split("\n").find((l) => l.includes("http")) || "";
    logs.push(`pageerror: ${err.message} @ ${frame.trim()}`);
  });

  // Interception: "liveOnly" 502s non-overview /api/chargers (instant leads keep working),
  // "all" 502s every /api/chargers, "off" lets everything through.
  let blockMode = "liveOnly";
  await page.route("**/api/chargers*", (route) => {
    const isOverview = /[?&]overview=true/.test(route.request().url());
    if (blockMode === "off" || (blockMode === "liveOnly" && isOverview)) return route.continue();
    return route.fulfill({
      status: 502,
      contentType: "application/json",
      body: JSON.stringify({ error: "Live source failed", detail: "forced QA failure" }),
    });
  });

  // ---- Phase 1: healthy national overview, then live mode with the live fetch 502'd ----
  await page.goto(`${baseUrl}/`, { waitUntil: "domcontentloaded", timeout: 20000 });
  await page.waitForSelector("[data-station]", { timeout: 20000 });

  const liveRequest = page.waitForRequest(
    (req) => req.url().includes("/api/chargers?") && !/[?&]overview=true/.test(req.url()),
    { timeout: 15000 },
  );
  await page.click('button[aria-label="Use my location"]');
  await liveRequest;
  // Settle: react-query retries the 502 a few times (~8s) before surfacing the error.
  await page.waitForFunction(
    () => {
      const spinning = !!document.querySelector('button[aria-label="Refresh live data"] .animate-spin');
      const notice = [...document.querySelectorAll('[role="status"]')].some((el) =>
        /live status is loading/i.test(el.textContent || ""),
      );
      return !spinning && !notice;
    },
    null,
    { timeout: 30000 },
  );
  await page.waitForTimeout(400);

  const liveFailure = await page.evaluate(snapshotPage);
  if (!/^\d+ (?:in view|nearby)$/i.test(liveFailure.h1)) {
    failures.push(`app did not enter live mode after Use my location (h1: "${liveFailure.h1}")`);
  }
  if (liveFailure.errorCard.present) {
    if (!liveFailure.errorCard.tryAgain) failures.push("live-failure error card has no Try again action");
    if (liveFailure.cardCount !== 0) failures.push("error card shown but stale station cards remain alongside it");
  } else if (liveFailure.cardCount > 0) {
    if (liveFailure.liveClaimCount !== 0) {
      failures.push(
        `live 502 still presents ${liveFailure.liveClaimCount} card(s) as live/available (${liveFailure.liveClaimSamples.join("; ")})`,
      );
    }
    if (liveFailure.notLiveCount !== liveFailure.cardCount) {
      failures.push(
        `kept cards are not all labeled Not live (${liveFailure.notLiveCount}/${liveFailure.cardCount})`,
      );
    }
    if (!/^\d+\s*leads\s*·\s*not live$/i.test(liveFailure.countsBadge)) {
      failures.push(`source-backed badge is not honestly not-live after live 502 (badge: "${liveFailure.countsBadge}")`);
    }
    if (liveFailure.readyGuidance || liveFailure.readyNotice) failures.push("route-ready guidance survived live failure");
  } else {
    failures.push("live 502 produced neither the error card nor honest Not-live leads (list went empty)");
  }

  // ---- Phase 2: total API failure on a fresh load -> visible error card, zero stale cards ----
  blockMode = "all";
  await page.goto(`${baseUrl}/`, { waitUntil: "domcontentloaded", timeout: 20000 });
  await page.waitForFunction(
    () => [...document.querySelectorAll(".card")].some((c) => /couldn.t reach the live source/i.test(c.textContent || "")),
    null,
    { timeout: 25000 },
  );
  await page.screenshot({ path: screenshot, fullPage: true });

  const totalFailure = await page.evaluate(snapshotPage);
  if (!totalFailure.errorCard.present) failures.push("missing visible live failure card on total API failure");
  if (!totalFailure.errorCard.tryAgain) failures.push("total-failure error card has no Try again action");
  if (!/temporarily unavailable/i.test(totalFailure.errorCard.text)) {
    failures.push(`error card does not expose bounded recovery guidance (text: "${totalFailure.errorCard.text}")`);
  }
  if (/HTTP\s*502|forced QA failure|\/api\/chargers|(?:lat|lon|bbox)=/i.test(totalFailure.errorCard.text)) {
    failures.push(`error card leaks transport/request detail (text: "${totalFailure.errorCard.text}")`);
  }
  if (totalFailure.cardCount !== 0) failures.push("failure state still shows station cards");
  if (totalFailure.h1Count !== "0") failures.push(`failure header count is not an honest 0 (got "${totalFailure.h1Count}")`);
  if (!/^0\s*leads\s*·\s*not live$/i.test(totalFailure.countsBadge)) {
    failures.push(`failure count badge still claims data (badge: "${totalFailure.countsBadge}")`);
  }
  if (totalFailure.readyGuidance || totalFailure.readyNotice) {
    failures.push("route-ready guidance survived total failure");
  }

  // ---- Phase 3: API restored -> Try again must actually recover the list ----
  blockMode = "off";
  let recovery = { recovered: false, cardCount: 0, countsBadge: "" };
  try {
    await page.click('button:has-text("Try again")');
    await page.waitForSelector("[data-station]", { timeout: 20000 });
    const snap = await page.evaluate(snapshotPage);
    recovery = { recovered: snap.cardCount > 0, cardCount: snap.cardCount, countsBadge: snap.countsBadge };
  } catch {
    // fallthrough: recovery.recovered stays false
  }
  if (!recovery.recovered) failures.push("Try again did not recover the list once the API was reachable again");

  await browser.close();

  const candidateErrors = logs.filter((line) => {
    if (/^error: Failed to load resource: the server responded with a status of 502/i.test(line)) return false;
    if (/net::ERR_ABORTED/i.test(line)) return false; // in-flight asset/tile fetches cancelled by the phase-2 reload
    return true;
  });
  const unexpectedErrors = unexpectedBrowserErrors(candidateErrors);
  if (unexpectedErrors.length) failures.push(`browser console contains unexpected errors: ${unexpectedErrors.join(" | ")}`);

  const result = { liveFailure, totalFailure, recovery };
  console.log(JSON.stringify({ ok: failures.length === 0, baseUrl, screenshot, result, logs, failures }, null, 2));
  process.exit(failures.length ? 1 : 0);
}

main().catch((err) => {
  console.error(JSON.stringify({ ok: false, baseUrl, error: `${err.name}: ${err.message}` }, null, 2));
  process.exit(1);
});
