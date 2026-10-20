#!/usr/bin/env node
/* Screen-reader oriented contract for dynamic Volta station cards — React SPA port.
 *
 * Ported from the retired static-shell script. Preserved intent:
 *  1. Station cards render and each card's select control is keyboard-operable and has a
 *     station-specific accessible name that carries the route facts a screen-reader user
 *     needs (station name, decision, price, availability, parking).
 *  2. Per-station action controls (Source link, map/navigate link, details disclosure)
 *     must be identifiable per station in a screen-reader controls/links list — not a
 *     wall of identical generic names.
 *  3. Proof/source provenance facts are readable per station (SPA: Details disclosure →
 *     "Why …", claim sections with "view source", "Public sources (N)").
 *  4. Global accessibility tree exposes brand context, the labeled search control, and the
 *     named filter toggles with their pressed state.
 *  5. Page loads without console errors.
 *
 * SPA surface notes (docs/spa-dom-contract.md): cards are virtualized — only on-screen
 * cards exist in the DOM. Default open is the national leads-only overview, so "Not live"
 * availability is the normal, honest state. The card select target is
 * div[role="button"][tabindex="0"]; it carries no aria-label, so its accessible name is
 * computed from content (allowed for role=button) — we assert that computed name instead
 * of requiring the attribute, but we DO require station-specific names on action controls.
 */
const { chromium } = require("playwright");
const { openFilters, closeFilters } = require("./qa_browser_harness");

const baseUrl = (process.argv[2] || process.env.VOLTA_QA_BASE_URL || "http://127.0.0.1:8773").replace(/\/$/, "");
const chromePath = process.env.VOLTA_QA_CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

/** A short distinctive token of the station name (first word of length >= 3). */
function stationToken(name) {
  const clean = String(name || "").replace(/\s+/g, " ").trim();
  const words = clean.split(" ").filter(w => w.replace(/[^\p{L}\p{N}]/gu, "").length >= 3);
  return (words[0] || clean.slice(0, 6)).toLowerCase();
}

function hasStationToken(label, name) {
  const token = stationToken(name);
  return Boolean(token) && String(label || "").toLowerCase().includes(token);
}

