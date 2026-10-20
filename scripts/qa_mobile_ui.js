#!/usr/bin/env node
/* Mobile UI contract check for the Volta React SPA (390x844).
 *
 * Ported from the retired vanilla static/ shell to the SPA DOM contract
 * (docs/spa-dom-contract.md). Preserved intent:
 *   - default view renders the broad public inventory with honest labels
 *     (only Leads is ON; source, parking, price, and connector constraints are opt-in),
 *   - every card carries decision pill + source tier + price + availability
 *     honesty ("Not live" for leads) + locate-mode route fact + actions
 *     (Open in Maps / optional provider Start/check / Details) with provenance behind Details,
 *   - "Open now" excludes leads/non-open candidates (national overview has
 *     0 live -> empty state) and the empty state offers working recovery,
 *   - an explicit constrained mode (Source checked + Easy parking ON) hides weaker
 *     candidates, then returning to the broad view reveals them again,
 *     including parking-risk and community-tier ones, all still honestly
 *     badged ("Not live", "Paid parking"/"Limited parking", tier badges),
 *   - transparency surfaces: header count, overview "N leads · not live" badge,
 *     driver guidance, Data honesty disclosure; no console errors.
 *
 * Usage: node scripts/qa_mobile_ui.js [baseUrl]   (default VOLTA_QA_BASE_URL)
 */
const { chromium } = require("playwright");
const { openFilters, closeFilters, setFilter } = require("./qa_browser_harness");

const baseUrl = (process.argv[2] || process.env.VOLTA_QA_BASE_URL || "http://127.0.0.1:8773").replace(/\/$/, "");
const screenshot = process.env.VOLTA_QA_SCREENSHOT || "/tmp/volta-mobile-default-reliable-parking.png";
const openNowScreenshot = process.env.VOLTA_QA_OPEN_NOW_SCREENSHOT || "/tmp/volta-mobile-open-now.png";
const broadScreenshot = process.env.VOLTA_QA_BROAD_SCREENSHOT || "/tmp/volta-mobile-broad-lead-mode.png";
const chromePath = process.env.VOLTA_QA_CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

const CHIP_LABELS = ["Open now", "Source checked", "Easy parking", "Tesla OK", "Fast", "Leads (Verify first)"];
const DEFAULT_ON = ["Leads (Verify first)"];
const DEFAULT_OFF = ["Open now", "Source checked", "Easy parking", "Tesla OK", "Fast"];
const TIERS = ["Live", "Official", "Directory", "Community"];

// Global safety valve: the whole contract must finish well under 2 minutes.
const guard = setTimeout(() => {
  console.error(JSON.stringify({ ok: false, baseUrl, error: "TimeoutError: global 110s guard hit" }));
  process.exit(1);
}, 110000);
guard.unref();

