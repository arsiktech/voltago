#!/usr/bin/env node
"use strict";

// Hermetic browser contract for the driver's chosen charging-price cap. The API
// deliberately returns all rows so client filtering is tested independently of
// server filtering. Native controls, request keys and query-cache reuse are real.
const assert = require("node:assert/strict");
const { chromium } = require("playwright");
const {
  installDeterministicBasemap,
  unexpectedBrowserErrors,
  openFilters,
  openPriceFilters,
  closeFilters,
} = require("./qa_browser_harness");
const baseUrl = (process.argv[2] || process.env.VOLTA_QA_BASE_URL || "http://127.0.0.1:8773").replace(/\/$/, "");
const chromePath = process.env.VOLTA_QA_CHROME
  || (process.platform === "darwin" ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" : undefined);
const results = [];
const logs = [];
const guard = setTimeout(() => {
  console.error(JSON.stringify({ ok: false, baseUrl, results, error: "Price slider contract exceeded 100 seconds" }));
  process.exit(1);
}, 100000);
guard.unref();

function station(id, price, extra = {}) {
  const url = `https://example.org/price-contract/${id}`;
  return {
    id, name: `Price contract ${id}`, lat: 40.4168, lon: -3.7038,
    city: "Madrid", lead: true, reliability: "official_directory",
    price_min: price, is_free: false,
    verification: { location_verified: true, price_verified: true, live_verified: false },
    decision: { level: "verify", label: "Verify first", reason: "Source-reported tariff; verify before driving." },
    connector_summary: { total: 2, max_power_kw: 22 },
    parking: { kind: "unknown", label: "Parking unknown" },
    primary_source: { name: "QA public directory", kind: "station_page", url, quality: "exact" },
    source_url: url, sources: [{ name: "QA public directory", url }],
    charge_claim: { type: "charging_price", source: "QA public directory", url, price_min: price },
    parking_claim: { kind: "unknown", source: "QA public directory", url, summary: "Check onsite parking signs." },
    ...extra,
  };
}
const rows = [
  station("reported-free", null, { is_free: true }),
  station("cached-ten", 0.10, { cache_stale: true }),
  station("eighteen", 0.18),
  station("forty", 0.40),
  station("sixty-five", 0.65),
  station("eighty", 0.80),
  station("unknown", null),
  station("contradictory-free", 0.80, { is_free: true }),
];
const cappedIds = {
  18: ["reported-free", "cached-ten", "eighteen"],
  40: ["reported-free", "cached-ten", "eighteen", "forty"],
  65: ["reported-free", "cached-ten", "eighteen", "forty", "sixty-five"],
};
const countSelector = "h1 span.tabular-nums";

async function assertInventory(page, expected) {
  await page.waitForFunction(({ selector, count }) =>
    Number(document.querySelector(selector)?.textContent?.trim()) === count,
  { selector: countSelector, count: expected.length }, { timeout: 10000 });
  if (expected.length) await page.locator("[data-station]").first().waitFor({ state: "visible" });
  await page.waitForFunction(() => document.querySelector("[data-result-scroller]")?.clientHeight > 0);
  const seen = new Set();
  const safety = [];
  // Inspect every virtualized row by walking the real result scroller. An absent
  // unknown-price row cannot hide outside the first screenful and pass this check.
  const geometry = await page.locator("[data-result-scroller]").evaluate(el => ({
    height: el.clientHeight, maximum: Math.max(0, el.scrollHeight - el.clientHeight),
  }));
  const step = Math.max(80, Math.floor(geometry.height / 2));
  for (let top = 0; top <= geometry.maximum + step; top += step) {
    await page.locator("[data-result-scroller]").evaluate((el, top) => { el.scrollTop = top; }, top);
    await page.waitForTimeout(35);
    for (const row of await page.locator("[data-station]").evaluateAll(wraps => wraps.map(wrap => ({
      id: wrap.getAttribute("data-station"),
      availability: wrap.querySelector("[data-availability]")?.getAttribute("data-availability"),
      go: Boolean(wrap.querySelector(".pill-go")),
    })))) {
      seen.add(row.id);
      safety.push(row);
    }
  }
  assert.deepEqual([...seen].sort(), [...expected].sort(), "The rendered list must exactly match the chosen cap");
  assert(safety.every(row => row.availability === "not-live" && !row.go), "A charging-price choice must not grant live availability to directory/cached rows");
  await page.locator("[data-result-scroller]").evaluate(el => { el.scrollTop = 0; });
}

async function moveTo(page, slider, cents) {
  await slider.focus();
  const current = Number(await slider.inputValue());
  const key = current <= cents ? "ArrowRight" : "ArrowLeft";
  for (let i = 0; i < Math.abs(cents - current); i += 1) await page.keyboard.press(key);
  assert.equal(await slider.inputValue(), String(cents), "Native arrow keys must move one cent per step");
}

async function runCase(browser, theme, width) {
  const key = `${theme}-${width}`;
  const requests = [];
  const context = await browser.newContext({ viewport: { width, height: 900 }, serviceWorkers: "block", reducedMotion: "reduce" });
  const page = await context.newPage();
  page.setDefaultTimeout(12000);
  page.on("console", message => logs.push(`${message.type()}: [${key}] ${message.text()}`));
  page.on("pageerror", error => logs.push(`pageerror: [${key}] ${error.message}`));
  await page.addInitScript(theme => localStorage.setItem("volta-theme", theme), theme);
  await installDeterministicBasemap(page);
  await page.route("**/api/chargers**", async route => {
    const url = new URL(route.request().url());
    requests.push(url.searchParams.get("max_price"));
    await route.fulfill({ json: { stations: [], leads: rows, cache: { stale: false }, meta: { coverage: { mode: "degraded", live_route_ready: 0, lead_count: rows.length } } } });
  });
  await page.goto(`${baseUrl}/`, { waitUntil: "domcontentloaded" });
  await assertInventory(page, rows.map(row => row.id));
  assert(requests.includes("5.0"), "No limit preserves the broad API envelope");
  const priceTrigger = page.locator("[data-price-filter]");
  const quickPriceBox = await priceTrigger.boundingBox();
  assert(quickPriceBox && quickPriceBox.height >= 44, "The quick price control has a 44px touch target");
  assert.equal(await priceTrigger.getAttribute("aria-label"), "Charging price");
  assert.equal((await priceTrigger.textContent()).trim(), "Price");
  const quickFree = page.getByRole("group", { name: "Filters", exact: true }).getByRole("button", { name: "Free", exact: true });
  assert.equal(await quickFree.getAttribute("aria-pressed"), "false");

  const { dialog, slider } = await openPriceFilters(page);
  assert(await slider.evaluate(el => el === document.activeElement), "The price opener focuses the native range");
  assert.equal(await slider.getAttribute("type"), "range");
  assert.equal(await slider.getAttribute("min"), "0");
  assert.equal(await slider.getAttribute("max"), "101");
  assert.equal(await slider.getAttribute("step"), "1");
  assert.equal(await slider.inputValue(), "101");
  assert.equal(await slider.getAttribute("aria-valuetext"), "No price limit");
  assert.equal(await dialog.getByRole("button", { name: "No limit", exact: true }).getAttribute("aria-pressed"), "true");
  const sliderBox = await slider.boundingBox();
  assert(sliderBox && sliderBox.height >= 44, "The range has a usable mobile touch target");
  assert.equal(await dialog.locator("button.chip[aria-labelledby][aria-pressed]").count(), 6);

  await slider.press("Home");
  assert.equal(await slider.inputValue(), "0", "Home selects Free");
  assert.equal(await quickFree.getAttribute("aria-pressed"), "true");
  await moveTo(page, slider, 18);
  await closeFilters(page);
  assert(await priceTrigger.evaluate(el => el === document.activeElement), "Done returns focus to the price opener");
  await assertInventory(page, cappedIds[18]);
  await page.waitForFunction(() => document.querySelector('[data-price-filter]')?.textContent.includes("€0.18"));
  await page.waitForTimeout(500);
  assert(requests.includes("0.18"), "The selected cap reaches the API");
  const eighteenRequests = requests.filter(price => price === "0.18").length;

  for (const cents of [40, 65]) {
    const opened = await openPriceFilters(page);
    const before = requests.length;
    await moveTo(page, opened.slider, cents);
    assert((await opened.slider.getAttribute("aria-valuetext")).includes((cents / 100).toFixed(2)), "Assistive technology receives the actual euro cap");
    await page.keyboard.press("Escape");
    await opened.dialog.waitFor({ state: "hidden" });
    assert(await priceTrigger.evaluate(el => el === document.activeElement), "Escape returns focus to the actual price opener");
    await assertInventory(page, cappedIds[cents]);
    assert.equal(await priceTrigger.getAttribute("aria-label"), `Charging price: ≤ €${(cents / 100).toFixed(2)}/kWh`, "The active accessible name includes the visible numeric cap for voice control");
    await page.waitForTimeout(500);
    assert(requests.includes((cents / 100).toFixed(2)), "Each numeric cap uses a separate query request");
    assert(requests.length - before <= 2, "Rapid native arrow changes must produce bounded API requests");
  }

  const revisit = await openPriceFilters(page);
  await moveTo(page, revisit.slider, 18);
  await closeFilters(page);
  await assertInventory(page, cappedIds[18]);
  await page.waitForTimeout(500);
  assert.equal(requests.filter(price => price === "0.18").length, eighteenRequests, "Revisiting a fresh cap reuses its own cached query");

  const endpoints = await openPriceFilters(page);
  await endpoints.slider.press("End");
  assert.equal(await endpoints.slider.inputValue(), "101", "End selects No limit");
  assert.equal(await endpoints.slider.getAttribute("aria-valuetext"), "No price limit");
  assert.equal(await endpoints.dialog.getByRole("button", { name: "No limit", exact: true }).getAttribute("aria-pressed"), "true");
  await closeFilters(page);
  await assertInventory(page, rows.map(row => row.id));
  await quickFree.click();
  assert.equal(await quickFree.getAttribute("aria-pressed"), "true");
  await assertInventory(page, ["reported-free"]);
  await quickFree.click();
  assert.equal(await quickFree.getAttribute("aria-pressed"), "false");
  await assertInventory(page, rows.map(row => row.id));

  const reset = await openPriceFilters(page);
  await reset.slider.press("Home");
  await reset.slider.press("ArrowRight");
  assert.equal(await reset.slider.inputValue(), "1");
  await reset.dialog.getByRole("button", { name: "No limit", exact: true }).click();
  assert.equal(await reset.slider.inputValue(), "101", "No limit explicitly clears the cap");
  await reset.slider.press("Home");
  await reset.dialog.getByRole("button", { name: "Clear all filters", exact: true }).click();
  assert.equal(await reset.slider.inputValue(), "101", "Clear filters restores No limit");
  assert.equal(await reset.dialog.getByRole("button", { name: "Leads (Verify first)", exact: true }).getAttribute("aria-pressed"), "true");
  await closeFilters(page);
  const allTrigger = page.getByRole("button", { name: /^All filters(?:\s|$)/ });
  await openFilters(page);
  await closeFilters(page);
  assert(await allTrigger.evaluate(el => el === document.activeElement), "All filters also receives focus when it was the opener");
  await assertInventory(page, rows.map(row => row.id));
  // The native close event is queued. It must not restore the opener again after
  // a driver has already continued their keyboard walk to another control.
  await openPriceFilters(page);
  await page.keyboard.press("Escape");
  await page.keyboard.press("Tab");
  assert(await allTrigger.evaluate(el => el === document.activeElement), "Tab continues from Price to All filters immediately after Escape");
  await page.waitForTimeout(100);
  assert(await allTrigger.evaluate(el => el === document.activeElement), "A queued close event cannot steal the next keyboard target");
  assert.equal(await allTrigger.getAttribute("aria-expanded"), "false");
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "The price dialog cannot overflow mobile or desktop");
  results.push({ theme, width, caps: [0.18, 0.40, 0.65], requests, nativeKeyboard: true, noLimitAndClear: true, freeWithoutNumericTariff: true, unknownExcludedUnderCap: true, contradictoryFreeExcludedUnderCap: true, cachedRowsNotLive: true, separateQueryCache: true, bothOpenersReturnFocus: true });
  await context.close();
}

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: chromePath });
  try {
    for (const theme of ["light", "dark"]) for (const width of [390, 1440]) await runCase(browser, theme, width);
    const failures = unexpectedBrowserErrors(logs, { allowServiceWorkerRegistrationBlocked: true });
    assert.deepEqual(failures, [], "The price interaction must not cause app errors");
    console.log(JSON.stringify({ ok: true, baseUrl, results, logs }, null, 2));
  } finally {
    clearTimeout(guard);
    await browser.close();
  }
})().catch(error => {
  console.error(JSON.stringify({ ok: false, baseUrl, results, logs, error: error.stack || String(error) }, null, 2));
  process.exitCode = 1;
});
