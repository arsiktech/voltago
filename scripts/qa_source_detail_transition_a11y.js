#!/usr/bin/env node
/*
 * Accessibility contract for the source-detail disclosure and the source-backed -> live
 * transition, ported from the retired static shell to the React SPA (docs/spa-dom-contract.md).
 *
 * Protected behaviors (old shell -> SPA surface):
 *  1. Mobile 390x844 load renders station cards; no horizontal page overflow in any phase.
 *  2. The initial state is honestly labeled as source-backed, not live (was the
 *     "Fast source-backed preview" banner; now: "Explore Spain" mode label, leads-only list
 *     with "Not live" cards, "N live · M leads" badge).
 *  3. Every card exposes a source-detail disclosure (was <details><summary>; now the "Details"
 *     button with aria-expanded) that opens to provenance content: the Why line, charging/
 *     parking claims, corroboration, and Public sources — with every source link an http(s)
 *     href and visible text.
 *  4. Disclosure a11y: aria-expanded false->true, keyboard-only operation (Enter and Space),
 *     focus stays on the trigger across toggles, Escape and a second activation collapse, and
 *     the trigger identifies WHICH station it discloses (was a station-specific summary
 *     aria-label).
 *  5. Provenance/status messaging is announced: coverage / "live status is loading" banners
 *     carry role="status" (implicit aria-live=polite + aria-atomic), and the persistent live
 *     status row ("N live · M leads" or "N live · cached fallback") carries live-region semantics (was #sourceCard
 *     role=status aria-live=polite aria-atomic=true and #trustStrip aria-live=polite).
 *  6. The source-backed state transitions to live data (geolocate -> "N in view/nearby", a live
 *     source card renders even when zero stations are route-ready, the loading notice clears), and the disclosure contract holds
 *     again on a LIVE card.
 *  7. No browser console errors or page errors attributable to app code. The MapLibre
 *     canvas is an aria-hidden backdrop enhancement (docs/spa-dom-contract.md): headless
 *     Chrome's software WebGL makes the maplibre chunk throw projection/frame errors a
 *     GPU browser doesn't hit — those are reported in mapBackdropNoise but are non-fatal,
 *     the same policy as qa_driver_readiness.js / qa_geolocation_distance.js.
 */
const { chromium } = require("playwright");

const baseUrl = (process.argv[2] || process.env.VOLTA_QA_BASE_URL || "http://127.0.0.1:8773").replace(/\/$/, "");
const liveBudgetMs = Number(process.env.VOLTA_QA_LIVE_REFRESH_BUDGET_MS || "30000");
const chromePath = process.env.VOLTA_QA_CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function compact(text) {
  return String(text || "").replace(/\s+/g, " ").trim();
}

/* Map-backdrop noise: the MapLibre canvas is an aria-hidden enhancement, and headless
 * Chrome's software WebGL makes the bundled maplibre chunk throw errors a GPU browser
 * doesn't hit. They are surfaced (mapBackdropNoise in the JSON result) but not fatal —
 * same list as qa_driver_readiness.js / qa_geolocation_distance.js. Any other console
 * error / pageerror still fails the contract. */
