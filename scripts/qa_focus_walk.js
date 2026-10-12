#!/usr/bin/env node
/* Browser-rendered keyboard focus walk for the Volta React SPA (MapLibre backdrop + panel).
 *
 * Ported from the retired static-shell contract. Protected behaviors:
 *  - Search-first tab order: brand -> theme -> region choices -> search -> location ->
 *    quick price choices -> All filters -> refresh -> station cards ->
 *    per-card actions (Open in Maps, optional public Start/check before Details).
 *  - First Tab lands on the brand home link, followed by the discovery controls; the map
 *    backdrop (MapLibre canvas, zoom controls, attribution — all inside the
 *    aria-hidden wrapper) must not appear in the tab order before the core route controls.
 *  - Non-interactive rows (tier/connector badges, decision pills, fact rows, stat tiles)
 *    must stay out of the tab order.
 *  - No browser console errors / page errors during the walk.
 *
 * Requires Playwright (repo root node_modules) and a running local server:
 *   node scripts/qa_focus_walk.js http://127.0.0.1:8773
 */
const { chromium } = require("playwright");

const baseUrl = (process.argv[2] || process.env.VOLTA_QA_BASE_URL || "http://127.0.0.1:8773").replace(/\/$/, "");
const chromePath = process.env.VOLTA_QA_CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

