#!/usr/bin/env node
"use strict";

// Real clipboard success plus denied/unavailable clipboard recovery. API/map
// fixtures isolate the copy action from provider and geolocation availability.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");
const { unexpectedBrowserErrors, isChromiumReadPixelsDiagnostic } = require("./qa_browser_harness");

const baseUrl = (process.argv[2] || process.env.VOLTA_QA_BASE_URL || "http://127.0.0.1:8773").replace(/\/$/, "");
const chromePath = process.env.VOLTA_QA_CHROME
  || (process.platform === "darwin" ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" : undefined);
const expectedPrompt = fs.readFileSync(path.join(__dirname, "../contracts/agent_connection_prompt.txt"), "utf8").trim();
const origin = new URL(baseUrl).origin;
const checks = [];
const logs = [];

async function fixturePage(browser, viewport, theme = "light", clipboard = "native") {
  const context = await browser.newContext({ viewport, reducedMotion: "reduce", serviceWorkers: "block" });
  await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin });
  const page = await context.newPage();
  page.on("console", message => logs.push(`${message.type()}: ${message.text()}`));
  page.on("pageerror", error => logs.push(`pageerror: ${error.message}`));
  await page.addInitScript(({ theme, clipboard }) => {
    localStorage.setItem("volta-theme", theme);
    Object.defineProperty(navigator, "geolocation", { configurable: true, value: {
      getCurrentPosition(_success, error) { error({ code: 1 }); },
    } });
    if (clipboard !== "native") Object.defineProperty(navigator, "clipboard", { configurable: true,
      value: clipboard === "missing" ? undefined : { writeText: async () => { throw new DOMException("Fixture denied clipboard", "NotAllowedError"); } },
    });
  }, { theme, clipboard });
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.hostname === "tiles.openfreemap.org") return route.fulfill({ json: { version: 8, sources: {}, layers: [] } });
    if (url.origin !== origin) return route.abort();
    if (url.pathname === "/api/chargers") return route.fulfill({ json: {
      stations: [], leads: [], cache: { stale: false },
      meta: { mode: "overview", coverage: { mode: "overview", live_route_ready: 0, lead_count: 0 } },
    } });
    return route.continue();
  });
  await page.goto(`${baseUrl}/`, { waitUntil: "domcontentloaded" });
  await page.locator("button.agent-connect").waitFor();
  assert((await page.title()).includes("Volta Go"));
  assert.equal(new URL(page.url()).origin, origin);
  assert(await page.getByRole("combobox").isVisible());
  assert.equal(await page.locator("vite-error-overlay, nextjs-portal").count(), 0);
  return { context, page };
}

async function main() {
  const browser = await chromium.launch({ headless: true, ...(chromePath ? { executablePath: chromePath } : {}) });
  try {
    for (const { name, width, height, theme } of [
      { name: "desktop-light", width: 1280, height: 900, theme: "light" },
      { name: "desktop-dark", width: 1280, height: 900, theme: "dark" },
      { name: "mobile", width: 390, height: 844, theme: "light" },
    ]) {
      const { context, page } = await fixturePage(browser, { width, height }, theme);
      const button = page.locator("button.agent-connect");
      assert((await button.innerText()).includes("Paste into your AI chat"));
      assert((await button.boundingBox()).height >= 44);
      await button.click();
      await page.getByRole("button", { name: "Prompt copied Paste into your AI chat", exact: true }).waitFor();
      assert.equal(await page.evaluate(() => navigator.clipboard.readText()), expectedPrompt);
      assert.equal(await page.locator("dialog[open]").count(), 0);
      assert.equal(await page.locator("textarea").count(), 0);
      await page.evaluate(() => navigator.clipboard.writeText("Clipboard retry fixture"));
      await button.click();
      await page.waitForFunction(() => document.querySelector(".agent-connect")?.textContent.includes("Prompt copied"));
      assert.equal(await page.evaluate(() => navigator.clipboard.readText()), expectedPrompt);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      if (process.env.VOLTA_QA_AGENT_OUT) {
        fs.mkdirSync(process.env.VOLTA_QA_AGENT_OUT, { recursive: true });
        await page.screenshot({ path: path.join(process.env.VOLTA_QA_AGENT_OUT, `agent-prompt-${name}.png`) });
      }
      checks.push(`one-click full prompt, repeat copy, no popup and stable layout: ${name}`);
      await context.close();
    }
    for (const clipboard of ["denied", "missing"]) {
      const { context, page } = await fixturePage(browser, { width: 390, height: 844 }, "light", clipboard);
      await page.locator("button.agent-connect").click();
      const manual = page.getByRole("textbox", { name: "AI setup prompt", exact: true });
      await manual.waitFor();
      assert.equal(await manual.inputValue(), expectedPrompt);
      await manual.focus();
      assert(await manual.evaluate(element => element.selectionStart === 0 && element.selectionEnd === element.value.length));
      assert.equal(await page.locator("dialog[open]").count(), 0);
      assert(!(await page.locator("button.agent-connect").innerText()).includes("Prompt copied"));
      await page.evaluate(() => Object.defineProperty(navigator, "clipboard", { configurable: true, value: {
        writeText: async text => { window.__qaCopiedPrompt = text; },
      } }));
      await page.locator("button.agent-connect").click();
      await page.getByRole("button", { name: "Prompt copied Paste into your AI chat", exact: true }).waitFor();
      assert.equal(await page.evaluate(() => window.__qaCopiedPrompt), expectedPrompt);
      assert.equal(await manual.count(), 0);
      checks.push(`complete selectable prompt and successful retry after clipboard ${clipboard}`);
      await context.close();
    }
    const errors = unexpectedBrowserErrors(logs.filter(line => !isChromiumReadPixelsDiagnostic(line)), {
      includeWarnings: true, allowServiceWorkerRegistrationBlocked: true,
    });
    assert.deepEqual(errors, [], "Unexpected app warnings or errors");
    console.log(JSON.stringify({ ok: true, baseUrl, checks }, null, 2));
  } finally { await browser.close(); }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
