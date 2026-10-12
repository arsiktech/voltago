#!/usr/bin/env node
/*
 * SPA contract: the intelligence-first preview (instant source-backed leads) is replaced
 * IN PLACE by the live charger refresh — no empty flash, honest labels throughout.
 *
 * Ported from the retired static-shell script. Old surface -> SPA surface:
 *   - "Fast source-backed preview" banner  -> national overview state ("Explore Spain" mode
 *     label, "N leads · not live" badge, every visible card marked "Not live").
 *   - preview -> live banner swap          -> geolocation-mocked "Use my location" flow:
 *     transitional "Refreshing…" signal (and optional role=status "live status is loading…"
 *     notice), then "<N> in view/nearby" header + "Near you" origin label.
 *   - card-count never shrinks             -> MutationObserver tracker: the virtualized list
 *     never goes from >0 cards to 0 during the swap, and no skeletons replace real cards.
 *   - /api/intel 200                       -> /api/chargers?overview=true 200 (the SPA no
 *     longer calls /api/intel; the overview query carries the intelligence-preview role).
 *   - /api/chargers 200                    -> non-overview /api/chargers 200 (live fetch).
 * The live (non-overview) response is delayed by VOLTA_QA_LIVE_DELAY_MS so the transitional
 * preview state is deterministically observable (timing only — data is never faked).
 *
 * Console gate scoping (suite convention, same as qa_geolocation_distance.js): the
 * geolocation fly-in currently throws uncaught MapLibre-internal errors ("Cannot read
 * properties of null (reading '0')" → "Invalid LngLat (NaN, NaN)" → "Attempting to run(),
 * but is already running") — the App.tsx double-fly race documented and honestly FAILED by
 * qa_first_render_budget.js, which owns that gap. The map is a backdrop enhancement per
 * docs/spa-dom-contract.md and this script's contract (list swap integrity) is unaffected,
 * so pageerrors whose stack points into the maplibre bundle are recorded transparently in
 * mapLibreInternalErrors but excluded from the gate. Every other error stays fatal.
 */
const { chromium } = require("playwright");

const baseUrl = (process.argv[2] || process.env.VOLTA_QA_BASE_URL || "http://127.0.0.1:8773").replace(/\/$/, "");
const previewBudgetMs = Number(process.env.VOLTA_QA_PREVIEW_BUDGET_MS || "4000");
// A cold server cache triggers a bounded upstream refresh of up to ~45s before the
// live response lands; 60s gives that single-fetch window real margin.
const liveBudgetMs = Number(process.env.VOLTA_QA_LIVE_REFRESH_BUDGET_MS || "60000");
const liveDelayMs = Number(process.env.VOLTA_QA_LIVE_DELAY_MS || "1500");
const chromePath = process.env.VOLTA_QA_CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

const GEO = { latitude: 39.47, longitude: -0.377 }; // Valencia — dense lead + live coverage

function parseBadge(text) {
  // "N live · M leads" (healthy) and "N live · cached fallback" (mixed-live degraded
  // mode) are both live-authority badges; negative lookahead keeps them distinct from
  // the overview "M leads · not live" wording.
  const live = /(\d+)\s*live\s*·\s*(?:(\d+)\s*leads|cached fallback)/.exec(text || "");
  if (live) return { live: Number(live[1]), leads: Number(live[2] ?? 0), overview: false };
  const overview = /(\d+)\s*leads\s*·\s*not live/i.exec(text || "");
  return overview ? { live: 0, leads: Number(overview[1]), overview: true } : null;
}

async function snapshot(page) {
  return page.evaluate(() => {
    const cards = Array.from(document.querySelectorAll("[data-station]"));
    const statuses = Array.from(document.querySelectorAll('div[role="status"]'));
    const notice = statuses.find((el) => /live status is loading/i.test(el.textContent || ""));
    const coverage = statuses.find((el) => /Limited live coverage|Live results may be incomplete|Showing cached data/i.test(el.textContent || ""));
    const clean = (s) => (s || "").replace(/\s+/g, " ").trim();
    return {
      cards: cards.length,
      cardsNotLive: cards.filter((c) => /Not live/.test(c.textContent || "")).length,
      liveCardsHonest: cards
        .filter((c) => !/Not live/.test(c.textContent || ""))
        .every((c) => /\d+\s*free|\d+\s*in use|\d+\s*out|status unknown/i.test(c.textContent || "")),
      h1: clean(document.querySelector("section h1")?.textContent),
      modeLabel: clean(document.querySelector("section h1")?.previousElementSibling?.textContent),
      statusText: clean(document.querySelector("section .live-dot + span")?.textContent),
      badge: clean(document.querySelector("section span.badge")?.textContent),
      noticeVisible: !!notice,
      noticeText: clean(notice?.textContent),
      coverageBanner: clean(coverage?.textContent),
    };
  });
}