const MAP_BACKDROP_NOISE = [
  /\/assets\/maplibre-[^\s'")]+/i,
  /Invalid LngLat object: \(NaN, NaN\)/i,
  /Attempting to run\(\), but is already running/i,
  /fromInvProjectionMatrix|getCameraFrustum/i,
];
const isMapBackdropNoise = (line) => MAP_BACKDROP_NOISE.some((re) => re.test(line));

/** Snapshot of the panel header/status area + first card, evaluated in the page. */
async function readPhase(page, phase) {
  return page.evaluate((label) => {
    const compactText = (v) => String(v || "").replace(/\s+/g, " ").trim();
    const section = document.querySelector("section");
    const h1 = section ? section.querySelector("h1") : null;
    const placeLabel = h1 && h1.previousElementSibling ? compactText(h1.previousElementSibling.textContent) : "";
    const dot = section ? section.querySelector(".live-dot") : null;
    const statusRow = dot ? dot.parentElement : null;
    const badgeText = statusRow ? compactText((statusRow.querySelector(".badge") || {}).textContent) : "";
    const badgeMatch = badgeText.match(/(\d+)\s*live\s*·\s*(\d+)\s*leads/);
    const mixedBadgeMatch = badgeText.match(/(\d+)\s*live\s*·\s*cached fallback/i);
    const overviewBadgeMatch = badgeText.match(/(\d+)\s*leads\s*·\s*not live/i);
    const firstCard = document.querySelector('[data-station] article');
    const firstCardText = compactText(firstCard ? firstCard.textContent : "");

    // Provenance/status banners that must be announced to assistive tech.
    const bannerMarkers = ["Limited live coverage", "Showing cached data", "live status is loading"];
    const banners = [];
    if (section) {
      for (const marker of bannerMarkers) {
        const matches = [...section.querySelectorAll("div")].filter((el) => (el.textContent || "").includes(marker));
        if (!matches.length) continue;
        const innermost = matches[matches.length - 1];
        banners.push({
          marker,
          announced: Boolean(innermost.closest('[role="status"],[aria-live]')),
        });
      }
    }

    return {
      phase: label,
      cards: document.querySelectorAll("[data-station]").length,
      h1: compactText(h1 ? h1.textContent : ""),
      placeLabel,
      badgeText,
      liveCount: badgeMatch ? Number(badgeMatch[1]) : mixedBadgeMatch ? Number(mixedBadgeMatch[1]) : overviewBadgeMatch ? 0 : null,
      leadCount: badgeMatch ? Number(badgeMatch[2]) : overviewBadgeMatch ? Number(overviewBadgeMatch[1]) : null,
      firstCardName: compactText(firstCard && firstCard.querySelector("h3") ? firstCard.querySelector("h3").textContent : ""),
      firstCardNotLive: /Not live/.test(firstCardText),
      loadingNoticePresent: Boolean(section && (section.textContent || "").includes("live status is loading")),
      statusRowLiveRegion: Boolean(statusRow && statusRow.closest('[role="status"],[aria-live]')),
      banners,
      overflowX: document.documentElement.scrollWidth > window.innerWidth,
    };
  }, phase);
}

function assertPhaseA11y(state, failures, { allowPlainNotLiveBadge = false } = {}) {
  if (state.cards <= 0) failures.push(`${state.phase}: no station cards rendered`);
  if (state.overflowX) failures.push(`${state.phase}: page has horizontal overflow`);
  // The guarded degraded live view (upstream failure, server data, badge "Not live")
  // is honestly NOT-live: the plain badge is accepted there and nowhere else.
  if (
    !allowPlainNotLiveBadge &&
    !/(\d+)\s*live\s*·\s*(\d+)\s*leads/.test(state.badgeText) &&
    !/(\d+)\s*live\s*·\s*cached fallback/i.test(state.badgeText) &&
    !/(\d+)\s*leads\s*·\s*not live/i.test(state.badgeText)
  ) {
    failures.push(`${state.phase}: trust/count badge missing (got "${state.badgeText}")`);
  } else if (
    state.badgeText === "Not live" &&
    !allowPlainNotLiveBadge &&
    !/leads\s*·\s*not live/i.test(state.badgeText)
  ) {
    failures.push(`${state.phase}: trust/count badge missing (got "${state.badgeText}")`);
  }
  for (const banner of state.banners) {
    if (!banner.announced) {
      failures.push(`${state.phase}: provenance banner "${banner.marker}" lacks role=status/aria-live — state changes are not announced`);
    }
  }
  // Old shell: #sourceCard role=status + aria-live=polite + aria-atomic, #trustStrip aria-live=polite.
  if (!state.statusRowLiveRegion) {
    failures.push(`${state.phase}: persistent live-status row ("${state.badgeText}") has no role=status/aria-live — refresh/count updates are silent for screen readers (old shell trust strip was aria-live=polite)`);
  }
}

async function waitExpanded(btn, want, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const value = await btn.getAttribute("aria-expanded");
    if (value === String(want)) return true;
    await sleep(120);
  }
  return false;
}

