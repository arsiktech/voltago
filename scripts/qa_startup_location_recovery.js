#!/usr/bin/env node
/* Startup GPS must respect explicit station selection and preserve the installed
 * PWA's fixed, not-live catalogue when nearby requests fail. Uses real MapLibre,
 * the production service worker and synthetic charger responses, not live data. */
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const { installDeterministicBasemap } = require('./qa_browser_harness');
const baseUrl = (process.argv[2] || 'http://127.0.0.1:8773').replace(/\/$/, '');
const chromePath = process.env.VOLTA_QA_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const station = {
  id: 'startup-saved', name: 'Saved Valencia charger', lat: 39.47, lon: -0.377,
  lead: true, reliability: 'official', price_min: null, max_power_kw: 22,
  status: 'UNKNOWN', connector_labels: ['Type 2'],
  verification: { location_verified: true, price_verified: false, live_verified: false, level: 'official' },
  decision: { level: 'verify', label: 'Verify first' },
  source: 'QA official catalogue', source_url: 'https://example.com/charger/startup',
  primary_source: { name: 'QA official catalogue', url: 'https://example.com/charger/startup', kind: 'station_page', quality: 'exact' },
};
const payload = { stations: [], leads: [station], cache: { live_supported: false, fresh_live_authority: false }, meta: { coverage: { mode: 'directory' } } };
const fix = { lat: 39.48, lon: -0.39 };
const guard = setTimeout(() => { console.error('Startup recovery global timeout'); process.exit(1); }, 120000);
guard.unref();