function classifyActiveElement() {
  const el = document.activeElement;
  if (!el || el === document.body) return { label: el ? "body" : "none", text: "" };
  const text = (el.getAttribute("aria-label") || el.getAttribute("title") || el.textContent || el.getAttribute("placeholder") || "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 80);
  const aria = el.getAttribute("aria-label") || "";
  // MapLibre backdrop: the wrapper is aria-hidden, so nothing inside it belongs in the tab order.
  if (el.matches?.(".maplibregl-canvas")) return { label: "map:canvas", text };
  if (el.closest?.(".maplibregl-ctrl")) return { label: "map:ctrl", text };
  if (el.closest?.('div[aria-hidden="true"]')) return { label: "map:hidden", text };
  if (aria === "Show full charger list" || aria === "Show map and chargers") return { label: "view-toggle", text };
  if (aria === "Volta Go home") return { label: "brand", text };
  if (el.closest?.('nav[aria-label="Explore a region"]')) return { label: `region:${text}`, text };
  if (el.hasAttribute("data-price-filter")) return { label: "price:open", text };
  if (el.getAttribute("aria-haspopup") === "dialog") return { label: "filters:open", text };
  if (aria === "Use my location") return { label: "locate", text };
  if (aria === "Refresh live data" || aria === "Refresh known charger leads") return { label: "refresh", text };
  if (aria.startsWith("Switch to")) return { label: "theme", text };
  if (el.matches?.('input[inputmode="search"]')) return { label: "search", text };
  if (aria === "Clear search") return { label: "search:clear", text };
  if (el.matches?.("button.chip[aria-pressed]")) return { label: `filter:${text}`, text };
  if (el.matches?.('div[role="button"][tabindex="0"]') && el.closest?.("[data-station]")) {
    return { label: "card", text: el.querySelector("h3")?.textContent?.trim() || text };
  }
  if (el.closest?.("[data-station]")) {
    // Cards label actions with station context ("Open <name> in Maps", optional public start page).
    if (el.matches?.("a") && /(?:^|\s)in Maps$|Open in Maps/.test(text)) return { label: "card:maps", text };
    if (el.matches?.("a") && (text.startsWith("Start/check") || text.startsWith("Open public start page"))) return { label: "card:start", text };
    if (el.matches?.("button") && el.hasAttribute("aria-expanded")) return { label: "card:details", text };
    if (el.matches?.("a")) return { label: "card:link", text };
    if (el.matches?.("button")) return { label: "card:button", text };
  }
  if (el.matches?.("summary")) return { label: "caveat", text };
  if (el.matches?.("button") && text === "Search this area") return { label: "area-search", text };
  if (el.matches?.("ul li button") && el.closest?.("section")) return { label: "geocode", text };
  const tag = el.tagName.toLowerCase();
  const role = el.getAttribute("role") || "";
  const interactive =
    ["a", "button", "input", "select", "textarea", "summary", "iframe", "audio", "video"].includes(tag) ||
    ["button", "link", "checkbox", "switch", "textbox", "combobox", "menuitem"].includes(role);
  const cls = typeof el.className === "string" && el.className ? `.${el.className.trim().split(/\s+/).slice(0, 3).join(".")}` : "";
  return { label: `${interactive ? "other" : "noninteractive"}:${tag}${cls}`, text };
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
  // National overview paints leads-only cards by default; wait for the first card's select target.
  await page.waitForSelector('[data-station] div[role="button"][tabindex="0"]', { timeout: 30000 });
  // The MapLibre backdrop is lazy-loaded; wait for it so the walk sees the final tab order.
  const mapPresent = await page
    .waitForSelector(".maplibregl-canvas", { timeout: 15000 })
    .then(() => true)
    .catch(() => false);
  await page.waitForTimeout(400);
  await page.evaluate(() => {
    document.activeElement?.blur?.();
    document.body.focus();
  });

  const focusWalk = [];
  for (let i = 0; i < 28; i += 1) {
    await page.keyboard.press("Tab");
    focusWalk.push(await page.evaluate(classifyActiveElement));
  }
  await browser.close();

  const labels = focusWalk.map(item => item.label);
  const firstIndex = token => labels.indexOf(token);
  const failures = [];

  const required = [
    "brand",
    "view-toggle",
    "theme",
    "region:Mainland",
    "region:Canary Islands",
    "region:Balearic Islands",
    "search",
    "locate",
    "filter:Free",
    "price:open",
    "filters:open",
    "refresh",
    "card",
    "card:maps",
    "card:details",
  ];
  required.forEach(token => {
    if (!labels.includes(token)) failures.push(`focus walk missing ${token}`);
  });

  const orderedPrefix = ["brand", "theme", "region:Mainland", "region:Canary Islands", "region:Balearic Islands", "search", "locate", "filter:Free", "price:open", "filters:open", "view-toggle", "refresh"];
  const prefixPositions = orderedPrefix.map(firstIndex);
  if (prefixPositions.some(pos => pos < 0) || prefixPositions.join(",") !== [...prefixPositions].sort((a, b) => a - b).join(",")) {
    failures.push(`top controls focus order drifted: ${labels.join(" > ")}`);
  }

  const cardIdx = firstIndex("card");
  const mapIdx = firstIndex("card:maps");
  const startIdx = firstIndex("card:start");
  const detailsIdx = firstIndex("card:details");
  if (
    [cardIdx, mapIdx, detailsIdx].some(pos => pos < 0) ||
    !(cardIdx < mapIdx && mapIdx < detailsIdx)
  ) {
    failures.push(`station card focus order drifted: ${labels.join(" > ")}`);
  }
  if (startIdx !== -1 && !(cardIdx < startIdx && startIdx < detailsIdx)) {
    failures.push(`station card start/check focus order drifted: ${labels.join(" > ")}`);
  }

  const firstAppFocus = labels.find(label => label !== "body" && label !== "none");
  if (firstAppFocus !== "brand") {
    failures.push(`first focus target should be brand (Volta Go home), got ${firstAppFocus || "none"}`);
  }

  const coreControlsIdx = firstIndex("card:details") === -1 ? labels.length : firstIndex("card:details");
  const earlyMap = labels.findIndex(label => label.startsWith("map:"));
  if (earlyMap !== -1 && earlyMap < coreControlsIdx) {
    failures.push(
      `map focus target appears before core route controls (aria-hidden MapLibre backdrop must stay out of the early tab order): ${labels.join(" > ")}`,
    );
  }

  const nonInteractive = labels.filter(label => label.startsWith("noninteractive:"));
  if (nonInteractive.length) {
    failures.push(`non-interactive elements are tabbable (badges/pills/fact rows/stat tiles must stay out of the tab order): ${nonInteractive.join(", ")}`);
  }

  const unexpectedErrors = logs.filter(line => /^error:|^pageerror:/.test(line));
  if (unexpectedErrors.length) failures.push(`browser console contains errors: ${unexpectedErrors.join(" | ")}`);

  console.log(JSON.stringify({ ok: failures.length === 0, baseUrl, mapPresent, labels, focusWalk, logs, failures }, null, 2));
  process.exit(failures.length ? 1 : 0);
}

main().catch(err => {
  console.error(JSON.stringify({ ok: false, baseUrl, error: `${err.name}: ${err.message}` }, null, 2));
  process.exit(1);
});
