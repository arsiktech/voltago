#!/usr/bin/env node
'use strict';

// Real canvas taps, geometry and hit-testing. Synthetic chargers are not live evidence.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { installDeterministicBasemap } = require('./qa_browser_harness');
const base = (process.argv[2] || 'http://127.0.0.1:8773').replace(/\/$/, '');
const output = process.env.VOLTA_QA_PIN_OUT;
const source = 'https://example.com/charger/mobile';
const rows = [0, 1, 2].map(index => ({
  id: `mobile-pin-${index}`, name: `Mobile pin charger ${index}`,
  lat: 40.4168 + index * 0.01, lon: -3.7038 + index * 0.01,
  address: 'Calle de la prueba 12, Madrid, Comunidad de Madrid, Spain', city: 'Madrid',
  lead: true, price_min: null, max_power_kw: 7.4,
  connectors: [{ label: 'Type 2', count: 2, power_kw: 7.4 }],
  reliability: 'official', status: 'UNKNOWN',
  verification: { location_verified: true, price_verified: false, live_verified: false, level: 'official' },
  decision: { level: 'verify', label: 'Verify first' }, source: 'QA source', source_url: source,
  primary_source: { name: 'QA source', url: source, kind: 'station_page', quality: 'exact' },
}));
const data = {
  stations: [], leads: rows,
  cache: { live_supported: false, fresh_live_authority: false },
  meta: { coverage: { mode: 'directory' } },
};
const results = [];
if (output) fs.mkdirSync(output, { recursive: true });

function save() {
  if (output) fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify({ results }, null, 2));
}

async function captureMap(page) {
  const manifest = await (await page.request.get(`${base}/.vite/manifest.json`)).json();
  const chunk = Object.values(manifest).map(entry => entry.file)
    .find(file => /^assets\/maplibre-(?!gl-worker-)[^/]+\.js$/.test(file));
  assert(chunk, 'The real production map chunk must exist');
  await page.evaluate(async file => {
    const module = await import(new URL(file, location.origin).href);
    const MapClass = Object.values(module).find(value => typeof value === 'function'
      && typeof value.prototype?.getSource === 'function' && typeof value.prototype?.getCanvas === 'function');
    const addSource = MapClass.prototype.addSource;
    MapClass.prototype.addSource = function (...args) {
      window.__pinMap = this;
      return addSource.apply(this, args);
    };
  }, `/${chunk}`);
}

