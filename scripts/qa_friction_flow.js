#!/usr/bin/env node
/* Friction-reduction contract for the Volta React SPA.
 *
 * Protects two click-saving behaviors:
 *  - moving/zooming the map into an area auto-applies the same bbox search that the
 *    "Search this area" pill performs after a short pause;
 *  - when live route-ready cards exist, the FlowHint exposes one predictable external
 *    handoff to the closest route-ready charger: Open closest in Maps. Provider
 *    Start/check remains a card-level action and must not replace this route CTA.
 *  - after locating, a manual pan owns the search origin across unrelated UI updates.
 */
const { chromium } = require("playwright");
const { installDeterministicBasemap } = require("./qa_browser_harness");

const baseUrl = (process.argv[2] || process.env.VOLTA_QA_BASE_URL || "http://127.0.0.1:8773").replace(/\/$/, "");
const chromePath = process.env.VOLTA_QA_CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

function isMapUrl(url) {
  return /google\.[^/]+\/maps|maps\.apple\.com/i.test(url || "");
}

function safeExternal(link) {
  const rel = String(link?.rel || "").toLowerCase().split(/\s+/);
  return link?.target === "_blank" && rel.includes("noopener") && rel.includes("noreferrer");
}

function compact(text) {
  return String(text || "").replace(/\s+/g, " ").trim();
}

async function waitSettledOverview(page) {
  await page.waitForSelector("[data-station] article.card", { timeout: 25000 });
  await page.waitForFunction(
    () => {
      const status = document.querySelector(".live-dot")?.parentElement?.textContent || "";
      return /Saved chargers · not live/.test(status) && !/Refreshing/.test(status);
    },
    null,
    { timeout: 30000 },
  ).catch(() => {});
}

async function panExposedMap(page) {
  const drag = await page.evaluate(() => {
    const canvas = document.querySelector(".maplibregl-canvas");
    const map = canvas.getBoundingClientRect();
    const panel = document.querySelector("section").getBoundingClientRect();
    let left = Math.max(0, map.left);
    const right = Math.min(innerWidth, map.right);
    const top = Math.max(0, map.top);
    let bottom = Math.min(innerHeight, map.bottom);
    // Mobile leaves the map above its sheet; desktop leaves it right of the panel.
    if (panel.top > top + 80) bottom = Math.min(bottom, panel.top);
    else left = Math.max(left, panel.right);
    if (right - left < 120 || bottom - top < 80) throw new Error("No exposed map area for a real pointer drag");
    const from = { x: left + (right - left) * 0.25, y: top + (bottom - top) * 0.55 };
    const to = { x: left + (right - left) * 0.8, y: from.y };
    if (document.elementFromPoint(from.x, from.y) !== canvas || document.elementFromPoint(to.x, to.y) !== canvas) {
      throw new Error("Map drag coordinates intersect an overlay instead of the canvas");
    }
    return { from, to };
  });
  await page.mouse.move(drag.from.x, drag.from.y);
  await page.mouse.down();
  await page.mouse.move(drag.to.x, drag.to.y, { steps: 12 });
  await page.mouse.up();
  return drag;
}

