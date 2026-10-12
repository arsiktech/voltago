#!/usr/bin/env node
"use strict";

// Interaction/material contract: no live-source dependency. API replies model an
// explicitly stale, empty result so glass can never be mistaken for route authority.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const { installDeterministicBasemap, openFilters, closeFilters } = require('./qa_browser_harness');
const baseUrl = process.argv[2] || 'http://127.0.0.1:8773';
const output = process.env.VOLTA_GLASS_EVIDENCE || '/tmp/volta-liquid-glass-evidence';

(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.VOLTA_QA_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    headless: true,
  });
  const results = [];
  try {
    fs.mkdirSync(output, { recursive: true });
    for (const theme of ['light', 'dark']) {
      for (const width of [390, 1440]) {
        const page = await browser.newPage({ viewport: { width, height: 900 }, serviceWorkers: 'block' });
        page.setDefaultTimeout(12000);
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.addInitScript(theme => localStorage.setItem('volta-theme', theme), theme);
        await installDeterministicBasemap(page);
        await page.route('**/api/chargers**', route => route.fulfill({
          json: { stations: [], cache: { stale: true }, coverage: { mode: 'degraded' } },
        }));
        await page.goto(baseUrl);
        await page.evaluate(theme => document.documentElement.dataset.theme = theme, theme);
        const filters = page.getByRole('group', { name: 'Filters', exact: true });
        const trigger = filters.getByRole('button', { name: /^All filters(?:\s|$)/ });
        const quick = filters.locator('button.chip').filter({ visible: true });
        assert.equal(await quick.count(), 2, 'Two quick price choices before opening modal');
        const dialog = await openFilters(page);
        const chips = dialog.locator('button.chip[aria-labelledby][aria-pressed]');
        await chips.first().waitFor();
        const geometry = () => chips.evaluateAll(items => items.map(item => ({ width: item.getBoundingClientRect().width, x: item.offsetLeft, height: item.getBoundingClientRect().height })));
        const before = await geometry();
        assert.equal(before.length, 6);
        const noLimit = dialog.getByRole("button", { name: "No limit", exact: true });
        assert.equal(await noLimit.getAttribute("aria-pressed"), "true");
        assert.equal(await dialog.getByRole("slider", { name: "Maximum charging price", exact: true }).inputValue(), "101");
        assert(before.every(rect => rect.height >= 44), '44px filter targets');
        const checked = dialog.getByRole('button', { name: 'Source checked', exact: true });
        const defaults = await chips.evaluateAll(items => items.map(item => ({ name: document.getElementById(item.getAttribute('aria-labelledby'))?.textContent, pressed: item.getAttribute('aria-pressed'), description: document.getElementById(item.getAttribute('aria-describedby'))?.textContent })));
        assert(defaults.every(item => item.pressed === (item.name === 'Leads (Verify first)' ? 'true' : 'false') && item.description), 'All six boolean defaults and safety descriptions are exposed');
        assert.equal(await checked.getAttribute('aria-pressed'), 'false');
        await checked.click();
        assert.equal(await checked.getAttribute('aria-pressed'), 'true');
        await checked.click();
        assert.equal(await checked.getAttribute('aria-pressed'), 'false');
        // Space/Enter must toggle synchronously and keep keyboard focus.
        await checked.focus();
        await page.keyboard.press('Space');
        assert.equal(await checked.getAttribute('aria-pressed'), 'true');
        await page.keyboard.press('Enter');
        assert.equal(await checked.getAttribute('aria-pressed'), 'false');
        assert(await checked.evaluate(el => el === document.activeElement));
        const after = await geometry();
        assert.deepEqual(after, before, 'Toggling must not move adjacent controls');
        // Native modal semantics must trap both Tab directions and return focus on Escape.
        await dialog.getByRole('button', { name: 'Done', exact: true }).focus();
        await page.keyboard.press('Tab');
        // Chromium may visit browser chrome (document.body) at the forward boundary.
        // It must never focus an inert background control and must re-enter the modal.
        if (await page.evaluate(() => document.activeElement === document.body)) await page.keyboard.press('Tab');
        assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('aria-label')), 'Close filters');
        await page.keyboard.press('Shift+Tab');
        assert.equal(await page.evaluate(() => document.activeElement?.textContent?.trim()), 'Done');
        for (let i = 0; i < 14; i += 1) {
          await page.keyboard.press('Tab');
          if (await page.evaluate(() => document.activeElement === document.body)) await page.keyboard.press('Tab');
          assert(await page.evaluate(() => Boolean(document.activeElement?.closest('dialog[open]'))), 'Background app controls must remain inert while the modal is open');
        }
        await page.keyboard.press('Escape');
        await dialog.waitFor({ state: 'hidden' });
        assert(await trigger.evaluate(el => el === document.activeElement), 'Escape returns focus to All filters');
        await page.waitForFunction(() => document.querySelector('[aria-haspopup=\"dialog\"]:not([data-price-filter])')?.getAttribute('aria-expanded') === 'false', null, { timeout: 1000 });
        assert.equal(await trigger.getAttribute('aria-expanded'), 'false');
        await openFilters(page);
        await checked.scrollIntoViewIfNeeded();
        assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'No horizontal page overflow');
        const glass = page.locator('.glass').first();
        assert.notEqual(await glass.evaluate(el => getComputedStyle(el).backdropFilter), 'none');
        const cdp = await page.context().newCDPSession(page);
        await cdp.send('Emulation.setEmulatedMedia', { features: [
          { name: 'prefers-reduced-motion', value: 'reduce' },
          { name: 'prefers-reduced-transparency', value: 'reduce' },
        ] });
        assert(await page.evaluate(() => matchMedia('(prefers-reduced-transparency: reduce)').matches));
        assert.equal(await glass.evaluate(el => getComputedStyle(el).backdropFilter), 'none');
        assert.equal(await glass.evaluate(el => getComputedStyle(el).backgroundImage), 'none');
        const box = await checked.boundingBox();
        await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
        await page.mouse.down();
        assert.equal(await checked.evaluate(el => getComputedStyle(el).scale), '1');
        await page.mouse.up();
        await cdp.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-contrast', value: 'more' }] });
        assert.equal(await glass.evaluate(el => getComputedStyle(el).backdropFilter), 'none');
        await cdp.send('Emulation.setEmulatedMedia', { features: [{ name: 'forced-colors', value: 'active' }] });
        assert.equal(await glass.evaluate(el => getComputedStyle(el).backdropFilter), 'none');
        await cdp.send('Emulation.setEmulatedMedia', { features: [] });
        await closeFilters(page);
        assert(await trigger.evaluate(el => el === document.activeElement), 'Done returns focus to All filters');
        await page.getByText('Showing cached data', { exact: true }).waitFor();
        assert.equal(await page.locator('.pill-go').count(), 0);
        await page.screenshot({ path: path.join(output, `${theme}-${width}.png`) });
        assert.deepEqual(errors, []);
        results.push({ theme, width, stableTargets: true, keyboard: true, modalFocusTrap: true, escapeReturnsFocus: true, reducedMotion: true, reducedTransparency: true, forcedColors: true, highContrast: true, staleNoGo: true });
        await page.close();
      }
    }
    console.log(JSON.stringify({ ok: true, results, output }, null, 2));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