async function readCardMeta(card) {
  return card.evaluate((art) => {
    const compactText = (v) => String(v || "").replace(/\s+/g, " ").trim();
    const btn = [...art.querySelectorAll("button")].find(
      (b) => /details/i.test(b.textContent || "") || /details/i.test(b.getAttribute("aria-label") || ""),
    );
    let accName = "";
    if (btn) {
      const labelledby = (btn.getAttribute("aria-labelledby") || "")
        .split(/\s+/)
        .filter(Boolean)
        .map((id) => (document.getElementById(id) || {}).textContent || "")
        .join(" ");
      accName = compactText(btn.getAttribute("aria-label") || labelledby || btn.textContent);
    }
    return {
      stationName: compactText((art.querySelector("h3") || {}).textContent),
      accName,
      ariaExpanded: btn ? btn.getAttribute("aria-expanded") : null,
    };
  });
}

async function readPanel(card) {
  return card.evaluate((art) => {
    const compactText = (v) => String(v || "").replace(/\s+/g, " ").trim();
    const panel = art.querySelector(".space-y-3.border-t");
    if (!panel) return null;
    return {
      visible: panel.getBoundingClientRect().height > 0,
      text: compactText(panel.textContent),
      links: [...panel.querySelectorAll("a[href]")].map((a) => ({
        text: compactText(a.textContent),
        href: a.href,
      })),
    };
  });
}

/**
 * Source-detail disclosure contract on the first visible card.
 * fullA11y=true additionally checks the station-specific trigger name and Escape (run once).
 */