/* Runs in the page: one honest snapshot of the mobile panel + rendered cards. */
const collect = () => {
  const txt = (el) => (el ? el.textContent.replace(/\s+/g, " ").trim() : "");
  const panel = document.querySelector("section");
  const rect = panel ? panel.getBoundingClientRect() : null;
  const h1 = document.querySelector("h1");
  const countSpan = h1 ? h1.querySelector("span.tabular-nums") : null;
  const chipRow = document.querySelector('div[role="group"][aria-label="Filters"]');
  const chips = [...(chipRow ? chipRow.querySelectorAll("dialog button.chip") : [])].map((b) => ({
    label: txt(document.getElementById(b.getAttribute("aria-labelledby"))),
    pressed: b.getAttribute("aria-pressed") === "true",
  }));
  const statusBadge = [...document.querySelectorAll("section .badge")]
    .map(txt)
    .find((t) => /^(?:\d+\s*live\s*·\s*\d+\s*leads|\d+\s*leads\s*·\s*not live)$/i.test(t)) || "";
  const honesty = txt(document.querySelector('button[aria-controls="data-honesty-panel"]'));

  const cards = [...document.querySelectorAll("[data-station]")].map((wrap) => {
    const card = wrap.querySelector("article.card");
    if (!card) return { id: wrap.getAttribute("data-station"), missingCard: true };
    const pill = card.querySelector(".pill");
    const badges = [...card.querySelectorAll(".badge")].map(txt);
    const links = [...card.querySelectorAll("a")];
    const maps = links.find((a) => /Open in Maps/.test(a.textContent));
    const source = links.find((a) => /^Start\/check/.test(txt(a)));
    const details = [...card.querySelectorAll("button")].find((b) => /Details/.test(b.textContent));
    const text = card.innerText.replace(/\s+/g, " ");
    const price = txt([...card.querySelectorAll("span")].find((el) => /^(Free|€\d+\.\d{2}|Unknown)$/.test(txt(el))));
    return {
      id: wrap.getAttribute("data-station"),
      name: txt(card.querySelector("h3")),
      pillLabel: txt(pill),
      pillCls: pill ? pill.className : "",
      pillReason: pill ? pill.getAttribute("title") || "" : "",
      tier: card.querySelector("[data-source-tier]")?.getAttribute("data-source-tier") || "",
      badges: badges.slice(0, 8),
      notLive: badges.includes("Not live") || /\bNot live\b/.test(text),
      risk: /Paid parking|Limited parking/.test(card.querySelector("[data-card-caution]")?.getAttribute("aria-label") || ""),
      price,
      priceOk: /^(Free|€\d+\.\d{2}|Unknown)$/.test(price),
      locateFact: /Locate to see ETA/.test(text),
      cautionCount: card.querySelectorAll("[data-card-caution]").length,
      liveClaim: !!card.querySelector('[data-availability="live"]'),
      mapsHref: maps ? maps.getAttribute("href") || "" : "",
      mapsBlank: !!maps && maps.getAttribute("target") === "_blank" && /noopener/.test(maps.getAttribute("rel") || ""),
      sourceHref: source ? source.getAttribute("href") || "" : "",
      sourceBlank: !!source && source.getAttribute("target") === "_blank" && /noopener/.test(source.getAttribute("rel") || ""),
      detailsBtn: !!details,
      detailsExpanded: details ? details.getAttribute("aria-expanded") === "true" : null,
    };
  });

  const emptyCard = [...document.querySelectorAll(".card")].find((c) =>
    /No chargers match these filters|No charger nearby yet/.test(c.textContent || ""));

  return {
    panel: rect ? { top: Math.round(rect.top), width: Math.round(rect.width), bottom: Math.round(rect.bottom) } : null,
    searchVisible: Boolean(panel?.querySelector('input[role="combobox"]')?.getBoundingClientRect().height),
    h1: txt(h1),
    count: countSpan ? parseInt(txt(countSpan), 10) : NaN,
    // NB: the label is CSS-uppercased in innerText — match the home-first copy case-insensitively.
    modeLabel: /explore spain/i.test(panel ? panel.innerText : ""),
    chips,
    filterRailFits: chipRow ? chipRow.scrollWidth <= chipRow.clientWidth + 1 : false,
    quickChips: chipRow ? [...chipRow.querySelectorAll("button.chip")].filter((button) => !button.closest("dialog")).map(txt) : [],
    noPriceLimit: [...(chipRow?.querySelectorAll("dialog button[aria-pressed]") || [])].some(button => txt(button) === "No limit" && button.getAttribute("aria-pressed") === "true"),
    freePressed: [...(chipRow?.querySelectorAll("button.chip[aria-pressed]") || [])].some(button => !button.closest("dialog") && txt(button) === "Free" && button.getAttribute("aria-pressed") === "true"),
    statusBadge,
    driverGuidance: txt(document.querySelector("[data-driver-guidance]")),
    honesty,
    cards,
    emptyState: emptyCard
      ? { text: txt(emptyCard.querySelector("p")), buttons: [...emptyCard.querySelectorAll("button")].map(txt) }
      : null,
  };
};

/* Card-level contract shared by every phase (default + broad + deep scan). */
/** The sheet slides between split and list layouts; measure geometry once it rests. */
async function waitForSheetToSettle(page) {
  let last = null;
  for (let i = 0; i < 30; i++) {
    await page.waitForTimeout(100);
    const top = await page.evaluate(() => document.querySelector("section").getBoundingClientRect().top);
    if (top === last) return;
    last = top;
  }
}

