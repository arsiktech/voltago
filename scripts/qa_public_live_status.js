#!/usr/bin/env node
"use strict";

// Hermetic status/disclosure journey. Fixtures are synthetic, not claims about real sites.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");
const { installDeterministicBasemap, unexpectedBrowserErrors } = require("./qa_browser_harness");
const base = (process.argv[2] || "http://127.0.0.1:8773").replace(/\/$/, "");
const out = process.env.VOLTA_LIVE_QA_OUT;
const results = [];

function fixture(feed, stale) {
  const endolla = feed === "endolla";
  const source = {
    name: endolla ? "Barcelona Endolla live" : "Spirii AFIR",
    url: "https://example.com/public-live-fixture",
    attribution: endolla ? "Ajuntament de Barcelona" : "Spirii; PowerGo",
    ...(endolla ? { license: "CC BY 4.0", license_url: "https://creativecommons.org/licenses/by/4.0/" } : {}),
    source_fetched_at: "2026-10-20T16:30:00Z",
    source_published_at: "2026-10-20T16:29:00Z",
    source_last_updated: "2024-01-01T00:00:00Z",
  };
  return {
    id: "public-status-fixture", name: "UITest public status disclosure", lat: 41.39, lon: 2.17,
    city: "Barcelona", lead: false, cache_stale: stale, status: "AVAILABLE", available: 1,
    total_evse: 2, price_min: null, is_free: false, is_cheap: false,
    verification: { live_verified: !stale, location_verified: true, price_verified: false, level: stale ? "stale" : "live" },
    decision: { level: "lead", label: "Price lead", reason: "No tariff from this status feed." },
    connector_summary: { available: 1, busy: 1, total: 2, max_power_kw: 22 },
    connectors: [{ label: "Type 2", power_kw: 22, status: "AVAILABLE" }],
    source: source.name, source_url: source.url, sources: [source], primary_source: { ...source, kind: "live_map" },
  };
}

async function run(browser, feed, mobile, theme) {
  let stale = false;
  const context = await browser.newContext({
    viewport: { width: mobile ? 390 : 1440, height: 1000 },
    isMobile: mobile, hasTouch: mobile, deviceScaleFactor: mobile ? 2 : 1,
    serviceWorkers: "block", reducedMotion: "reduce",
  });
  const page = await context.newPage();
  const logs = [];
  page.on("pageerror", error => logs.push(`pageerror: ${error.message}`));
  page.on("console", message => logs.push(`${message.type()}: ${message.text()}`));
  await page.addInitScript(theme => localStorage.setItem("volta-theme", theme), theme);
  await installDeterministicBasemap(page);
  await page.route("**/api/**", route => route.fulfill({ json: route.request().url().includes("/api/chargers")
    ? { stations: [fixture(feed, stale)], leads: [], cache: { stale: false, fresh_live_authority: !stale }, meta: { coverage: { mode: "standard" } } }
    : {} }));
  await page.goto(base, { waitUntil: "domcontentloaded" });
  await page.bringToFront();
  const card = page.locator('[data-station="public-status-fixture"]');
  await card.waitFor({ state: "visible" });
  assert.equal(await card.locator('[data-availability="live"]').count(), 1);
  assert.match(await card.innerText(), /\b1\s+free\b/);
  assert.equal(await card.locator(".pill-go").count(), 0, "Unknown price cannot imply Go now");
  await card.getByRole("button", { name: "Source details for UITest public status disclosure" }).click();
  await card.getByText("Feed published:", { exact: false }).waitFor();
  const text = await card.innerText();
  for (const value of ["Retrieved: 2026-10-20T16:30:00Z", "Feed published: 2026-10-20T16:29:00Z", "Source updated: 2024-01-01T00:00:00Z", fixture(feed, false).sources[0].attribution]) {
    assert(text.includes(value), `Missing disclosure: ${value}`);
  }
  if (feed === "endolla") assert.equal(await card.getByRole("link", { name: "CC BY 4.0" }).getAttribute("href"), "https://creativecommons.org/licenses/by/4.0/");
  assert(!text.includes("Charging tariff"), "Status integration must not fabricate tariffs");
  const metrics = await page.evaluate(() => ({ width: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth, touch: navigator.maxTouchPoints, coarse: matchMedia("(pointer: coarse)").matches, hidden: document.hidden }));
  assert(metrics.scroll <= metrics.width, "Page overflows horizontally");
  assert(!metrics.hidden);
  if (mobile) assert(metrics.touch > 0 && metrics.coarse);
  if (out) {
    fs.mkdirSync(out, { recursive: true });
    await card.getByText("Feed published:", { exact: false }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(out, `${feed}-${mobile ? "mobile" : "desktop"}-${theme}.png`) });
  }
  stale = true;
  await page.reload({ waitUntil: "domcontentloaded" });
  await card.locator('[data-availability="not-live"]').waitFor();
  assert(!/\b1\s+free\b/.test(await card.innerText()), "Cached feed cannot claim current availability");
  assert.equal(await card.locator(".pill-go").count(), 0);
  assert.deepEqual(unexpectedBrowserErrors(logs, { allowServiceWorkerRegistrationBlocked: true }), []);
  results.push({ feed, mobile, theme, metrics, freshDisclosure: true, staleFailClosed: true });
  await context.close();
}

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.VOLTA_QA_CHROME || (process.platform === "darwin" ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" : undefined) });
  try {
    for (const feed of ["endolla", "spirii"]) {
      for (const mobile of [false, true]) await run(browser, feed, mobile, mobile ? "dark" : "light");
    }
    console.log(JSON.stringify({ ok: true, cases: results.length, results }, null, 2));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
