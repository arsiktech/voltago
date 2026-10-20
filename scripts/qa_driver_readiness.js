#!/usr/bin/env node
/* Driver-readiness browser contract for Volta (React SPA).
 *
 * Ported 2026-07-03 from the retired vanilla static/ shell to the MapLibre/React SPA
 * (see docs/spa-dom-contract.md). Intent preserved: the default and Open-now flows must
 * answer "can I choose a charger safely right now?" with live/source/parking/navigation
 * evidence in the rendered UI, and Open now must stay fail-closed — leads must never be
 * presented as available, and the empty state must offer recovery that actually works.
 *
 * Flow: national overview (leads-only) -> per-card honesty + provenance details ->
 * Open now fail-closed on overview (deterministic empty state + recovery exercise) ->
 * geolocation mock (Valencia) into live mode -> Open now fail-closed in live mode.
 */
const { chromium } = require("playwright");
const { openFilters, closeFilters, setFilter } = require("./qa_browser_harness");

const baseUrl = (process.argv[2] || process.env.VOLTA_QA_BASE_URL || "http://127.0.0.1:8773").replace(/\/$/, "");
const chromePath = process.env.VOLTA_QA_CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const screenshot = process.env.VOLTA_QA_DRIVER_SCREENSHOT || "/tmp/volta-driver-readiness-default.png";
const openNowScreenshot = process.env.VOLTA_QA_DRIVER_OPEN_NOW_SCREENSHOT || "/tmp/volta-driver-readiness-open-now.png";

const RAW_ERROR_RE = /\b(?:HTTPError|HTTP Error|Traceback|TypeError|Exception|API \d{3}|HTTP \d{3})\b/i;

/* The MapLibre canvas is a backdrop enhancement (docs/spa-dom-contract.md): headless
 * Chrome's software WebGL makes maplibre-gl throw projection/frame errors that a real
 * GPU browser doesn't hit. Errors attributable to the maplibre chunk are logged but not
 * fatal; any other console error / pageerror still fails the contract. */