async function main() {
  const logs = [];
  const mapLibreInternalErrors = [];
  const responses = [];
  const browser = await chromium.launch({ headless: true, executablePath: chromePath });
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
    permissions: ["geolocation"],
    geolocation: GEO,
  });
  const page = await context.newPage();
  page.on("console", (msg) => logs.push(`${msg.type()}: ${msg.text()}`));
  page.on("pageerror", (err) => {
    // MapLibre-internal throws (backdrop map; known double-fly gap owned by
    // qa_first_render_budget.js) are recorded but not gated — see header comment.
    if (/maplibre/i.test(err.stack || "")) {
      mapLibreInternalErrors.push(err.message);
      return;
    }
    logs.push(`pageerror: ${err.message}`);
  });
  page.on("response", (response) => {
    const url = response.url();
    if (url.includes("/api/chargers") || url.includes("/api/intel") || url.includes("/api/geocode")) {
      responses.push({
        url: url.replace(baseUrl, ""),
        status: response.status(),
        kind: url.includes("/api/chargers") ? (url.includes("overview=true") ? "overview" : "live") : "other",
      });
    }
  });
  // Stretch the live (non-overview) fetch so the transitional preview state is observable.
  await page.route("**/api/chargers*", async (route) => {
    try {
      if (!route.request().url().includes("overview=true")) {
        await new Promise((r) => setTimeout(r, liveDelayMs));
      }
      await route.continue();
    } catch {
      /* page may already be closing */
    }
  });

  const startedAt = Date.now();
  await page.goto(`${baseUrl}/`, { waitUntil: "domcontentloaded", timeout: 20000 });
  await page.waitForSelector("[data-station]", { timeout: previewBudgetMs });
  const previewMs = Date.now() - startedAt;
  const preview = await snapshot(page);

  // Arm the swap tracker BEFORE entering live mode: samples the virtualized list on every
  // DOM mutation + every 40ms, recording wipes, skeletons, the loading notice and the
  // transitional "Refreshing…" fetch signal.
  await page.evaluate(() => {
    const state = {
      everCards: false,
      minCards: Infinity,
      wipedToZero: false,
      skeletonSeen: false,
      refreshingSeen: false,
      noticeSeen: false,
      noticeText: "",
      samples: 0,
    };
    const sample = () => {
      state.samples += 1;
      const cards = document.querySelectorAll("[data-station]").length;
      if (cards > 0) state.everCards = true;
      if (state.everCards) {
        if (cards < state.minCards) state.minCards = cards;
        if (cards === 0) state.wipedToZero = true;
      }
      if (document.querySelector("section .skeleton")) state.skeletonSeen = true;
      const statusEl = document.querySelector("section .live-dot + span");
      if (/Refreshing…/.test(statusEl?.textContent || "")) state.refreshingSeen = true;
      const notice = Array.from(document.querySelectorAll('div[role="status"]')).find((el) =>
        /live status is loading/i.test(el.textContent || ""),
      );
      if (notice) {
        state.noticeSeen = true;
        state.noticeText = (notice.textContent || "").replace(/\s+/g, " ").trim();
      }
    };
    const mo = new MutationObserver(sample);
    mo.observe(document.body, { childList: true, subtree: true, characterData: true });
    const iv = setInterval(sample, 40);
    sample();
    window.__voltaSwap = {
      state,
      stop() {
        mo.disconnect();
        clearInterval(iv);
        sample();
        return state;
      },
    };
  });

  // Enter live mode via the mocked geolocation.
  const liveResponsePromise = page.waitForResponse(
    (r) => r.url().includes("/api/chargers") && !r.url().includes("overview=true") && r.status() === 200,
    { timeout: liveBudgetMs },
  );
  await page.click('button[aria-label="Use my location"]');
  const liveResponse = await liveResponsePromise;
  let liveBody = null;
  try {
    liveBody = await liveResponse.json();
  } catch {
    /* asserted below */
  }

  // Wait for the swap to settle: live header, fetch finished, loading notice gone.
  // Healthy path: status "Live…". Honest degraded path: the live fetch completed with a
  // no-authority response (badge exactly "Not live", non-lead live-source cards rendered,
  // live count never claimed). Both count as "the preview was replaced by the live query".
  await page.waitForFunction(
    () => {
      const h1 = document.querySelector("section h1")?.textContent || "";
      const status = document.querySelector("section .live-dot + span")?.textContent || "";
      const badge = document.querySelector("section span.badge")?.textContent?.trim() || "";
      const notice = Array.from(document.querySelectorAll('div[role="status"]')).some((el) =>
        /live status is loading/i.test(el.textContent || ""),
      );
      const states = {
        cards: document.querySelectorAll("[data-station]").length,
      };
      const degraded = badge === "Not live" && states.cards > 0;
      return /\b(?:in view|nearby)\b/.test(h1) && (/^Live/.test(status.trim()) || degraded) && !notice;
    },
    null,
    { timeout: liveBudgetMs },
  );
  const liveMs = Date.now() - startedAt;
  // Let the map fly-in finish narrowing the viewport while the tracker keeps watching.
  await page.waitForTimeout(1200);
  const swap = await page.evaluate(() => window.__voltaSwap.stop());
  const live = await snapshot(page);
  await browser.close();

  const failures = [];

  // 1) Instant preview: source-backed national overview paints fast and honestly.
  if (previewMs > previewBudgetMs) failures.push(`preview took ${previewMs}ms > ${previewBudgetMs}ms`);
  if (preview.cards <= 0) failures.push("preview rendered no station cards");
  const previewBadge = parseBadge(preview.badge);
  if (preview.modeLabel !== "Explore Spain" || !/public chargers/.test(preview.h1)) {
    failures.push(
      `initial render did not expose the source-backed national overview state (mode="${preview.modeLabel}", h1="${preview.h1}")`,
    );
  }
  if (!previewBadge || !previewBadge.overview || previewBadge.leads <= 0) {
    failures.push(`preview badge should report leads-only content ("N leads · not live"), got "${preview.badge}"`);
  }
  if (preview.cards > 0 && preview.cardsNotLive !== preview.cards) {
    failures.push(
      `preview should contain only source-backed lead cards marked "Not live" (${preview.cardsNotLive}/${preview.cards} visible cards marked)`,
    );
  }

  // 2) The swap: live refresh replaces preview in place, within budget, with honest signals.
  if (liveMs > previewBudgetMs + liveBudgetMs) {
    failures.push(`live refresh took ${liveMs}ms > ${previewBudgetMs + liveBudgetMs}ms`);
  }
  if (!swap.refreshingSeen) failures.push('live fetch never surfaced the transitional "Refreshing…" signal');
  if (swap.wipedToZero) failures.push("station list wiped to 0 cards during the preview→live swap (empty flash)");
  if (swap.skeletonSeen) failures.push("skeleton placeholders replaced real cards during the swap");
  if (swap.noticeSeen && !/Showing known chargers from public sources/i.test(swap.noticeText)) {
    failures.push(`loading notice text drifted from contract: "${swap.noticeText}"`);
  }
  if (live.noticeVisible) failures.push("'live status is loading' notice still visible after live data arrived");

  // 3) Live state: preview labels are gone, live data is surfaced and explained.
  if (!/^\d+ (?:in view|nearby)$/.test(live.h1)) failures.push(`header did not switch to live "<N> in view/nearby" mode (h1="${live.h1}")`);
  if (live.modeLabel !== "Near you") {
    failures.push(`live refresh did not replace the overview mode label (got "${live.modeLabel}")`);
  }
  if (
    (liveBody?.meta?.coverage?.mode === "degraded" || liveBody?.meta?.coverage?.is_degraded === true) &&
    !live.coverageBanner
  ) {
    failures.push("degraded live coverage is not explained by a coverage banner");
  }
  if (live.cards <= 0) failures.push("live refresh left the list with no visible cards");
  const liveBadge = parseBadge(live.badge);
  const liveStations = Array.isArray(liveBody?.stations) ? liveBody.stations.length : 0;
  if (liveStations <= 0) failures.push("live /api/chargers response contained no live (non-lead) stations");
  // Honest degraded live: the response carried live rows but no fresh live authority
  // (badge "Not live", never a claimed count). The healthy "N live · M leads" matching
  // expected route-ready count stays required whenever authority exists.
  const degradedNoAuthority =
    live.badge === "Not live" && live.coverageBanner && live.cards > 0;
  const expectedLiveReady = Number(liveBody?.meta?.coverage?.live_route_ready);
  if (!liveBadge || liveBadge.overview) {
    if (!degradedNoAuthority) {
      failures.push(`live refresh did not switch to the route-ready/live badge (got "${live.badge}")`);
    } else if (liveBody?.cache?.fresh_live_authority !== false) {
      failures.push(`badge "Not live" lacks a matching no-authority response (cache=${JSON.stringify(liveBody?.cache)})`);
    }
  } else if (!Number.isFinite(expectedLiveReady)) {
    failures.push("live response omitted meta.coverage.live_route_ready");
  } else if (liveBadge.live !== expectedLiveReady) {
    failures.push(`live badge route-ready count ${liveBadge.live} does not match API coverage ${expectedLiveReady}`);
  }
  if (!live.liveCardsHonest) {
    failures.push("a visible live card lacks availability/status markers (free / in use / status unknown)");
  }

  // 4) API + console contract.
  if (!responses.some((r) => r.kind === "overview" && r.status === 200)) {
    failures.push("/api/chargers?overview=true 200 (intelligence preview) not observed");
  }
  if (!responses.some((r) => r.kind === "live" && r.status === 200)) {
    failures.push("live /api/chargers 200 not observed");
  }
  const unexpectedErrors = logs.filter(
    // Console mirrors of the same MapLibre-internal throws carry the bundle URL in their
    // stack text; exclude those, keep every other error/pageerror fatal.
    (line) => /^error:|^pageerror:/.test(line) && !/\/assets\/maplibre-[^\s'"]+\.js/i.test(line),
  );
  if (unexpectedErrors.length) failures.push(`browser console contains errors: ${unexpectedErrors.join(" | ")}`);

  console.log(
    JSON.stringify(
      { ok: failures.length === 0, baseUrl, previewBudgetMs, liveBudgetMs, liveDelayMs, previewMs, liveMs, preview, swap, live, responses, logs, mapLibreInternalErrors, failures },
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