async function main() {
  const browser = await chromium.launch({ headless: true, executablePath: chromePath });
  const logs = [];
  const failures = [];

  const desktop = await browser.newPage({ viewport: { width: 1280, height: 900 }, reducedMotion: "reduce" });
  await installDeterministicBasemap(desktop);
  desktop.on("console", (msg) => logs.push(`${msg.type()}: ${msg.text()}`));
  desktop.on("pageerror", (err) => logs.push(`pageerror: ${String(err.stack || err.message).split("\n")[0]}`));

  await desktop.goto(`${baseUrl}/`, { waitUntil: "domcontentloaded", timeout: 20000 });
  await waitSettledOverview(desktop);
  await desktop.waitForSelector(".maplibregl-canvas", { timeout: 15000 });
  await desktop.waitForTimeout(1000);

  const autoAreaResponse = desktop.waitForResponse(
    (response) => {
      if (response.status() !== 200 || !response.url().includes("/api/chargers")) return false;
      const url = new URL(response.url());
      return url.searchParams.has("bbox") && url.searchParams.get("overview") !== "true";
    },
    { timeout: 30000 },
  );

  for (let i = 0; i < 5; i += 1) await desktop.locator(".maplibregl-ctrl-zoom-in").click();
  // A real drag supplies a user-originated viewport event; derive its coordinates
  // from the exposed canvas so a panel layout change cannot turn this into panel scrolling.
  const desktopDrag = await panExposedMap(desktop);

  let autoAreaUrl = "";
  try {
    const response = await autoAreaResponse;
    autoAreaUrl = response.url();
  } catch (err) {
    failures.push("map zoom/pan did not auto-apply a bbox search without clicking Search this area");
  }

  const afterAuto = await desktop.evaluate(() => ({
    h1: document.querySelector("section h1")?.textContent?.replace(/\s+/g, " ").trim() || "",
    mode: document.querySelector("section h1")?.parentElement?.firstElementChild?.textContent?.replace(/\s+/g, " ").trim() || "",
    areaButtonVisible: [...document.querySelectorAll("button")].some((b) => /Search this area/.test(b.textContent || "")),
  }));

  const mobile = await browser.newPage({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    isMobile: true,
    reducedMotion: "reduce",
  });
  await installDeterministicBasemap(mobile);
  await mobile.context().grantPermissions(["geolocation"], { origin: baseUrl });
  await mobile.context().setGeolocation({ latitude: 39.47, longitude: -0.377 });
  mobile.on("console", (msg) => logs.push(`${msg.type()}: ${msg.text()}`));
  mobile.on("pageerror", (err) => logs.push(`pageerror: ${String(err.stack || err.message).split("\n")[0]}`));

  await mobile.goto(`${baseUrl}/`, { waitUntil: "domcontentloaded", timeout: 20000 });
  await waitSettledOverview(mobile);
  const locatedResponse = mobile.waitForResponse(
    (response) => {
      if (response.status() !== 200 || !response.url().includes("/api/chargers")) return false;
      const url = new URL(response.url());
      return url.searchParams.has("lat") && url.searchParams.has("lon") && url.searchParams.get("overview") !== "true";
    },
    { timeout: 45000 },
  );
  await mobile.click('button[aria-label="Use my location"]');
  await locatedResponse;
  await mobile.waitForFunction(
    () => {
      const status = document.querySelector(".live-dot")?.parentElement?.textContent || "";
      return /Saved chargers · not live/.test(status) && !/Refreshing/.test(status);
    },
    null,
    { timeout: 30000 },
  ).catch(() => {});

  const hint = await mobile.evaluate(() => {
    const statuses = [...document.querySelectorAll('[role="status"]')].map((el) => ({
      text: (el.textContent || "").replace(/\s+/g, " ").trim(),
      href: el.querySelector('a')?.href || "",
      target: el.querySelector('a')?.target || "",
      rel: el.querySelector('a')?.rel || "",
      action: (el.querySelector('a')?.textContent || "").replace(/\s+/g, " ").trim(),
    }));
    return statuses.find((row) => /Go now option|No live bay available nearby/.test(row.text)) || null;
  });

  const routeReadyCards = await mobile
    .locator("[data-station] article.card .pill-go")
    .count()
    .catch(() => 0);

  if (routeReadyCards > 0) {
    if (!hint || !/Go now option/.test(hint.text)) failures.push("route-ready live state lacks a Go now FlowHint");
    const action = hint?.action || "";
    if (action !== "Open closest in Maps") {
      failures.push(`Go now FlowHint action must be "Open closest in Maps": ${action || "(none)"}`);
    }
    if (!isMapUrl(hint?.href)) failures.push(`Open closest in Maps href is not a Maps URL: ${hint?.href || "(none)"}`);
    if (!safeExternal(hint)) failures.push("direct best handoff lacks target=_blank rel=noopener noreferrer");
  } else if (hint && hint.action === "Open closest in Maps") {
    failures.push("direct best handoff is visible without a route-ready card");
  }

  // Leave the located bbox, then make an unrelated UI update. The old device result
  // must not reclaim the origin after the user has deliberately moved the map.
  for (let i = 0; i < 7; i += 1) await mobile.locator(".maplibregl-ctrl-zoom-out").click();
  await mobile.waitForTimeout(500);
  const manualRequest = mobile.waitForRequest((request) => {
    const url = new URL(request.url());
    const latValue = url.searchParams.get("lat");
    const lonValue = url.searchParams.get("lon");
    const lat = latValue?.trim() ? Number(latValue) : NaN;
    const lon = lonValue?.trim() ? Number(lonValue) : NaN;
    return url.pathname === "/api/chargers" && url.searchParams.has("bbox")
      && url.searchParams.get("overview") !== "true"
      && Number.isFinite(lat) && Number.isFinite(lon)
      && (Math.abs(lat - 39.47) > 0.001 || Math.abs(lon + 0.377) > 0.001);
  }, { timeout: 15000 });
  const mobileDrag = await panExposedMap(mobile);
  const manualAreaUrl = (await manualRequest).url();
  await mobile.getByText("Near map center", { exact: true }).waitFor({ timeout: 5000 });
  await mobile.getByRole("button", { name: /Switch to .* mode/ }).click();
  await mobile.getByText("Near map center", { exact: true }).waitFor({ timeout: 5000 });

  await browser.close();

  const unexpectedErrors = logs.filter(
    (line) =>
      /^error:|^pageerror:/.test(line) &&
      !/maplibre-|Invalid LngLat|Attempting to run\(\)|fromInvProjectionMatrix|getCameraFrustum/i.test(line),
  );
  if (unexpectedErrors.length) failures.push(`browser console contains errors: ${unexpectedErrors.join(" | ")}`);

  console.log(JSON.stringify({
    ok: failures.length === 0,
    baseUrl,
    autoAreaUrl,
    desktopDrag,
    afterAuto,
    routeReadyCards,
    hint,
    mobileDrag,
    manualAreaUrl,
    logs,
    failures,
  }, null, 2));
  process.exit(failures.length ? 1 : 0);
}

main().catch((err) => {
  console.error(JSON.stringify({ ok: false, baseUrl, error: `${err.name}: ${err.message}` }, null, 2));
  process.exit(1);
});