const MAP_BACKDROP_NOISE = [
  /\/assets\/maplibre-[^\s'")]+/i,
  /Invalid LngLat object: \(NaN, NaN\)/i,
  /Attempting to run\(\), but is already running/i,
  /fromInvProjectionMatrix|getCameraFrustum/i,
];
const isMapBackdropNoise = (line) => MAP_BACKDROP_NOISE.some((re) => re.test(line));

function isSourceUrl(url) {
  if (!/^https?:\/\//i.test(url || "")) return false;
  return !/google\.[^/]+\/maps|maps\.apple\.com/i.test(url);
}

/* Mirror of frontend/src/lib/format.ts sourceTier()/passesFilters() so the rendered
 * card ids can be cross-checked against the dataset the page fetched. */
function reliabilityTier(item) {
  if (!item.lead) return "live";
  const r = (item.reliability || "").toLowerCase();
  if (r.startsWith("official")) return "official";
  if (/directory|map|locator/.test(r)) return "directory";
  return "community";
}
function verifiedPass(item) {
  const tier = reliabilityTier(item);
  return tier !== "community" || (item.verification && item.verification.route_checked === true);
}
function parkingRisk(item) {
  const p = item.parking || {};
  return p.paid === true || ["paid_or_metered", "time_limited_or_validated"].includes(p.kind || "");
}

/** Snapshot the panel: chips, status row, coverage banners, rendered cards, empty state. */
function collect(page) {
  return page.evaluate(() => {
    const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
    const section = document.querySelector("section");
    const chips = [...document.querySelectorAll('div[role="group"][aria-label="Filters"] dialog button.chip')].map((b) => ({
      label: norm(document.getElementById(b.getAttribute("aria-labelledby"))?.textContent),
      pressed: b.getAttribute("aria-pressed") === "true",
    }));
    const liveDot = section ? section.querySelector(".live-dot") : null;
    const statusText = norm(liveDot && liveDot.parentElement ? liveDot.parentElement.textContent : "");
    const badgeText =
      [...document.querySelectorAll("span.badge")]
        .map((el) => norm(el.textContent))
        .find((t) => /^(?:\d+ live · \d+ leads|\d+ leads · not live|Not live)$/.test(t)) || "";
    const h1 = norm(section && section.querySelector("h1") ? section.querySelector("h1").textContent : "");
    const h1El = section ? section.querySelector("h1") : null;
    const modeLabel = norm(h1El && h1El.parentElement && h1El.parentElement.firstElementChild ? h1El.parentElement.firstElementChild.textContent : "");
    const banners = [...(section ? section.querySelectorAll('div[role="status"]') : [])].map((el) => {
      const p = el.querySelector("p");
      const style = p ? window.getComputedStyle(p) : null;
      return {
        text: norm(el.textContent),
        isCoverage: /Limited live coverage|Live results may be incomplete|Showing cached data/.test(el.textContent || ""),
        isInstant: /live status is loading/i.test(el.textContent || ""),
        whiteSpace: style ? style.whiteSpace : "",
        clipped: p ? p.scrollHeight - p.clientHeight > 2 : false,
      };
    });
    const cards = [...document.querySelectorAll("[data-station]")]
      .map((w) => {
        const holder = w.parentElement;
        const idx = holder && holder.dataset ? Number(holder.dataset.index != null ? holder.dataset.index : -1) : -1;
        const art = w.querySelector("article");
        const anchors = [...(art ? art.querySelectorAll("a") : [])];
        const maps = anchors.find((a) => /open in maps/i.test(a.textContent || ""));
        const start = anchors.find((a) => /^Start\/check/.test(norm(a.textContent)));
        const badges = [...(art ? art.querySelectorAll(".badge") : [])].map((el) => norm(el.textContent));
        const pill = art ? art.querySelector(".pill") : null;
        const details = art ? art.querySelector("button[aria-expanded]") : null;
        const text = norm(art ? art.textContent : "");
        // Read each semantic availability fact independently: card textContent joins
        // "Not live" directly to distance on rows with no port/power facts.
        const availabilityRows = art ? [...art.querySelectorAll("[data-availability]")] : [];
        const availability = availabilityRows[0];
        const availabilityFacts = availability ? [...availability.querySelectorAll("span")].map((el) => norm(el.textContent)) : [];
        const availabilityVisible = Boolean(availability?.getClientRects().length && getComputedStyle(availability).visibility !== "hidden");
        const freeMatch = availabilityFacts.map((fact) => /^(\d+) free$/.exec(fact)).find(Boolean);
        return {
          id: w.getAttribute("data-station"),
          index: idx,
          title: norm(art && art.querySelector("h3") ? art.querySelector("h3").textContent : ""),
          hasPill: Boolean(pill),
          pillText: norm(pill ? pill.textContent : ""),
          pillGo: Boolean(pill && pill.classList.contains("pill-go")),
          badges,
          availabilityKind: availability?.getAttribute("data-availability") || "",
          availabilityRows: availabilityRows.length,
          availabilityVisible,
          notLive: availabilityVisible && availability?.getAttribute("data-availability") === "not-live" && availabilityFacts.includes("Not live"),
          tierBadge: art?.querySelector("[data-source-tier]")?.getAttribute("data-source-tier") || "",
          parkingRiskBadge: /Paid parking|Limited parking/i.test(art?.querySelector("[data-card-caution]")?.getAttribute("aria-label") || ""),
          freeCount: freeMatch ? Number(freeMatch[1]) : null,
          liveSignal: availabilityVisible && availability?.getAttribute("data-availability") === "live" && availabilityFacts.some((fact) => /^(?:\d+ free|\d+ in use|\d+ out|Live · status unknown)$/.test(fact)),
          mapsHref: maps ? maps.href : "",
          mapsRel: maps ? maps.rel || "" : "",
          mapsTarget: maps ? maps.target || "" : "",
          startHref: start ? start.href : "",
          startRel: start ? start.rel || "" : "",
          startTarget: start ? start.target || "" : "",
          detailsExpanded: details ? details.getAttribute("aria-expanded") === "true" : false,
          cardText: text,
        };
      })
      .sort((a, b) => a.index - b.index);
    const cardTexts = [...document.querySelectorAll(".card")].map((el) => norm(el.textContent));
    const emptyText = cardTexts.find((t) => /No chargers match these filters|No charger nearby yet/.test(t)) || "";
    const errorCard = cardTexts.find((t) => /Couldn.t reach the live source/i.test(t)) || "";
    const recoveryButtons = [...document.querySelectorAll("button")]
      .map((b) => norm(b.textContent))
      .filter((t) => /^(Show all leads|Broaden price|Clear search)$/.test(t));
    return {
      chips,
      noPriceLimit: [...document.querySelectorAll("dialog button[aria-pressed]")].some(button => norm(button.textContent) === "No limit" && button.getAttribute("aria-pressed") === "true"),
      statusText,
      badgeText,
      h1,
      modeLabel,
      coverage: banners.find((b) => b.isCoverage) || null,
      instantNotice: banners.some((b) => b.isInstant),
      cards,
      emptyText,
      errorCard,
      recoveryButtons,
      overflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    };
  });
}

function chipPressed(state, label) {
  const chip = state.chips.find((c) => c.label === label);
  return chip ? chip.pressed : null;
}

/** Route-ready live cards must stay ahead of every backup; lower tiers are distance-ranked. */
function routeReadyPrefixViolation(cards) {
  let seenBackup = false;
  for (const c of cards) {
    const routeReady = !c.notLive && c.pillGo && c.freeCount != null && c.freeCount > 0;
    if (!routeReady) seenBackup = true;
    else if (seenBackup) return c.title || c.id;
  }
  return null;
}

function names(list, n = 3) {
  return list.map((c) => c.title || c.id).slice(0, n).join(", ");
}

/** Messaging honesty shared checks: raw tech text never reaches the driver. */
function messagingChecks(state, phase, failures) {
  const surfaces = [state.statusText, state.coverage ? state.coverage.text : "", state.emptyText, state.errorCard];
  for (const text of surfaces) {
    if (text && RAW_ERROR_RE.test(text)) failures.push(`${phase}: UI messaging exposes raw technical failure text: ${text}`);
  }
  if (state.errorCard) failures.push(`${phase}: error card is showing instead of results: ${state.errorCard}`);
  if (state.coverage) {
    if (state.coverage.whiteSpace === "nowrap") failures.push(`${phase}: coverage banner message is nowrap and can hide warnings`);
    if (state.coverage.clipped) failures.push(`${phase}: coverage banner message is clipped and not fully readable`);
  }
}

/** Per-card evidence a driver needs before rolling: pill + tier + honest links. */
function cardEvidenceChecks(state, phase, failures) {
  const malformedAvailability = state.cards.filter((c) => c.availabilityRows !== 1 || !c.availabilityVisible || !["live", "not-live"].includes(c.availabilityKind) || (c.availabilityKind === "not-live" ? !c.notLive : !c.liveSignal));
  if (malformedAvailability.length) failures.push(`${phase}: cards lack one visible, honestly labeled availability row: ${names(malformedAvailability)}`);
  const noPill = state.cards.filter((c) => !c.hasPill);
  const noTier = state.cards.filter((c) => !c.tierBadge);
  const badMaps = state.cards.filter((c) => !/^https?:\/\//i.test(c.mapsHref));
  const badStart = state.cards.filter((c) => c.startHref && !isSourceUrl(c.startHref));
  const badRel = state.cards.filter(
    (c) =>
      (c.mapsHref && (!c.mapsRel.includes("noopener") || c.mapsTarget !== "_blank")) ||
      (c.startHref && (!c.startRel.includes("noopener") || c.startTarget !== "_blank")),
  );
  if (noPill.length) failures.push(`${phase}: cards missing a decision pill: ${names(noPill)}`);
  if (noTier.length) failures.push(`${phase}: cards missing a source-tier badge: ${names(noTier)}`);
  if (badMaps.length) failures.push(`${phase}: cards missing an Open in Maps link: ${names(badMaps)}`);
  if (badStart.length) failures.push(`${phase}: card start/check URLs must be non-map provider pages: ${names(badStart)}`);
  if (badRel.length) failures.push(`${phase}: external card links lack target=_blank rel=noopener: ${names(badRel)}`);
}

async function main() {
  const logs = [];
  const responses = [];
  const failures = [];
  const expect = (cond, msg) => {
    if (!cond) failures.push(msg);
  };

  // Dataset the page will render (overview intel DB is stable within a run) — used to
  // cross-check rendered card ids and visible source/parking caveats in the broad default.
  let intelIndex = null;
  let intelError = "";
  try {
    const res = await fetch(`${baseUrl}/api/chargers?include_leads=true&max_price=5.0&overview=true`, {
      headers: { Accept: "application/json" },
    });
    if (!res.ok) throw new Error(`overview payload fetch -> ${res.status}`);
    const payload = await res.json();
    intelIndex = new Map();
    for (const item of [...(payload.stations || []), ...(payload.leads || [])]) intelIndex.set(item.id, item);
  } catch (err) {
    intelError = String((err && err.message) || err);
  }

  const browser = await chromium.launch({ headless: true, executablePath: chromePath });
  const page = await browser.newPage({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    isMobile: true,
  });
  page.setDefaultTimeout(15000);
  page.on("console", (msg) => logs.push(`${msg.type()}: ${msg.text()}`));
  // Keep the first stack frames so errors can be attributed (app code vs maplibre backdrop).
  page.on("pageerror", (err) =>
    logs.push(`pageerror: ${String(err.stack || err.message).split("\n").slice(0, 4).join(" | ")}`),
  );
  page.on("response", (response) => {
    const url = response.url();
    if (/\/api\/(chargers|geocode|intel)/.test(url)) responses.push({ url, status: response.status() });
  });

  // Geolocation mock (Valencia) prepared up front; only consumed when "Use my location" is clicked.
  const context = page.context();
  await context.grantPermissions(["geolocation"], { origin: baseUrl });
  await context.setGeolocation({ latitude: 39.47, longitude: -0.377 });

  /* ---------------- Phase 1: default national overview ---------------- */
  await page.goto(`${baseUrl}/`, { waitUntil: "domcontentloaded", timeout: 20000 });
  await page.waitForSelector("[data-station] article.card", { timeout: 20000 });
  await page.waitForFunction(
    () => {
      const section = document.querySelector("section");
      const dot = section && section.querySelector(".live-dot");
      const status = dot && dot.parentElement ? dot.parentElement.textContent || "" : "";
      return /Saved chargers · not live/.test(status) && !/Refreshing/.test(status);
    },
    null,
    { timeout: 30000 },
  );
  // Evidence screenshots should show the settled map-backed product state, not the
  // brief panel-only first paint while MapLibre is still constructing its canvas.
  await page.waitForSelector(".maplibregl-canvas", { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(800);
  await page.screenshot({ path: screenshot, fullPage: true });
  await openFilters(page);
  const defaultState = await collect(page);
  await closeFilters(page);

  expect(defaultState.cards.length > 0, "default: view rendered no cards");
  expect(/^\d+ public chargers$/.test(defaultState.h1), `default: header is not the broad overview count: "${defaultState.h1}"`);
  expect(defaultState.modeLabel === "Explore Spain", `default: mode label is not "Explore Spain": "${defaultState.modeLabel}"`);
  expect(/^\d+ leads · not live$/.test(defaultState.badgeText), "default: source-backed/not-live count badge is missing");
  for (const [label, want] of [
    ["Open now", false],
    ["Source checked", false],
    ["Easy parking", false],
    ["Tesla OK", false],
    ["Fast", false],
    ["Leads (Verify first)", true],
  ]) {
    expect(chipPressed(defaultState, label) === want, `default: filter chip "${label}" should be ${want ? "on" : "off"} by default`);
  }
  expect(defaultState.noPriceLimit, "default: charging price should be No limit");
  messagingChecks(defaultState, "default", failures);
  cardEvidenceChecks(defaultState, "default", failures);

  // Overview is leads-only by design: every card must be honestly non-live and non-"go".
  const fakeLive = defaultState.cards.filter((c) => !c.notLive || c.freeCount != null || c.pillGo);
  expect(
    fakeLive.length === 0,
    `default: overview leads presented with live/route-ready signals (Not live availability fact missing, "N free" or Go now shown): ${names(fakeLive)}`,
  );
  const misorder = routeReadyPrefixViolation(defaultState.cards);
  expect(!misorder, `default: a live card is ranked below leads: ${misorder}`);
  expect(!defaultState.overflowX, "default: mobile viewport has horizontal overflow");

  // Dataset-level cross-check of the broad default's source and parking disclosures.
  const idCheck = { checked: 0, missing: [], badSource: [], unknownParkingNoCaveat: [] };
  if (intelIndex) {
    for (const card of defaultState.cards) {
      const item = intelIndex.get(card.id);
      if (!item) {
        idCheck.missing.push(card.id);
        continue;
      }
      idCheck.checked += 1;
      const src = (item.primary_source && item.primary_source.url) || item.source_url || "";
      if (!isSourceUrl(src)) idCheck.badSource.push(card.title || card.id);
      if ((item.parking && item.parking.kind) === "unknown" && !/Parking unknown/i.test(card.cardText)) {
        idCheck.unknownParkingNoCaveat.push(card.title || card.id);
      }
    }
    expect(idCheck.missing.length === 0, `default: rendered card ids missing from the overview payload: ${idCheck.missing.slice(0, 3).join(", ")}`);
    expect(idCheck.badSource.length === 0, `default: dataset rows behind rendered cards lack real source URLs: ${idCheck.badSource.slice(0, 3).join(", ")}`);
    // SPA change vs old shell: parking.kind === "unknown" is no longer excluded by Easy
    // parking — the honest substitute is a visible "Parking unknown" caveat on the card.
    expect(
      idCheck.unknownParkingNoCaveat.length === 0,
      `default: unknown-parking cards lack the visible "Parking unknown" caveat: ${idCheck.unknownParkingNoCaveat.slice(0, 3).join(", ")}`,
    );
  } else {
    failures.push(`default: could not fetch the overview payload for the id cross-check: ${intelError}`);
  }

  /* ---------------- Phase 2: provenance details on the first card ---------------- */
  // The Details toggle carries aria-label="Source details for <name>" (accessible name !=
  // visible text), so target the aria-expanded affordance and assert its labeling instead.
  const detailsBtn = page.locator("[data-station]").first().locator("button[aria-expanded]").first();
  const detailsBtnInfo = await detailsBtn
    .evaluate((b) => ({ text: (b.textContent || "").replace(/\s+/g, " ").trim(), ariaLabel: b.getAttribute("aria-label") || "" }))
    .catch(() => null);
  expect(Boolean(detailsBtnInfo), "details: first card has no expandable Details button (button[aria-expanded])");
  if (detailsBtnInfo) {
    expect(/Details/i.test(detailsBtnInfo.text), `details: expand affordance is not labeled "Details" for sighted users: "${detailsBtnInfo.text}"`);
    expect(
      !detailsBtnInfo.ariaLabel || /details/i.test(detailsBtnInfo.ariaLabel),
      `details: Details button aria-label does not describe details: "${detailsBtnInfo.ariaLabel}"`,
    );
  }
  await detailsBtn.click({ timeout: 8000 });
  await page
    .waitForFunction(
      () => {
        const art = document.querySelector("[data-station] article");
        const btn = art && art.querySelector("button[aria-expanded]");
        return btn && btn.getAttribute("aria-expanded") === "true" && /Why [“"]|Charging claim|Public sources/.test(art.textContent || "");
      },
      null,
      { timeout: 8000 },
    )
    .catch(() => {});
  const detailState = await page.evaluate(() => {
    const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
    const art = document.querySelector("[data-station] article");
    const text = norm(art ? art.textContent : "");
    const anchors = [...(art ? art.querySelectorAll("a") : [])];
    const evidenceLinks = anchors.filter((a) => {
      const t = norm(a.textContent);
      if (/view source/i.test(t)) return true;
      return Boolean(a.closest("ul")) && /^https?:/i.test(a.href || "");
    });
    const btn = art && art.querySelector("button[aria-expanded]");
    const sourcesMatch = text.match(/Public sources \((\d+)\)/);
    return {
      open: Boolean(btn && btn.getAttribute("aria-expanded") === "true"),
      hasWhy: /Why [“"]/.test(text),
      hasClaim: /Charging claim|Parking claim/.test(text),
      publicSources: sourcesMatch ? Number(sourcesMatch[1]) : null,
      evidenceLinkCount: evidenceLinks.length,
      textSample: text.slice(0, 240),
    };
  });
  expect(detailState.open, "details: first card Details did not expand");
  expect(detailState.hasWhy, "details: expanded details lack the decision reason (Why …)");
  expect(detailState.hasClaim || detailState.publicSources != null, "details: expanded details lack claim/public-source evidence sections");
  expect(detailState.evidenceLinkCount > 0, "details: expanded details expose no external evidence links");
  expect(/source|live|claim|corroborat/i.test(detailState.textSample), "details: detail text lacks useful evidence wording");

  /* ---------------- Phase 3: Open now on the overview must fail closed ---------------- */
  const overviewAvailResp = page.waitForResponse(
    (r) => r.url().includes("/api/chargers") && r.url().includes("available=true") && r.url().includes("overview=true"),
    { timeout: 25000 },
  );
  await setFilter(page, "Open now", true);
  const ovAvail = await overviewAvailResp;
  expect(ovAvail.status() === 200, `overview-open-now: available=true fetch returned ${ovAvail.status()}`);
  await page.waitForFunction(
    () => {
      const chip = [...document.querySelectorAll('div[role="group"][aria-label="Filters"] dialog button.chip')].find(
        (b) => (document.getElementById(b.getAttribute("aria-labelledby"))?.textContent || "").trim() === "Open now",
      );
      const pressed = chip && chip.getAttribute("aria-pressed") === "true";
      const section = document.querySelector("section");
      const dot = section && section.querySelector(".live-dot");
      const status = dot && dot.parentElement ? dot.parentElement.textContent || "" : "";
      const hasCards = document.querySelectorAll("[data-station]").length > 0;
      const hasEmpty = [...document.querySelectorAll(".card")].some((el) =>
        /No chargers match these filters|No charger nearby yet/.test(el.textContent || ""),
      );
      return pressed && !/Refreshing/.test(status) && (hasCards || hasEmpty);
    },
    null,
    { timeout: 30000 },
  );
  const overviewOpenNow = await collect(page);
  expect(chipPressed(overviewOpenNow, "Open now") === true, "overview-open-now: chip did not activate");
  const ovLeaky = overviewOpenNow.cards.filter((c) => c.notLive);
  expect(ovLeaky.length === 0, `overview-open-now: leads rendered while Open now is on (fail-open): ${names(ovLeaky)}`);
  const ovNotReady = overviewOpenNow.cards.filter((c) => !c.pillGo || !(c.freeCount != null && c.freeCount > 0));
  expect(
    overviewOpenNow.cards.length === 0 || ovNotReady.length === 0,
    `overview-open-now: non-route-ready cards shown under Open now: ${names(ovNotReady)}`,
  );
  let overviewRecovery = null;
  if (!overviewOpenNow.cards.length) {
    expect(Boolean(overviewOpenNow.emptyText), "overview-open-now: empty state card with guidance is missing");
    expect(overviewOpenNow.recoveryButtons.includes("Show all leads"), "overview-open-now: empty state lacks a Show all leads recovery button");
    expect(overviewOpenNow.recoveryButtons.includes("Broaden price"), "overview-open-now: empty state lacks a Broaden price recovery button");
    messagingChecks(overviewOpenNow, "overview-open-now", failures);

    // The recovery button must actually recover — the old shell's "Show trusted leads"
    // cleared the availability filter and restored trusted leads.
    await page.getByRole("button", { name: "Show all leads", exact: true }).click({ timeout: 8000 });
    await page
      .waitForFunction(() => document.querySelectorAll("[data-station]").length > 0, null, { timeout: 6000 })
      .catch(() => {});
    overviewRecovery = await collect(page);
    if (!overviewRecovery.cards.length) {
      failures.push(
        "overview-open-now: 'Show all leads' recovery leaves the list empty — it re-enables leads but does not clear the Open now filter, which still excludes every lead",
      );
    } else if (chipPressed(overviewRecovery, "Open now") === true && overviewRecovery.cards.some((c) => c.notLive)) {
      failures.push("overview-open-now: recovery shows leads while Open now is still active (leads presented as available)");
    }
  } else {
    expect(!overviewOpenNow.overflowX, "overview-open-now: mobile viewport has horizontal overflow");
  }

  // Opt into source/parking constraints before the live follow-up. "Show all leads"
  // recovery clears Open now and constraints itself, so toggle by observed aria-pressed
  // state instead of blind clicks.
  const setChip = (label, want) => setFilter(page, label, want);
  await setChip("Open now", false);
  await setChip("Source checked", true);
  await setChip("Easy parking", true);
  await page.waitForFunction(
    () => {
      const chips = [...document.querySelectorAll('div[role="group"][aria-label="Filters"] dialog button.chip')];
      const get = (label) => chips.find((b) => (document.getElementById(b.getAttribute("aria-labelledby"))?.textContent || "").trim() === label);
      const on = (label) => get(label) && get(label).getAttribute("aria-pressed") === "true";
      const off = (label) => get(label) && get(label).getAttribute("aria-pressed") === "false";
      return off("Open now") && on("Source checked") && on("Easy parking") && on("Leads (Verify first)") && document.querySelectorAll("[data-station]").length > 0;
    },
    null,
    { timeout: 20000 },
  );

  /* ---------------- Phase 4: geolocation -> live mode ---------------- */
  const liveResp = page.waitForResponse((r) => r.url().includes("/api/chargers") && /[?&]lat=/.test(r.url()), { timeout: 45000 });
  await page.locator('button[aria-label="Use my location"]').click();
  const liveFetch = await liveResp;
  expect(liveFetch.status() === 200, `live: /api/chargers with lat/lon returned ${liveFetch.status()}`);
  const livePayload = await liveFetch.json().catch(() => null);
  // Healthy path: "Live · updated" status. Honest degraded path: the live response had no
  // fresh live authority, so the settled status must be "Cached chargers · not live" with
  // live-source cards rendered and no claimed live count — never a stuck "Refreshing…".
  const degradedNoAuthority =
    livePayload?.cache?.fresh_live_authority === false &&
    (livePayload?.cache?.stale === true || (livePayload?.stations?.length || 0) > 0);
  await page.waitForFunction(
    () => {
      const section = document.querySelector("section");
      const h1El = section && section.querySelector("h1");
      const h1 = h1El ? h1El.textContent || "" : "";
      const modeLabelWrap = h1El && h1El.parentElement ? h1El.parentElement.firstElementChild : null;
      const modeLabel = (modeLabelWrap && modeLabelWrap.textContent ? modeLabelWrap.textContent : "").replace(/\s+/g, " ").trim();
      const dot = section && section.querySelector(".live-dot");
      const status = dot && dot.parentElement ? dot.parentElement.textContent || "" : "";
      const badge = dot && dot.parentElement ? (dot.parentElement.querySelector(".badge") || {}).textContent || "" : "";
      const instant = [...(section ? section.querySelectorAll('div[role="status"]') : [])].some((el) =>
        /live status is loading/i.test(el.textContent || ""),
      );
      const reachedNearby = /\b(?:in view|nearby)\b/.test(h1) && !/Refreshing/.test(status) && !instant && modeLabel === "Near you";
      const healthy = /Live(?: source \+ cached evidence)? · updated/.test(status);
      const degraded = badge.trim() === "Not live" && document.querySelectorAll("[data-station]").length > 0;
      return reachedNearby && (healthy || degraded);
    },
    null,
    { timeout: 45000 },
  );
  const liveState = await collect(page);

  expect(/^\d+ (?:in view|nearby)$/.test(liveState.h1), `live: header did not switch to the live "N in view/nearby" mode: "${liveState.h1}"`);
  expect(liveState.modeLabel === "Near you", `live: mode label is not "Near you": "${liveState.modeLabel}"`);
  if (livePayload?.meta?.coverage?.mode === "degraded" || livePayload?.meta?.coverage?.is_degraded === true) {
    expect(
      Boolean(liveState.coverage),
      "live: API coverage is degraded but no coverage status banner explains it",
    );
  }
  if (liveState.coverage) {
    expect(
      /Low live coverage|Limited live coverage|may be incomplete|route-ready|cached|stale|live stations/i.test(liveState.coverage.text),
      `live: coverage banner lacks a human explanation: ${liveState.coverage.text}`,
    );
  }
  messagingChecks(liveState, "live", failures);
  if (liveState.cards.length) cardEvidenceChecks(liveState, "live", failures);
  const liveMisorder = routeReadyPrefixViolation(liveState.cards);
  expect(!liveMisorder, `live: a route-ready card is ranked below a backup tier: ${liveMisorder}`);
  const liveCards = liveState.cards.filter((c) => !c.notLive);
  const liveNoSignal = liveCards.filter((c) => !c.liveSignal);
  expect(liveNoSignal.length === 0, `live: live cards lack a real-time availability signal: ${names(liveNoSignal)}`);
  const liveLeadGo = liveState.cards.filter((c) => c.notLive && c.pillGo);
  expect(liveLeadGo.length === 0, `live: lead cards carry a "Go now" pill: ${names(liveLeadGo)}`);
  expect(!liveState.overflowX, "live: mobile viewport has horizontal overflow");

  /* ---------------- Phase 5: Open now in live mode must be fail-closed ---------------- */
  const liveAvailResp = page.waitForResponse(
    (r) => r.url().includes("/api/chargers") && r.url().includes("available=true") && /[?&]lat=/.test(r.url()),
    { timeout: 45000 },
  );
  await setFilter(page, "Open now", true);
  const liveAvail = await liveAvailResp;
  expect(liveAvail.status() === 200, `open-now: available=true live fetch returned ${liveAvail.status()}`);
  await page.waitForFunction(
    () => {
      const chip = [...document.querySelectorAll('div[role="group"][aria-label="Filters"] dialog button.chip')].find(
        (b) => (document.getElementById(b.getAttribute("aria-labelledby"))?.textContent || "").trim() === "Open now",
      );
      const pressed = chip && chip.getAttribute("aria-pressed") === "true";
      const section = document.querySelector("section");
      const dot = section && section.querySelector(".live-dot");
      const status = dot && dot.parentElement ? dot.parentElement.textContent || "" : "";
      const hasCards = document.querySelectorAll("[data-station]").length > 0;
      const hasEmpty = [...document.querySelectorAll(".card")].some((el) =>
        /No chargers match these filters|No charger nearby yet/.test(el.textContent || ""),
      );
      return pressed && !/Refreshing/.test(status) && (hasCards || hasEmpty);
    },
    null,
    { timeout: 45000 },
  );
  await page.screenshot({ path: openNowScreenshot, fullPage: true });
  const openNowState = await collect(page);

  expect(chipPressed(openNowState, "Open now") === true, "open-now: chip did not activate in live mode");
  const onLeaky = openNowState.cards.filter((c) => c.notLive);
  expect(onLeaky.length === 0, `open-now: leads rendered as available under Open now (fail-open): ${names(onLeaky)}`);
  messagingChecks(openNowState, "open-now", failures);
  expect(!openNowState.overflowX, "open-now: mobile viewport has horizontal overflow");

  let broadenRecovery = null;
  if (openNowState.cards.length) {
    // Branch A: route-ready results — every card must be live, "Go now", with free ports & sources.
    const notReady = openNowState.cards.filter((c) => !c.pillGo || !(c.freeCount != null && c.freeCount > 0));
    expect(notReady.length === 0, `open-now: non-route-ready cards shown under Open now: ${names(notReady)}`);
    const riskyOpen = openNowState.cards.filter((c) => c.parkingRiskBadge);
    expect(riskyOpen.length === 0, `open-now: paid/limited-parking cards shown while Easy parking is on: ${names(riskyOpen)}`);
    cardEvidenceChecks(openNowState, "open-now", failures);
  } else {
    // Branch B: honest empty state with recovery.
    expect(Boolean(openNowState.emptyText), "open-now: empty state card with guidance is missing");
    expect(
      /No charger nearby yet|No chargers match these filters/.test(openNowState.emptyText),
      `open-now: empty state does not explain that nothing route-ready is available: ${openNowState.emptyText}`,
    );
    expect(openNowState.recoveryButtons.includes("Show all leads"), "open-now: empty state lacks a Show all leads recovery button");
    expect(openNowState.recoveryButtons.includes("Broaden price"), "open-now: empty state lacks a Broaden price recovery button");
    expect(
      Boolean(openNowState.coverage) || /^0 live/.test(openNowState.badgeText),
      "open-now: empty state lacks any low-live-coverage explanation",
    );

    // Exercise the second recovery path (old shell's "Broaden search"): it must restore options.
    const broadenResp = page.waitForResponse(
      (r) => r.url().includes("/api/chargers") && r.url().includes("available=true") && r.url().includes("max_price=5") && /[?&]lat=/.test(r.url()),
      { timeout: 45000 },
    );
    await page.getByRole("button", { name: "Broaden price", exact: true }).click({ timeout: 8000 });
    await broadenResp.catch(() => {});
    await page
      .waitForFunction(
        () => {
          const section = document.querySelector("section");
          const dot = section && section.querySelector(".live-dot");
          const status = dot && dot.parentElement ? dot.parentElement.textContent || "" : "";
          return !/Refreshing/.test(status);
        },
        null,
        { timeout: 45000 },
      )
      .catch(() => {});
    await page.waitForFunction(() => document.querySelectorAll("[data-station]").length > 0, null, { timeout: 6000 }).catch(() => {});
    broadenRecovery = await collect(page);
    if (!broadenRecovery.cards.length) {
      failures.push(
        "open-now: 'Broaden price' recovery leaves the list empty — the Open now filter stays active so no lead can ever satisfy it, stranding the driver",
      );
    } else {
      const stillLeaky = broadenRecovery.cards.filter((c) => c.notLive);
      if (chipPressed(broadenRecovery, "Open now") === true && stillLeaky.length) {
        failures.push(`open-now: broadened recovery shows leads while Open now is still active: ${names(stillLeaky)}`);
      }
    }
  }

  await browser.close();

  /* ---------------- Network + console contract ---------------- */
  const ok200 = (test) => responses.some((r) => test(r.url) && r.status === 200);
  expect(ok200((u) => u.includes("/api/chargers") && u.includes("overview=true")), "network: overview /api/chargers 200 not observed");
  expect(ok200((u) => u.includes("/api/chargers") && /[?&]lat=/.test(u)), "network: live /api/chargers (lat/lon) 200 not observed");
  expect(ok200((u) => u.includes("/api/chargers") && u.includes("available=true")), "network: Open now /api/chargers available=true 200 not observed");
  const errorLines = logs.filter((line) => /^error:|^pageerror:/.test(line));
  const mapBackdropNoise = errorLines.filter((line) => isMapBackdropNoise(line));
  const consoleErrors = errorLines.filter((line) => !isMapBackdropNoise(line));
  expect(consoleErrors.length === 0, `console: browser errors detected: ${consoleErrors.slice(0, 4).join(" | ")}`);

  console.log(
    JSON.stringify(
      {
        ok: failures.length === 0,
        baseUrl,
        screenshot,
        openNowScreenshot,
        defaultView: {
          cards: defaultState.cards.length,
          h1: defaultState.h1,
          modeLabel: defaultState.modeLabel,
          statusText: defaultState.statusText,
          badgeText: defaultState.badgeText,
          chips: defaultState.chips,
          first: defaultState.cards[0] || null,
          idCheck: intelIndex
            ? { checked: idCheck.checked, missing: idCheck.missing.length, unknownParkingRendered: idCheck.unknownParkingNoCaveat.length }
            : { error: intelError },
        },
        detailState,
        overviewOpenNow: {
          cards: overviewOpenNow.cards.length,
          leadsRendered: overviewOpenNow.cards.filter((c) => c.notLive).length,
          emptyText: overviewOpenNow.emptyText,
          recoveryButtons: overviewOpenNow.recoveryButtons,
          recoveredCards: overviewRecovery ? overviewRecovery.cards.length : null,
        },
        live: {
          h1: liveState.h1,
          modeLabel: liveState.modeLabel,
          coverage: liveState.coverage,
          cards: liveState.cards.length,
          liveCards: liveCards.length,
          leadCards: liveState.cards.length - liveCards.length,
          first: liveState.cards[0] || null,
        },
        openNow: {
          branch: openNowState.cards.length ? "route-ready-cards" : "empty-state",
          cards: openNowState.cards.length,
          leadsRendered: onLeaky.length,
          emptyText: openNowState.emptyText,
          recoveryButtons: openNowState.recoveryButtons,
          coverage: openNowState.coverage,
          broadenRecoveredCards: broadenRecovery ? broadenRecovery.cards.length : null,
          first: openNowState.cards[0] || null,
        },
        responses,
        mapBackdropNoise: mapBackdropNoise.length,
        logs: logs.slice(-40).map((l) => l.slice(0, 300)),
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