async function main() {
  const logs = [];
  const browser = await chromium.launch({ headless: true, executablePath: chromePath });
  const page = await browser.newPage({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    isMobile: true,
  });
  page.on("console", msg => logs.push(`${msg.type()}: ${msg.text()}`));
  page.on("pageerror", err => logs.push(`pageerror: ${err.message}`));

  await page.goto(`${baseUrl}/`, { waitUntil: "domcontentloaded", timeout: 20000 });
  // Virtualized list: only on-screen cards exist in the DOM.
  await page.waitForSelector('[data-station] article.card', { timeout: 30000 });
  await page.waitForTimeout(900); // let entrance animations / virtualizer settle

  const dom = await page.evaluate(() => {
    const readName = (el) => {
      if (!el) return "";
      const aria = el.getAttribute("aria-label");
      if (aria) return aria;
      const byIds = el.getAttribute("aria-labelledby");
      if (byIds) {
        const joined = byIds.split(/\s+/)
          .map(id => document.getElementById(id)?.textContent || "")
          .join(" ").trim();
        if (joined) return joined;
      }
      // role=button computes its accessible name from content when no label is given.
      return (el.innerText || el.textContent || "").replace(/\s+/g, " ").trim();
    };
    const wraps = [...document.querySelectorAll("[data-station]")].slice(0, 12);
    const first = document.querySelector("[data-station] article.card");
    return {
      firstCardText: (first?.innerText || "").replace(/\s+/g, " ").trim(),
      firstParkingFact: (() => {
        const fact = first?.querySelector('[data-route-fact="parking"]');
        return (fact?.getAttribute("aria-label") || fact?.textContent || "").replace(/\s+/g, " ").trim();
      })(),
      cards: wraps.map(wrap => {
        const card = wrap.querySelector("article.card");
        const sel = card?.querySelector('div[role="button"]');
        const pill = card?.querySelector(".pill");
        const links = [...(card?.querySelectorAll("a") || [])];
        const mapsLink = links.find(a => /open in maps/i.test(a.textContent || ""));
        const sourceLink = links.find(a => /^Start\/check/i.test((a.textContent || "").trim()));
        const detailsBtn = [...(card?.querySelectorAll("button") || [])].find(b => b.hasAttribute("aria-expanded"));
        return {
          id: wrap.getAttribute("data-station") || "",
          hasSelectTarget: Boolean(sel && sel.getAttribute("tabindex") === "0"),
          selHasAriaLabel: Boolean(sel?.getAttribute("aria-label") || sel?.getAttribute("aria-labelledby")),
          selName: readName(sel),
          name: card?.querySelector("h3")?.textContent.trim() || "",
          pill: pill?.textContent.trim() || "",
          pillReason: pill?.getAttribute("title") || "",
          maps: mapsLink ? { name: readName(mapsLink), href: mapsLink.href || "" } : null,
          source: sourceLink ? { name: readName(sourceLink), href: sourceLink.href || "" } : null,
          details: detailsBtn
            ? { name: readName(detailsBtn), expanded: detailsBtn.getAttribute("aria-expanded") }
            : null,
        };
      }),
    };
  });

  // Screen-reader disclosure contract: expand the first card's Details and check that the
  // proof/source provenance becomes readable (old contract: Proof:/Source: facts per card).
  let detailsExpanded = null;
  let expandedText = "";
  try {
    await page.locator('[data-station] article.card button[aria-expanded]').first().click({ timeout: 5000 });
    await page.waitForFunction(() => {
      const b = document.querySelector('[data-station] article.card button[aria-expanded]');
      return b && b.getAttribute("aria-expanded") === "true";
    }, { timeout: 5000 });
    detailsExpanded = true;
    await page.waitForTimeout(500); // disclosure animation
    expandedText = await page.evaluate(() => {
      const card = document.querySelector("[data-station] article.card");
      return (card?.innerText || "").replace(/\s+/g, " ").trim();
    });
  } catch (err) {
    detailsExpanded = false;
  }

  // Accessibility tree snapshot (modern ariaSnapshot, legacy fallback like the old script).
  let ax = "";
  try {
    ax = await page.locator("body").ariaSnapshot({ timeout: 5000 });
  } catch (err) {
    ax = typeof page.accessibility?.snapshot === "function"
      ? JSON.stringify(await page.accessibility.snapshot({ interestingOnly: true }))
      : "";
  }
  const filterDialog = await openFilters(page);
  const filterAx = await filterDialog.ariaSnapshot();
  await closeFilters(page);
  await browser.close();

  const failures = [];
  const cards = dom.cards;
  if (cards.length < 1) failures.push("no station cards rendered ([data-station] article.card)");

  // --- Per-card: select target keyboard-operable + accessible name carries route facts ---
  cards.forEach((card, index) => {
    const n = index + 1;
    if (!card.hasSelectTarget) {
      failures.push(`card ${n} missing div[role="button"][tabindex="0"] select target`);
      return;
    }
    if (!card.name) failures.push(`card ${n} has no station name (h3)`);
    if (!card.selName) {
      failures.push(`card ${n} select target has NO accessible name (no aria-label and no content)`);
      return;
    }
    if (card.name && !hasStationToken(card.selName, card.name)) {
      failures.push(`card ${n} select target accessible name is not station-specific: ${card.selName.slice(0, 120)}`);
    }
    if (!card.pill) {
      failures.push(`card ${n} missing decision pill text`);
    } else if (!card.selName.toLowerCase().includes(card.pill.toLowerCase())) {
      failures.push(`card ${n} decision "${card.pill}" is not part of the select target's readable content`);
    }
    if (!card.pillReason) failures.push(`card ${n} decision pill has no title (decision reason not exposed)`);
    if (!/(free\b|€\s?\d)/i.test(card.selName)) {
      failures.push(`card ${n} readable content is missing a price (Free / €0.NN)`);
    }
    if (!/(\d+\s+free|not live|live · status unknown)/i.test(card.selName)) {
      failures.push(`card ${n} readable content is missing an availability fact (N free / Not live / status unknown)`);
    }
    if (!card.maps) failures.push(`card ${n} missing "Open in Maps" action link`);
    else if (!/^https?:/.test(card.maps.href)) failures.push(`card ${n} "Open in Maps" link has no usable href`);
    if (card.source && !/^https?:/.test(card.source.href)) failures.push(`card ${n} provider link has no usable href`);
    if (!card.details) failures.push(`card ${n} missing Details disclosure button with aria-expanded`);
  });

  // --- Station-specific naming of per-card action controls (old contract: source-link /
  // location / summary aria-labels had to name the station). Aggregated per control type. ---
  const genericNaming = { maps: [], source: [], details: [] };
  cards.forEach((card, index) => {
    if (card.maps && card.name && !hasStationToken(card.maps.name, card.name)) genericNaming.maps.push(index + 1);
    if (card.source && card.name && !hasStationToken(card.source.name, card.name)) genericNaming.source.push(index + 1);
    if (card.details && card.name && !hasStationToken(card.details.name, card.name)) genericNaming.details.push(index + 1);
  });
  if (genericNaming.maps.length) {
    failures.push(`app gap: "Open in Maps" link accessible name is generic (not station-specific) on ${genericNaming.maps.length}/${cards.length} sampled cards — a screen-reader links list reads "Open in Maps" x${genericNaming.maps.length} with no station context`);
  }
  if (genericNaming.source.length) {
    failures.push(`app gap: "Source" link accessible name is generic (not station-specific) on ${genericNaming.source.length}/${cards.length} sampled cards — a screen-reader links list reads "Source" x${genericNaming.source.length} with no station context`);
  }
  if (genericNaming.details.length) {
    failures.push(`app gap: Details disclosure accessible name is generic (not station-specific) on ${genericNaming.details.length}/${cards.length} sampled cards — old contract required a station-specific source-summary label`);
  }

  // --- Parking and full proof/source facts are readable in Details ---
  if (!/Parking:\s*\S+/i.test(expandedText)) {
    failures.push("first card text is missing a readable parking fact");
  }
  if (detailsExpanded === false) {
    failures.push("first card Details disclosure did not expand (aria-expanded never became true)");
  } else if (detailsExpanded) {
    if (!/(why [“"]|charging claim|parking claim)/i.test(expandedText)) {
      failures.push("expanded Details is missing the proof facts (Why … / Charging claim / Parking claim)");
    }
    if (!/(view source|public sources)/i.test(expandedText)) {
      failures.push("expanded Details is missing readable source provenance (view source / Public sources)");
    }
  }

  // --- Global accessibility tree ---
  const axText = typeof ax === "string" ? ax : JSON.stringify(ax);
  const axChecks = [
    ["Volta", "brand context"],
    ["Enter home address or city", "labeled search control"],
    ["All filters", "filter dialog trigger"],
    ["Charging price", "price dialog trigger"],
  ];
  for (const [token, what] of axChecks) {
    if (!axText.includes(token)) failures.push(`accessibility tree missing ${what} ("${token}")`);
  }
  for (const label of ["Open now", "Source checked", "Easy parking", "Maximum charging price", "No limit", "Tesla OK", "Fast", "Leads (Verify first)"]) {
    if (!filterAx.includes(label)) failures.push(`filter dialog accessibility tree missing ${label}`);
  }
  if (!/pressed/i.test(filterAx)) {
    failures.push("accessibility tree does not expose filter toggle pressed state (aria-pressed)");
  }
  if (!/(public chargers|free chargers|chargers ≤ €\d+\.\d{2}\/kWh|nearby)/i.test(axText)) {
    failures.push('accessibility tree missing the list heading context ("N public chargers" / price-filtered scope / "N nearby")');
  }

  const unexpectedErrors = logs.filter(line => /^error:|^pageerror:/.test(line));
  if (unexpectedErrors.length) failures.push(`browser console contains errors: ${unexpectedErrors.join(" | ")}`);

  const sample = cards[0]
    ? {
        name: cards[0].name,
        pill: cards[0].pill,
        pillReason: cards[0].pillReason,
        selHasAriaLabel: cards[0].selHasAriaLabel,
        selNameHead: cards[0].selName.slice(0, 160),
        maps: cards[0].maps,
        source: cards[0].source,
        details: cards[0].details,
      }
    : null;

  console.log(JSON.stringify({
    ok: failures.length === 0,
    baseUrl,
    checkedCards: cards.length,
    sample,
    detailsExpanded,
    notes: "card select target has no explicit aria-label; its accessible name is computed from content (station-specific), so only generic action-control names are treated as gaps",
    logs,
    failures,
  }, null, 2));
  process.exit(failures.length ? 1 : 0);
}

main().catch(err => {
  console.error(JSON.stringify({ ok: false, baseUrl, error: `${err.name}: ${err.message}` }, null, 2));
  process.exit(1);
});