async function prepare(page) {
  await page.addInitScript(() => {
    window.__gpsCalls = 0;
    Object.defineProperty(navigator, 'geolocation', { configurable: true, value: {
      getCurrentPosition(success) { window.__gpsCalls++; window.__gpsSuccess = success; },
    } });
  });
  await installDeterministicBasemap(page);
}
async function observeMap(page) {
  const manifest = await (await page.request.get(`${baseUrl}/.vite/manifest.json`)).json();
  const chunk = Object.values(manifest).map(e => e.file).find(f => /^assets\/maplibre-(?!gl-worker-)[^/]+\.js$/.test(f));
  assert.ok(chunk, 'Production map chunk must exist');
  await page.evaluate(async file => {
    const module = await import(new URL(file, location.origin).href);
    const MapClass = Object.values(module).find(v => typeof v === 'function' && typeof v.prototype?.getSource === 'function' && typeof v.prototype?.getCanvas === 'function');
    const addSource = MapClass.prototype.addSource;
    MapClass.prototype.addSource = function (...args) { window.__map = this; return addSource.apply(this, args); };
  }, `/${chunk}`);
}
async function releaseGPS(page) {
  await page.evaluate(({ lat, lon }) => window.__gpsSuccess({ coords: { latitude: lat, longitude: lon } }), fix);
}
async function camera(page) {
  await page.waitForFunction(() => window.__map && !window.__map.isMoving());
  return page.evaluate(() => ({ center: window.__map.getCenter().toArray(), zoom: window.__map.getZoom() }));
}
async function selection(browser, intent) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce', serviceWorkers: 'block' });
  try {
    const page = await context.newPage();
    await prepare(page);
    let release;
    const ready = new Promise(resolve => { release = resolve; });
    const requests = [];
    await page.route('**/api/chargers?*', async route => { requests.push(route.request().url()); await ready; await route.fulfill({ json: payload }); });
    await page.goto(baseUrl, { waitUntil: 'domcontentloaded' });
    await observeMap(page);
    release();
    await page.waitForFunction(() => window.__gpsCalls === 1 && window.__map?.isStyleLoaded());
    await page.locator('[data-station="startup-saved"]').waitFor();
    if (intent === 'card') {
      await page.locator('[data-station="startup-saved"] article').click({ position: { x: 80, y: 20 } });
    } else {
      // A real canvas click through MapLibre's hit testing, not onSelect or map.fire.
      const point = await page.evaluate(({ lat, lon }) => {
        const p = window.__map.project([lon, lat]);
        const r = window.__map.getCanvas().getBoundingClientRect();
        return { x: r.x + p.x, y: r.y + p.y };
      }, station);
      await page.mouse.click(point.x, point.y);
    }
    await page.locator('.maplibregl-popup').waitFor();
    const before = await camera(page);
    const count = requests.length;
    await releaseGPS(page);
    await page.waitForTimeout(1200); // Includes the area's 1 s automatic-search debounce.
    const after = await camera(page);
    assert.deepEqual(after, before, `Late startup GPS moved the camera after ${intent} selection`);
    assert.equal(await page.locator('.maplibregl-popup').count(), 1, 'Selection must stay open');
    assert.equal(requests.length, count, 'Selection must retain request scope');
    assert.ok(requests.every(url => !new URL(url).searchParams.has('lat')), 'GPS must not replace query coordinates');
    return { intent, before, after, requests };
  } finally { await context.close(); }
}
async function fallback(browser, failure) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, reducedMotion: 'reduce', serviceWorkers: 'allow' });
  try {
    const page = await context.newPage();
    await prepare(page);
    let fail = false;
    const requests = [];
    await context.route('**/api/chargers?*', async route => {
      requests.push(route.request().url());
      // Let the nearby catalogue exhaust its retries before the live failure.
      if (fail && new URL(route.request().url()).searchParams.has('lat')) await new Promise(resolve => setTimeout(resolve, 1500));
      return route.fulfill({ status: fail ? 503 : 200, json: fail ? { error: 'QA network failure' } : payload });
    });
    await page.goto(baseUrl, { waitUntil: 'domcontentloaded' });
    await page.locator('[data-station="startup-saved"]').waitFor();
    await page.evaluate(() => navigator.serviceWorker.ready);
    await page.waitForFunction(() => Boolean(navigator.serviceWorker.controller));
    fail = true;
    if (failure === 'offline') await context.setOffline(true);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.locator('[data-station="startup-saved"]').waitFor();
    await page.waitForFunction(() => window.__gpsCalls === 1);
    await releaseGPS(page);
    await page.getByText('Live data unavailable', { exact: true }).waitFor({ timeout: 20000 });
    await page.waitForTimeout(2500);
    await page.locator('[data-station="startup-saved"]').waitFor({ timeout: 3000 });
    assert.equal(await page.locator('.pill-go, [data-availability="live"]').count(), 0, 'Cached rows must never gain live authority');
    assert.match(await page.locator('[data-station="startup-saved"]').textContent(), /Not live/);
    const cacheKeys = await page.evaluate(async () => {
      const all = [];
      for (const name of await caches.keys()) for (const request of await (await caches.open(name)).keys()) if (request.url.includes('/api/')) all.push(request.url);
      return all;
    });
    assert.ok(cacheKeys.length, 'Fixed catalogue must remain cached');
    for (const key of cacheKeys) {
      const p = new URL(key).searchParams;
      assert.equal(p.get('bbox'), '35.8,-9.6,43.9,3.4');
      assert.equal(p.has('lat') || p.has('lon'), false, 'User location must never be persisted');
    }
    return { failure, controlled: await page.evaluate(() => Boolean(navigator.serviceWorker.controller)), online: await page.evaluate(() => navigator.onLine), cards: await page.locator('[data-station]').count(), cacheKeys, requests };
  } finally { await context.close(); }
}
(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: chromePath });
  const results = [];
  let failed = false;
  try {
    for (const intent of ['card', 'pin']) {
      try { results.push(await selection(browser, intent)); }
      catch (error) { failed = true; results.push({ intent, error: error.message }); }
    }
    for (const failure of ['offline', 'api-503']) {
      try { results.push(await fallback(browser, failure)); }
      catch (error) { failed = true; results.push({ failure, error: error.message }); }
    }
  } finally { await browser.close(); }
  console.log(JSON.stringify({ ok: !failed, results }, null, 2));
  process.exit(failed ? 1 : 0);
})().catch(error => { console.error(error); process.exit(1); });
