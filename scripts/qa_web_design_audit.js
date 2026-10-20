#!/usr/bin/env node
"use strict";

// Synthetic browser regression for the presentation pass, not real charger evidence.
const assert = require("node:assert/strict");
const { chromium } = require("playwright");
const { installDeterministicBasemap, setFilter, unexpectedBrowserErrors } = require("./qa_browser_harness");
const base = (process.argv[2] || "http://127.0.0.1:8773").replace(/\/$/, "");
const results = [];
const ports = Array.from({ length: 10 }, (_, index) => ({
  connector_id: `port-${index}`, label: "Wall outlet (connector unspecified)", count: 1, power_kw: 3.6,
}));
const station = {
  id: "audit-summary", name: "UITest compact station", city: "Barcelona", lat: 41.38, lon: 2.18,
  lead: true, price_min: 0.22, connectors: ports, connector_count: 10, max_power_kw: 3.6,
  verification: { price_verified: true, location_verified: true }, reliability: "official-cheap",
  decision: { level: "verify", label: "Verify first", reason: "Not live. Confirm before driving." },
  access: { level: "restricted", label: "Members only", note: "Membership required." },
  parking: { paid: true, label: "Paid parking", note: "Parking is charged separately." },
  claim_conflict: { summary: "Confirm the charging and parking sources." },
  pricing: [{ valid_from: "00:00", valid_until: "08:00", price: 0.22 }],
  price_window_status: { label: "€0.22/kWh · 00:00–08:00", is_currently_within_standard: false },
};
const unknown = { ...station, id: "audit-unknown", name: "UITest unknown price", price_min: null, detail_snapshot: "unavailable", lat: 41.381 };
const outage = { ...station, id: "audit-outage", name: "UITest outage", status: "OUT_OF_SERVICE", lat: 41.382 };
const place = (name) => name === "Paris"
  ? { name, label: "Paris, France", lat: 48.85, lon: 2.35, bbox: { latitude_sw: 48.8, latitude_ne: 48.9, longitude_sw: 2.3, longitude_ne: 2.4 } }
  : { name: "Barcelona", label: "Barcelona, Spain", lat: 41.38, lon: 2.18, bbox: { latitude_sw: 41.3, latitude_ne: 41.5, longitude_sw: 2.05, longitude_ne: 2.25 } };

async function search(page, text) {
  await page.getByRole("combobox").fill(text);
  await page.getByRole("option").first().waitFor();
  await page.getByRole("combobox").press("ArrowDown");
  await page.getByRole("combobox").press("Enter");
  await page.waitForTimeout(1000);
}