async function check(browser, height, theme) {
  const context = await browser.newContext({
    viewport: { width: 390, height }, isMobile: true, hasTouch: true, deviceScaleFactor: 2,
    reducedMotion: 'reduce', serviceWorkers: 'block',
  });
  try {
    await context.addInitScript(theme => {
      localStorage.setItem('volta-theme', theme);
      Object.defineProperty(navigator, 'geolocation', {
        configurable: true, value: { getCurrentPosition() {} },
      });
      // Observe real trusted pointer clicks without navigating to an external provider.
      window.__pinClicks = [];
      document.addEventListener('click', event => {
        const anchor = event.target.closest('.volta-popup a[href]');
        if (anchor) {
          window.__pinClicks.push({ href: anchor.href, trusted: event.isTrusted });
          event.preventDefault();
        }
      }, true);
    }, theme);
    const page = await context.newPage();
    page.setDefaultTimeout(12000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await installDeterministicBasemap(page);
    let release;
    const ready = new Promise(resolve => { release = resolve; });
    await page.route('**/api/**', async route => {
      await ready;
      const url = new URL(route.request().url());
      return route.fulfill({ json: url.pathname === '/api/chargers' ? data : {
        results: [{ name: 'Madrid', label: 'Madrid, Spain', lat: 40.4168, lon: -3.7038 }],
      } });
    });
    await page.goto(base, { waitUntil: 'domcontentloaded' });
    await page.bringToFront();
    await captureMap(page);
    release();
    await page.waitForFunction(() => window.__pinMap?.isStyleLoaded()
      && window.__pinMap.getLayer('clusters')
      && window.__pinMap.queryRenderedFeatures({ layers: ['clusters'] }).length > 0);
    const cluster = await page.evaluate(() => {
      const map = window.__pinMap;
      const feature = map.queryRenderedFeatures({ layers: ['clusters'] })[0];
      const point = map.project(feature.geometry.coordinates);
      const rect = map.getCanvas().getBoundingClientRect();
      return { x: rect.x + point.x, y: rect.y + point.y, zoom: map.getZoom() };
    });
    await page.touchscreen.tap(cluster.x, cluster.y);
    await page.waitForFunction(zoom => window.__pinMap.getZoom() > zoom + 1
      && !window.__pinMap.isMoving(), cluster.zoom);
    await page.waitForFunction(() => window.__pinMap.queryRenderedFeatures({ layers: ['station-dot'] }).length > 0);
    const pin = await page.evaluate(() => {
      const map = window.__pinMap;
      const feature = map.queryRenderedFeatures({ layers: ['station-dot'] })[0];
      const point = map.project(feature.geometry.coordinates);
      const rect = map.getCanvas().getBoundingClientRect();
      return { x: rect.x + point.x, y: rect.y + point.y, id: feature.properties.id };
    });
    await page.touchscreen.tap(pin.x, pin.y);
    const popup = page.locator('.volta-popup');
    await popup.waitFor();
    await page.waitForFunction(() => !window.__pinMap.isMoving());
    await page.waitForTimeout(350);
    const metrics = await page.evaluate(() => {
      const root = document.querySelector('.volta-popup');
      const actions = [...root.querySelectorAll('a[href]')].map(anchor => {
        const rect = anchor.getBoundingClientRect();
        const points = [[.5, .5], [.15, .2], [.85, .2], [.15, .8], [.85, .8]].map(([x, y]) => {
          const px = rect.x + rect.width * x, py = rect.y + rect.height * y;
          const hit = document.elementFromPoint(px, py);
          return { x: px, y: py, hit: hit?.outerHTML.slice(0, 260), ok: hit === anchor || anchor.contains(hit) };
        });
        return {
          label: anchor.textContent, href: anchor.href, rect: rect.toJSON(), points,
          withinViewport: rect.left >= 0 && rect.right <= 390 && rect.top >= 0 && rect.bottom <= innerHeight,
        };
      });
      const ancestors = [];
      for (let element = root; element; element = element.parentElement) {
        const css = getComputedStyle(element);
        ancestors.push({
          className: element.className, overflow: css.overflow, zIndex: css.zIndex,
          rect: element.getBoundingClientRect().toJSON(),
        });
      }
      const panel = document.querySelector('.discovery-panel');
      const map = document.querySelector('.discovery-map');
      return {
        actions, ancestors, panelRect: panel.getBoundingClientRect().toJSON(), mapRect: map.getBoundingClientRect().toJSON(),
        mapAriaHidden: map.getAttribute('aria-hidden'), panelAccessible: !panel.closest('[aria-hidden="true"]'),
        touch: navigator.maxTouchPoints, coarse: matchMedia('(pointer:coarse)').matches, hidden: document.hidden,
        notLive: root.textContent.includes('Not live'), unknown: root.textContent.includes('Unknown'),
        viewport: { inner: innerWidth, client: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth },
      };
    });
    const result = { height, theme, pin, cluster, metrics };
    results.push(result);
    save();
    if (output) await page.screenshot({ path: path.join(output, `${height}-${theme}-popup.png`) });
    assert(metrics.touch > 0 && metrics.coarse && !metrics.hidden);
    assert(metrics.notLive && metrics.unknown, 'Opening a pin must retain non-live and unknown-price semantics');
    assert.equal(metrics.mapAriaHidden, 'true');
    assert(metrics.panelAccessible, 'The list and search must remain the accessible station path');
    assert.deepEqual(metrics.viewport, { inner: 390, client: 390, scroll: 390 });
    assert.equal(metrics.actions.length, 2);
    assert(metrics.actions.some(action => action.href === source));
    assert(metrics.actions.some(action => action.label.includes('Google Maps')));
    assert(metrics.actions.every(action => action.withinViewport && action.points.every(point => point.ok)),
      'Source/Maps actions must be visible and pointer-reachable, not merely present in the DOM');
    for (const anchor of await popup.locator('a[href]').all()) await anchor.tap();
    result.clicks = await page.evaluate(() => window.__pinClicks);
    assert.equal(result.clicks.length, 2);
    assert(result.clicks.every(event => event.trusted));
    await popup.locator('.maplibregl-popup-close-button').tap();
    await popup.waitFor({ state: 'detached' });
    const search = page.getByRole('combobox');
    await search.tap();
    await search.fill('Madrid');
    await page.getByRole('option').first().waitFor();
    await search.press('ArrowDown');
    await search.press('Enter');
    const fullList = page.getByRole('button', { name: 'Show full charger list', exact: true });
    if (await fullList.isVisible()) await fullList.tap();
    const card = page.locator('[data-station]').first();
    await card.waitFor();
    const cardSource = card.locator('a[data-station-source]');
    await cardSource.scrollIntoViewIfNeeded();
    assert(await cardSource.isVisible());
    await search.focus();
    await page.keyboard.press('Tab');
    result.keyboardInPanel = await page.evaluate(() => Boolean(document.activeElement.closest('.discovery-panel')));
    assert(result.keyboardInPanel, 'Closing the map must retain the panel keyboard path');
    assert.deepEqual(errors, []);
    save();
  } finally {
    await context.close();
  }
}

(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.VOLTA_QA_CHROME
    ? { executablePath: process.env.VOLTA_QA_CHROME } : {}) });
  try {
    for (const height of [844, 1000]) for (const theme of ['light', 'dark']) await check(browser, height, theme);
    console.log(JSON.stringify({ ok: true, cases: results.length, results }, null, 2));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