function cardFailures(cards, label, failures) {
  if (!cards.length) return;
  const bad = (pred, msg) => {
    const hits = cards.filter(pred);
    if (hits.length) failures.push(`${label}: ${msg} (${hits.length}/${cards.length}, e.g. ${hits[0].id || hits[0].name || "?"})`);
  };
  bad((c) => !c.pillLabel, "card missing decision pill");
  // lead/avoid are legitimate backend decision levels (D-084) — "Price lead" store-locator
  // rows and off-cap rows render muted/danger pills instead of crashing.
  bad((c) => c.pillLabel && !/pill-(go|check|verify|lead|avoid)/.test(c.pillCls), "decision pill missing a known tone class");
  bad((c) => !c.tier, "card missing source-tier text (Live/Official/Directory/Community)");
  bad((c) => !c.priceOk, "card price is not Free/€N.NN/Unknown");
  bad((c) => !c.notLive, "overview lead card not marked Not live");
  bad((c) => c.liveClaim, "lead card claims live connector counts");
  bad((c) => !c.mapsHref, "card missing Open in Maps action");
  bad((c) => c.mapsHref && !c.mapsBlank, "Open in Maps not target=_blank rel=noopener");
  bad((c) => c.sourceHref && !/^https?:\/\//.test(c.sourceHref), "Source link is not an absolute public URL");
  bad((c) => c.sourceHref && !c.sourceBlank, "Source link not target=_blank rel=noopener");
  bad((c) => !c.detailsBtn, "card missing Details disclosure button");
  bad((c) => !c.locateFact, "card missing locate-mode route fact (Locate to see ETA before geolocation)");
  bad((c) => c.cautionCount > 1, "collapsed card has more than one caution indicator");
}

async function main() {
  const logs = [];
  const browser = await chromium.launch({ headless: true, executablePath: chromePath });
  const page = await browser.newPage({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
  });
  page.on("console", (msg) => logs.push(`${msg.type()}: ${msg.text()}`));
  page.on("pageerror", (err) => logs.push(`pageerror: ${err.message}`));

  const setChip = (name, pressed) => setFilter(page, name, pressed);
  const readCount = () =>
    page.evaluate(() => {
      const s = document.querySelector("h1 span.tabular-nums");
      return s ? parseInt(s.textContent.trim(), 10) : NaN;
    });

  // ---- Phase 1: default national overview -------------------------------
  await page.goto(`${baseUrl}/`, { waitUntil: "domcontentloaded", timeout: 20000 });
  await page.waitForSelector("[data-station] article.card", { timeout: 30000 });
  await page.waitForTimeout(900); // let card entrance animations + map settle
  await page.screenshot({ path: screenshot, fullPage: true });
  await openFilters(page);
  const modalOptions = await page.getByRole("dialog", { name: "All filters", exact: true }).locator("button.chip[aria-labelledby][aria-pressed]").count();
  await closeFilters(page);
  const result = await page.evaluate(collect);

  // A driver can use a full-height list without losing their discovery state.
  await page.getByRole("button", { name: "Show full charger list", exact: true }).click();
  await waitForSheetToSettle(page);
  const fullList = await page.evaluate(() => ({
    top: document.querySelector("section").getBoundingClientRect().top,
    headerBottom: document.querySelector("header").getBoundingClientRect().bottom,
    search: document.querySelector('input[role="combobox"]').value,
    count: document.querySelector("h1").textContent.trim(),
    scrollerHeight: document.querySelector("[data-result-scroller]").clientHeight,
    mapHidden: getComputedStyle(document.querySelector(".discovery-map")).visibility === "hidden",
  }));
  await page.getByRole("button", { name: "Show map and chargers", exact: true }).click();
  await waitForSheetToSettle(page);
  const restoredSplit = await page.evaluate(collect);

  // Details disclosure on the first card: provenance must live behind it.
  await page.locator("[data-station]").first().getByRole("button", { name: "Details" }).click();
  const deep = await page
    .waitForFunction(() => {
      // Provenance headings are CSS-uppercased; use textContent (untransformed source text).
      const card = document.querySelector("[data-station] article.card");
      const t = card ? card.textContent : "";
      return /Public sources \(\d+\)/i.test(t)
        ? {
            why: /Why “/i.test(t),
            chargingClaim: /Charging claim/i.test(t),
            publicSources: /Public sources \(\d+\)/i.test(t),
            viewSource: [...card.querySelectorAll("a")].some((a) => /view source/i.test(a.textContent)),
            expanded: [...card.querySelectorAll("button")].some((b) => b.getAttribute("aria-expanded") === "true"),
          }
        : false;
    }, null, { timeout: 6000 })
    .then((h) => h.jsonValue())
    .catch(() => null);

  // ---- Phase 2: Open now on the overview (0 live -> honest empty state) --
  const openNowResponse = page
    .waitForResponse((r) => r.url().includes("/api/chargers") && r.url().includes("available=true"), { timeout: 20000 })
    .catch(() => null);
  await setChip("Open now", true);
  const openNowApiHit = (await openNowResponse) != null;
  await page.waitForFunction(() => {
    const c = [...document.querySelectorAll("dialog button.chip")].find((b) => document.getElementById(b.getAttribute("aria-labelledby"))?.textContent.trim() === "Open now");
    const pressed = c && c.getAttribute("aria-pressed") === "true";
    const cards = document.querySelectorAll("[data-station]").length;
    const empty = [...document.querySelectorAll(".card")].some((el) =>
      /No chargers match these filters|No charger nearby yet/.test(el.textContent || ""));
    return pressed && (cards > 0 || empty);
  }, null, { timeout: 15000 });
  await page.waitForTimeout(300);
  await page.screenshot({ path: openNowScreenshot, fullPage: true });
  const openNow = await page.evaluate(collect);

  // Empty-state recovery: "Show all leads" must get the driver back to results.
  let recovery = { attempted: false, recovered: false };
  if (openNow.emptyState && openNow.emptyState.buttons.includes("Show all leads")) {
    recovery.attempted = true;
    await page.getByRole("button", { name: "Show all leads", exact: true }).click();
    recovery.recovered = await page
      .waitForSelector("[data-station] article.card", { timeout: 5000 })
      .then(() => true)
      .catch(() => false);
  }

  // Escape hatch the user actually has: untoggle Open now -> cards must return.
  await setChip("Open now", false);
  const escaped = await page
    .waitForSelector("[data-station] article.card", { timeout: 15000 })
    .then(() => true)
    .catch(() => false);

  // ---- Phase 3: explicit constraints, then return to broad discovery -------
  await setChip("Source checked", true);
  await setChip("Easy parking", true);
  await setChip("Leads (Verify first)", true);
  await page.waitForTimeout(500);
  const strict = await page.evaluate(collect);
  const parkingOnCount = await readCount();
  await setChip("Source checked", false);
  await setChip("Easy parking", false);
  await page.waitForTimeout(300);
  await page.screenshot({ path: broadScreenshot, fullPage: true });
  const broad = await page.evaluate(collect);

  // Deep scan of the virtualized broad list: risky + community candidates must
  // exist AND stay honestly badged far beyond the first screenful.
  const scan = { sampled: 0, riskFound: false, communityFound: false, riskExample: "", cards: [] };
  const seen = new Set();
  const fracs = [0, 0.1, 0.12, 0.14, 0.16, 0.3, 0.5, 0.7, 0.8, 0.84, 0.87, 0.9, 0.93, 0.96, 0.985, 1];
  // A rare badge can sit between the fixed stops, so if either is still unseen,
  // sweep the whole list densely (401 stops cover every card of a ~2,300-card list).
  const dense = Array.from({ length: 401 }, (_, i) => i / 400);
  for (const [i, f] of [...fracs, ...dense].entries()) {
    if (i >= fracs.length && scan.riskFound && scan.communityFound) break;
    await page.evaluate((frac) => {
      const el = document.querySelector("[data-result-scroller]");
      if (el) el.scrollTop = frac * (el.scrollHeight - el.clientHeight);
    }, f);
    await page.waitForTimeout(i < fracs.length ? 160 : 120);
    const snap = await page.evaluate(collect);
    for (const c of snap.cards) {
      if (!c.id || seen.has(c.id)) continue;
      seen.add(c.id);
      scan.cards.push(c);
      if (c.risk && !scan.riskFound) {
        scan.riskFound = true;
        scan.riskExample = `${c.name} [${c.badges.join(", ")}]`;
      }
      if (c.tier === "Community") scan.communityFound = true;
    }
  }
  scan.sampled = scan.cards.length;

  await browser.close();

  // ---- Assertions --------------------------------------------------------
  const failures = [];
  const pressedMap = Object.fromEntries(result.chips.map((c) => [c.label, c.pressed]));

  // Shell: bottom sheet + header + chips row (mobile 390x844 contract).
  if (Math.abs(fullList.top - fullList.headerBottom) > 1 || fullList.scrollerHeight < 200 || !fullList.mapHidden) failures.push(`full-list mode does not provide a usable expanded list: ${JSON.stringify(fullList)}`);
  if (fullList.count !== result.h1 || restoredSplit.h1 !== result.h1 || fullList.search !== "") failures.push("switching map/list changed the search or result count");
  if (JSON.stringify(restoredSplit.chips) !== JSON.stringify(result.chips)) failures.push("switching map/list changed filter state");
  if (!restoredSplit.panel || Math.abs(restoredSplit.panel.top - result.panel.top) > 1) failures.push("returning to map/list did not restore the split geometry");
  if (!result.panel) failures.push("panel <section> did not render");
  else {
    if (result.panel.top < 480 || result.panel.top > 500) failures.push(`panel is not the usable bottom sheet (top=${result.panel.top}, expected ~58dvh of 844)`);
    if (result.panel.width < 388) failures.push("panel does not span the mobile width");
    if (result.panel.bottom < 830) failures.push("panel does not reach the bottom of the viewport");
    if (!result.searchVisible) failures.push("mobile discovery panel is missing its visible search control");
  }
  if (!/^\d+ public chargers$/.test(result.h1)) failures.push(`overview h1 is not "N public chargers" (got "${result.h1}")`);
  if (!(result.count > 0)) failures.push("header count is not a positive number");
  if (!result.modeLabel) failures.push("mode label 'Explore Spain' missing on default view");
  const chipLabels = result.chips.map((c) => c.label);
  if (CHIP_LABELS.some((l) => !chipLabels.includes(l))) failures.push(`filter chips incomplete: ${JSON.stringify(chipLabels)}`);
  if (!result.filterRailFits) failures.push("quick filters overflow the mobile panel");
  if (result.quickChips.join("|") !== "Free|Price") failures.push("quick price choices are missing or crowded by advanced filters");
  if (modalOptions !== 6) failures.push(`All filters dialog exposes ${modalOptions} boolean options, expected six`);
  if (!result.noPriceLimit || result.freePressed) failures.push("Default price filter must be No limit with Free off");
  for (const l of DEFAULT_ON) if (!pressedMap[l]) failures.push(`default filter "${l}" is not active`);
  for (const l of DEFAULT_OFF) if (pressedMap[l]) failures.push(`filter "${l}" should be off by default`);

  // Default list: broad discovery stays labeled as non-live, never a route-ready claim.
  if (!result.cards.length) failures.push("no station cards rendered on the default overview");
  cardFailures(result.cards, "default", failures);

  // Transparency surfaces (port of the retired trust strip intent).
  const overviewBadge = /^(\d+)\s*leads\s*·\s*not live$/i.exec(result.statusBadge);
  if (!overviewBadge) failures.push(`overview trust badge 'N leads · not live' missing (got "${result.statusBadge}")`);
  else if (!(Number(overviewBadge[1]) > 0)) failures.push(`status badge reports zero leads on the overview (${result.statusBadge})`);
  if (!result.driverGuidance) failures.push("driver guidance missing");
  if (!result.honesty) failures.push("Data honesty disclosure button missing");

  // Details disclosure: provenance (why / claims / public sources) behind Details.
  if (!deep) failures.push("Details expansion did not reveal provenance (Public sources) on the first card");
  else {
    if (!deep.expanded) failures.push("Details button aria-expanded did not flip to true");
    if (!deep.why) failures.push("expanded Details missing decision reason (Why …)");
    if (!deep.chargingClaim) failures.push("expanded Details missing Charging claim section");
    if (!deep.viewSource) failures.push("expanded Details missing external 'view source' link");
  }
  if (result.cards.some((c) => c.detailsExpanded === true)) failures.push("a card rendered with Details pre-expanded by default");

  // Open now: chip pressed, API refetch, and no lead/non-open candidates shown.
  const onPressed = openNow.chips.find((c) => c.label === "Open now");
  if (!onPressed || !onPressed.pressed) failures.push("Open now chip is not active after tapping it");
  if (!openNowApiHit) failures.push("tapping Open now did not trigger an /api/chargers request with available=true");
  if (openNow.cards.some((c) => c.notLive)) failures.push("Open now still shows lead (Not live) cards");
  if (openNow.cards.length === 0) {
    if (!openNow.emptyState) failures.push("Open now with 0 live results did not show the empty state");
    else {
      if (!/No chargers match these filters/.test(openNow.h1 + " " + (openNow.emptyState.text || "")) && !openNow.emptyState) failures.push("empty state lacks the overview message");
      for (const b of ["Show all leads", "Broaden price"]) {
        if (!openNow.emptyState.buttons.includes(b)) failures.push(`empty state missing recovery button "${b}"`);
      }
    }
    if (recovery.attempted && !recovery.recovered)
      failures.push("empty-state recovery failed: 'Show all leads' does not clear the Open now dead end (available filter still excludes every lead, list stays empty)");
  }
  if (!escaped) failures.push("untoggling Open now did not bring station cards back");

  // The opt-in constraints must narrow the inventory; returning to broad discovery
  // reveals the risk/community rows again, all with their honesty labels intact.
  if (!strict.cards.length) failures.push("constrained mode rendered no cards");
  cardFailures(strict.cards, "constrained", failures);
  if (strict.cards.some((c) => c.risk)) failures.push("constrained mode (Easy parking ON) shows Paid/Limited parking cards");
  if (strict.cards.some((c) => c.tier === "Community")) failures.push("constrained mode (Source checked ON) shows Community-tier cards");
  if (!(broad.count > strict.count)) failures.push(`broad mode did not reveal more candidates (constrained ${strict.count} -> broad ${broad.count})`);
  if (!broad.cards.length) failures.push("broad mode rendered no cards");
  cardFailures(broad.cards, "broad", failures);
  if (!(parkingOnCount < broad.count)) failures.push(`re-enabling Easy parking did not hide any candidates (broad ${broad.count} vs parking-on ${parkingOnCount}) — parking-risk stations were never revealed`);
  cardFailures(scan.cards, "scan", failures);
  if (scan.sampled < 20) failures.push(`virtualized scan sampled too few cards (${scan.sampled})`);
  if (!scan.riskFound) failures.push("broad mode never rendered a Paid/Limited parking badge in the sampled list");
  if (!scan.communityFound) failures.push("broad mode never rendered a Community-tier badge in the sampled list");

  // Console honesty.
  if (logs.some((l) => /^error:|^pageerror:/.test(l))) failures.push("browser console contains errors");

  const summary = {
    ok: failures.length === 0,
    baseUrl,
    screenshot,
    openNowScreenshot,
    broadScreenshot,
    result: { ...result, cards: result.cards.slice(0, 4) },
    deep,
    openNow: { ...openNow, cards: openNow.cards.slice(0, 3) },
    recovery,
    strict: { ...strict, cards: strict.cards.slice(0, 3), parkingOnCount },
    broad: { ...broad, cards: broad.cards.slice(0, 3), parkingOnCount },
    scan: { sampled: scan.sampled, riskFound: scan.riskFound, riskExample: scan.riskExample, communityFound: scan.communityFound },
    counts: { default: result.count, strict: strict.count, broad: broad.count, parkingOn: parkingOnCount },
    logs,
    failures,
  };
  console.log(JSON.stringify(summary, null, 2));
  process.exit(failures.length ? 1 : 0);
}

main().catch((err) => {
  console.error(JSON.stringify({ ok: false, baseUrl, error: `${err.name}: ${err.message}` }, null, 2));
  process.exit(1);
});