async function run(browser, width, theme) {
  const mobile = width < 768;
  const context = await browser.newContext({ viewport: { width, height: mobile ? 844 : 900 }, isMobile: mobile, hasTouch: mobile, deviceScaleFactor: mobile ? 2 : 1, serviceWorkers: "block" });
  await context.addInitScript(theme => {
    localStorage.setItem("volta-theme", theme);
    window.pinText = [];
    const fill = CanvasRenderingContext2D.prototype.fillText;
    CanvasRenderingContext2D.prototype.fillText = function(text, ...args) { window.pinText.push(text); return fill.call(this, text, ...args); };
  }, theme);
  const page = await context.newPage();
  page.setDefaultTimeout(12000);
  const logs = [];
  page.on("pageerror", e => logs.push(`pageerror: ${e.message}`));
  page.on("console", m => logs.push(`${m.type()}: ${m.text()}`));
  await installDeterministicBasemap(page);
  await page.route("**/api/**", route => {
    const url = new URL(route.request().url());
    const payload = url.pathname === "/api/chargers"
      ? { stations: [], leads: [station, unknown, outage], cache: { stale: false }, meta: { truncated: true, coverage: { mode: "directory" } } }
      : url.pathname === "/api/geocode" ? { results: [place(url.searchParams.get("q"))] }
      : {};
    return url.pathname === "/api/station" ? route.abort("failed") : route.fulfill({ json: payload });
  });
  await page.goto(base, { waitUntil: "domcontentloaded" });
  await page.bringToFront();
  const card = page.locator('[data-station="audit-summary"]');
  await card.waitFor();
  await page.waitForTimeout(1200);
  const arrival = await page.evaluate(() => ({
    top: document.querySelector(".discovery-panel").getBoundingClientRect().top,
    mapHeight: document.querySelector(".discovery-map").getBoundingClientRect().height,
    search: document.querySelector('[role="combobox"]').getBoundingClientRect().toJSON(),
    touch: navigator.maxTouchPoints, coarse: matchMedia("(pointer:coarse)").matches, hidden: document.hidden,
  }));
  assert(!arrival.hidden);
  if (mobile) {
    assert(arrival.touch > 0 && arrival.coarse);
    assert(arrival.top > 480 && arrival.top < 500);
    assert(arrival.mapHeight > 420);
    assert(arrival.search.bottom < 844 && arrival.search.height >= 44);
    await page.getByRole("button", { name: "Show full charger list", exact: true }).tap();
  }
  await search(page, "Barcelona");
  assert.match(await card.innerText(), /Charging price/);
  assert.match(await card.innerText(), /10 × Wall outlet, type not published · 3.6 kW/);
  assert.equal(await card.locator("[data-card-caution]").count(), 1);
  assert.equal(await card.locator(".pill").count(), 1);
  assert.match(await card.locator("[data-card-caution]").getAttribute("aria-label"), /Restricted; Sources disagree; Paid parking; Time-limited price/);
  assert.equal(await card.locator('[data-availability="not-live"]').count(), 1);
  assert.equal(await card.locator("[data-connector-summary] p").count(), 1);
  assert.match(await page.locator('[data-station="audit-unknown"]').innerText(), /Unknown/);
  assert.match(await page.locator('[data-station="audit-outage"] [data-card-caution]').innerText(), /Out of service/);
  assert.match(await card.ariaSnapshot(), /Restricted; Sources disagree; Paid parking; Time-limited price/);
  const contrast = await card.evaluate(card => {
    const ctx = document.createElement('canvas').getContext('2d', { willReadFrequently: true });
    const surface = getComputedStyle(document.querySelector('.discovery-panel')).backgroundColor;
    const luminance = rgb => [...rgb].slice(0, 3).map(c => { const n = c / 255; return n <= 0.04045 ? n / 12.92 : ((n + 0.055) / 1.055) ** 2.4; }).reduce((sum, n, i) => sum + n * [0.2126, 0.7152, 0.0722][i], 0);
    const color = (value, background) => { ctx.clearRect(0, 0, 1, 1); if (background) { ctx.fillStyle = background; ctx.fillRect(0, 0, 1, 1); } ctx.fillStyle = value; ctx.fillRect(0, 0, 1, 1); return ctx.getImageData(0, 0, 1, 1).data; };
    return ['.pill', '[data-card-caution]', '[data-source-tier]', '[data-connector-summary] p', '[data-station="audit-outage"] [data-card-caution]'].map(selector => {
      const el = selector.startsWith("[data-station") ? document.querySelector(selector) : card.querySelector(selector);
      const css = getComputedStyle(el);
      const bg = luminance(color(css.backgroundColor, surface)), fg = luminance(color(css.color));
      return { selector, ratio: (Math.max(bg, fg) + 0.05) / (Math.min(bg, fg) + 0.05) };
    });
  });
  assert(contrast.every(sample => sample.ratio >= 4.5), JSON.stringify(contrast));
  const details = card.getByRole("button", { name: /Source details for/ });
  await details.focus();
  await page.keyboard.press("Enter");
  await card.getByRole("region", { name: "Connector inventory" }).waitFor();
  assert.equal(await card.locator('[aria-label="Connector inventory"] li').count(), 10);
  for (const text of ["Membership required.", "Parking is charged separately.", "Confirm the charging and parking sources.", "Price window, not active now:"]) assert((await card.innerText()).includes(text));
  await details.press("Escape");
  assert.equal(await details.getAttribute("aria-expanded"), "false");
  assert(await details.evaluate(el => document.activeElement === el));
  const deferred = page.locator('[data-station="audit-unknown"]');
  await deferred.getByRole("button", { name: /Source details for/ }).click();
  await deferred.getByRole("button", { name: "Retry details" }).waitFor();
  assert((await deferred.innerText()).includes("Membership required."), "Detail failure must not hide restrictions");
  await deferred.getByRole("button", { name: /Source details for/ }).click();
  await setFilter(page, "Open now", true);
  assert.equal(await page.locator("[data-station]").count(), 0, "Not-live results cannot survive Open now");
  await page.getByRole("button", { name: "Show all leads", exact: true }).click();
  await card.waitFor();
  if (mobile) await page.getByRole("button", { name: "Show map and chargers", exact: true }).tap();
  await search(page, "Paris");
  if (mobile) await page.getByRole("button", { name: "Show map and chargers", exact: true }).tap();
  await page.getByRole("navigation", { name: "Explore a region" }).getByRole("button", { name: "Spain", exact: true }).waitFor();
  await page.getByRole("button", { name: "Spain", exact: true }).click();
  await page.getByRole("button", { name: "Mainland", exact: true }).waitFor();
  await page.emulateMedia({ reducedMotion: "reduce" });
  if (mobile) {
    await page.getByRole("button", { name: "Show full charger list", exact: true }).tap();
    assert(await page.locator(".discovery-panel").evaluate(el => getComputedStyle(el).transitionDuration.split(",").every(value => parseFloat(value) <= 0.001)));
  }
  const metrics = await page.evaluate(() => ({
    width: innerWidth, client: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth,
    pinText: window.pinText,
    smallTargets: [...document.querySelectorAll("button,a[href]")].filter(el => !el.closest('[aria-hidden="true"]') && el.getClientRects().length && getComputedStyle(el).visibility !== "hidden").map(el => ({ name: el.getAttribute("aria-label") || el.textContent.trim(), rect: el.getBoundingClientRect() })).filter(({rect}) => rect.top >= 0 && rect.bottom <= innerHeight && (rect.width < 43.9 || rect.height < 43.9)).map(({name,rect}) => ({name,width:rect.width,height:rect.height})),
  }));
  assert.equal(metrics.width, width);
  assert.equal(metrics.client, width);
  assert(metrics.scroll <= width);
  assert.deepEqual(metrics.smallTargets, []);
  assert(metrics.pinText.includes("€0.22") && metrics.pinText.includes("3.6 kW"));
  assert(!metrics.pinText.some(text => /Wall outlet|connector unspecified/.test(text)));
  // The deliberate failed detail request is expected; JavaScript errors are not.
  const unexpected = unexpectedBrowserErrors(logs, { allowServiceWorkerRegistrationBlocked: true }).filter(line => !/Failed to load resource: net::ERR_FAILED/.test(line));
  assert.deepEqual(unexpected, []);
  results.push({ width, theme, mobile, arrival, contrast, overflow: false, targets44px: true, details: true, errorRecovery: true, keyboard: true, reducedMotion: true, regionContext: true, pinLabels: true });
  await context.close();
}

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.VOLTA_QA_CHROME || (process.platform === "darwin" ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" : undefined) });
  try {
    for (const width of [320, 390, 1440]) for (const theme of ["light", "dark"]) await run(browser, width, theme);
    console.log(JSON.stringify({ ok: true, cases: results.length, results }, null, 2));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