async function disclosureChecks(page, phase, failures, fullA11y) {
  const result = { phase, ran: false };
  const card = page.locator("[data-station] article.card").first();
  const btn = card.locator("button", { hasText: "Details" }).first();
  if ((await btn.count()) === 0) {
    failures.push(`${phase}: no Details disclosure button on the first card`);
    return result;
  }
  result.ran = true;

  const meta = await readCardMeta(card);
  result.stationName = meta.stationName;
  result.triggerAccessibleName = meta.accName;
  if (!meta.stationName) failures.push(`${phase}: missing first station name`);
  if (meta.ariaExpanded !== "false") {
    failures.push(`${phase}: Details trigger aria-expanded should start "false" (got ${JSON.stringify(meta.ariaExpanded)})`);
  }

  if (fullA11y) {
    const selection = card.locator('[role="button"][tabindex="0"]').first();
    await page.keyboard.press("Tab");
    await selection.focus();
    result.selectionFocus = await selection.evaluate((el) => {
      const style = getComputedStyle(el);
      return {
        visible: el.matches(":focus-visible"),
        style: style.outlineStyle,
        width: parseFloat(style.outlineWidth),
      };
    });
    if (!result.selectionFocus.visible || result.selectionFocus.style === "none" || result.selectionFocus.width < 2) {
      failures.push(`${phase}: keyboard selection has no visible focus outline`);
    }
    // Old shell protected a station-specific summary aria-label ("<station> … sources").
    const token = (meta.stationName.split(/\s+/)[0] || "").toLowerCase();
    if (!token || !meta.accName.toLowerCase().includes(token)) {
      failures.push(
        `${phase}: disclosure trigger is not station-specific — accessible name is "${meta.accName}" for station "${meta.stationName}" (every card exposes an identical "Details" button; old shell had a per-station summary aria-label — add aria-label/aria-labelledby naming the station, ideally aria-controls to the panel)`,
      );
    }
  }

  // Keyboard-only: Enter expands.
  await btn.focus();
  await page.keyboard.press("Enter");
  if (!(await waitExpanded(btn, true, 2500))) {
    failures.push(`${phase}: Enter on the focused Details trigger did not set aria-expanded=true`);
  }
  if (!(await btn.evaluate((el) => document.activeElement === el))) {
    failures.push(`${phase}: focus left the Details trigger after keyboard expand`);
  }

  await sleep(420); // let the expand animation finish before measuring the panel
  const panel = await readPanel(card);
  result.panel = panel;
  if (!panel) {
    failures.push(`${phase}: expanded state has no provenance panel in the card`);
  } else {
    if (!panel.visible) failures.push(`${phase}: provenance panel rendered but is not visible`);
    if (!/Why\s+[“"].+?[”"]\s*:/.test(panel.text)) {
      failures.push(`${phase}: provenance panel is missing the decision "Why …:" line`);
    }
    if (!/(Charging claim|Parking claim|Corroboration: \d+ independent sources|Public sources \(\d+\))/.test(panel.text)) {
      failures.push(`${phase}: provenance panel does not explain source evidence (no claim/corroboration/Public sources section)`);
    }
    if (!panel.links.length) {
      failures.push(`${phase}: provenance panel exposes no source links`);
    }
    panel.links.forEach((link, index) => {
      if (!/^https?:\/\//i.test(link.href)) failures.push(`${phase}: source link ${index + 1} has non-http href (${link.href})`);
      if (!link.text) failures.push(`${phase}: source link ${index + 1} has no visible text`);
    });
  }

  // Keyboard-only: Space collapses; focus must stay on the trigger.
  await page.keyboard.press("Space");
  if (!(await waitExpanded(btn, false, 2500))) {
    failures.push(`${phase}: Space on the focused Details trigger did not collapse (aria-expanded stuck true)`);
  }
  if (!(await btn.evaluate((el) => document.activeElement === el))) {
    failures.push(`${phase}: focus left the Details trigger after keyboard collapse`);
  }
  await sleep(350);
  const collapsedPanel = await readPanel(card);
  if (collapsedPanel && collapsedPanel.visible) {
    failures.push(`${phase}: provenance panel still visible after collapse`);
  }

  if (fullA11y) {
    // Escape must dismiss the open disclosure.
    await page.keyboard.press("Enter");
    await waitExpanded(btn, true, 2500);
    await page.keyboard.press("Escape");
    if (!(await waitExpanded(btn, false, 1500))) {
      failures.push(`${phase}: Escape does not collapse the open Details disclosure (no Escape handling on the trigger/panel)`);
    }
  }

  // Pointer path: clicking the trigger of an open disclosure collapses it (second click).
  if ((await btn.getAttribute("aria-expanded")) !== "true") {
    await btn.click();
    await waitExpanded(btn, true, 2500);
  }
  await btn.click();
  if (!(await waitExpanded(btn, false, 2500))) {
    failures.push(`${phase}: second click on the Details trigger did not collapse the disclosure`);
  }
  if (!(await btn.evaluate((el) => document.activeElement === el))) {
    failures.push(`${phase}: focus left the Details trigger after pointer toggle`);
  }
  return result;
}

async function main() {
  const logs = [];
  const mapBackdropNoise = [];
  const failures = [];
  const browser = await chromium.launch({ headless: true, executablePath: chromePath });
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    isMobile: true,
    geolocation: { latitude: 39.47, longitude: -0.377 }, // València — has live coverage
    permissions: ["geolocation"],
  });
  const page = await context.newPage();
  page.on("console", (msg) => {
    const line = `${msg.type()}: ${msg.text()}`;
    if (msg.type() === "error" && isMapBackdropNoise(line)) mapBackdropNoise.push(line);
    else logs.push(line);
  });
  page.on("pageerror", (err) => {
    // pageerror messages alone are chunk-less; attribute via the Error stack.
    if (isMapBackdropNoise(`${err.message} ${err.stack || ""}`)) {
      mapBackdropNoise.push(`pageerror: ${err.message}`);
    } else {
      logs.push(`pageerror: ${err.message}`);
    }
  });

  await page.goto(`${baseUrl}/`, { waitUntil: "domcontentloaded", timeout: 20000 });
  await page.waitForSelector("[data-station] article.card", { timeout: 15000 });

  // Phase 1 — national overview: honestly labeled source-backed (the old "preview" state).
  const overviewState = await readPhase(page, "overview");
  assertPhaseA11y(overviewState, failures);
  if (overviewState.placeLabel !== "Explore Spain") {
    failures.push(`overview: mode label should be "Explore Spain" (got "${overviewState.placeLabel}")`);
  }
  if (!/public chargers/.test(overviewState.h1)) {
    failures.push(`overview: heading should read "<N> public chargers" (got "${overviewState.h1}")`);
  }
  if ((overviewState.leadCount || 0) < 1) {
    failures.push(`overview: expected source-backed leads in the badge (got "${overviewState.badgeText}")`);
  }
  if (!overviewState.firstCardNotLive) {
    failures.push(`overview: first card is not labeled "Not live" — the source-backed (non-live) state must be explicit`);
  }

  // Phase 2 — disclosure a11y on a source-backed lead card (full checks incl. name + Escape).
  const overviewDisclosure = await disclosureChecks(page, "overview-details", failures, true);

  // Phase 3 — transition to live: geolocate and wait for live data to replace the
  // source-backed state (old: preview banner replaced + live cards appear).
  await page.locator('button[aria-label="Use my location"]').click();
  const transition = { reachedLive: false, degradedLive: false, waitedMs: 0, noticeSeen: false, noticeAnnounced: null, lastSample: null };
  const deadline = Date.now() + liveBudgetMs;
  const startedAt = Date.now();
  while (Date.now() < deadline) {
    const sample = await page.evaluate(() => {
      const compactText = (v) => String(v || "").replace(/\s+/g, " ").trim();
      const section = document.querySelector("section");
      const h1 = compactText(section && section.querySelector("h1") ? section.querySelector("h1").textContent : "");
      const dot = section ? section.querySelector(".live-dot") : null;
      const badge = dot && dot.parentElement ? compactText((dot.parentElement.querySelector(".badge") || {}).textContent) : "";
      const match = badge.match(/(\d+)\s*live\s*·\s*(\d+)\s*leads/);
      const mixedMatch = badge.match(/(\d+)\s*live\s*·\s*cached fallback/i);
      const cards = [...document.querySelectorAll("[data-station] article.card")];
      const hasLiveCard = cards.some((card) => !/\bNot live\b/i.test(card.textContent || ""));
      const mixedMatchEarly = badge.match(/(\d+)\s*live\s*·\s*cached fallback/i);
      const liveCountEarly = mixedMatchEarly ? Number(mixedMatchEarly[1]) : null;
      // The guarded stale view: after a live upstream failure the response still returned
      // server data, and the app must NOT claim a live count. That honest degraded view
      // appears as either badge "Not live" (stale branch) or badge "0 live · cached
      // fallback" (mixed branch, zero route-ready rows) — both while live-source cards
      // render and neither claiming a positive live count.
      const noAuthority = badge === "Not live" || (badge.endsWith("cached fallback") && liveCountEarly === 0);
      const noticePresent = Boolean(section && (section.textContent || "").includes("live status is loading"));
      let noticeAnnounced = null;
      if (noticePresent && section) {
        const matches = [...section.querySelectorAll("div")].filter((el) => (el.textContent || "").includes("live status is loading"));
        const innermost = matches[matches.length - 1];
        noticeAnnounced = Boolean(innermost && innermost.closest('[role="status"],[aria-live]'));
      }
      return {
        h1,
        badge,
        liveCount: match ? Number(match[1]) : mixedMatch ? Number(mixedMatch[1]) : null,
        hasLiveCard,
        noAuthority,
        noticePresent,
        noticeAnnounced,
      };
    });
    transition.lastSample = sample;
    if (sample.noticePresent) {
      transition.noticeSeen = true;
      transition.noticeAnnounced = sample.noticeAnnounced;
    }
    // Live authority: "N live · M leads" or "N live · cached fallback" (mixed degraded mode).
    if (sample.liveCount != null && sample.liveCount > 0) {
      transition.reachedLive = true;
      break;
    }
    // Honest degraded transition: the live fetch completed with a fresh no-authority
    // response (badge "Not live") while live-source rows still render. The protected
    // behavior is that NOTHING is claimed live — this matches the header's behavior 6
    // ("a live source card renders even when zero stations are route-ready").
    if (sample.noAuthority && sample.hasLiveCard) {
      transition.degradedLive = true;
      break;
    }
    await sleep(300);
  }
  transition.waitedMs = Date.now() - startedAt;
  if (!transition.reachedLive && !transition.degradedLive) {
    failures.push(
      `transition: did not reach the live view with a live-source card within ${liveBudgetMs}ms (last: h1="${transition.lastSample ? transition.lastSample.h1 : ""}", badge="${transition.lastSample ? transition.lastSample.badge : ""}")`,
    );
  }
  if (transition.noticeSeen && transition.noticeAnnounced === false) {
    failures.push(`transition: "live status is loading" notice shown without role=status/aria-live`);
  }

  // Let the fly-to animation and viewport-driven list settle, until a live card leads the list.
  // The server's bounded live crawl can take tens of seconds on a cold cache, so the settle
  // window shares the transition budget instead of a fixed 10s slice of it.
  const settleDeadline = Date.now() + liveBudgetMs;
  let liveState = await readPhase(page, "live");
  while (Date.now() < settleDeadline && (liveState.firstCardNotLive || liveState.cards === 0)) {
    await sleep(400);
    liveState = await readPhase(page, "live");
  }
  assertPhaseA11y(liveState, failures, { allowPlainNotLiveBadge: transition.degradedLive });
  if (transition.reachedLive) {
    if (!/^\d+ (?:in view|nearby)$/.test(liveState.h1)) {
      failures.push(`live: heading should read "<N> in view/nearby" (got "${liveState.h1}")`);
    }
    if (liveState.placeLabel !== "Near you") {
      failures.push(`live: mode label still "${liveState.placeLabel}" — the source-backed overview state was not replaced by a live state`);
    }
    if (liveState.firstCardNotLive) {
      failures.push(`live: no live station card rendered at the top of the list (first card "${liveState.firstCardName}" is still a lead)`);
    }
    if (liveState.loadingNoticePresent) {
      failures.push(`live: "live status is loading" notice still shown after live data arrived`);
    }
  }

  // Phase 4 — disclosure contract must hold on a LIVE card too (old live-open checks).
  let liveDisclosure = { ran: false, skipped: "transition failed" };
  if (transition.reachedLive || transition.degradedLive) {
    liveDisclosure = await disclosureChecks(page, "live-details", failures, false);
  } else {
    failures.push("live-details: skipped — live view never became available");
  }

  await browser.close();

  const unexpectedErrors = logs.filter((line) => /^error:|^pageerror:/.test(line));
  if (unexpectedErrors.length) failures.push(`browser console contains errors: ${unexpectedErrors.join(" | ")}`);

  console.log(
    JSON.stringify(
      {
        ok: failures.length === 0,
        baseUrl,
        overviewState,
        overviewDisclosure,
        transition,
        liveState,
        liveDisclosure,
        logs,
        mapBackdropNoise,
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
