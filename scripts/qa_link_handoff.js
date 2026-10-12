#!/usr/bin/env node
/* External link handoff contract for Volta (React SPA).
 *
 * Verifies that station cards expose map links as safe external handoffs
 * (target=_blank + rel noopener noreferrer), expose card-level provider
 * start/check links only when a public start page exists, keep source evidence
 * in Details, and keep the Volta app tab intact after an external handoff.
 * The clicked destination receives a local document fixture: this checks browser
 * handoff behavior, not the external site's availability or response time.
 */
const { chromium } = require("playwright");

const baseUrl = (process.argv[2] || process.env.VOLTA_QA_BASE_URL || "http://127.0.0.1:8773").replace(/\/$/, "");
const chromePath = process.env.VOLTA_QA_CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

function isHttpUrl(url) {
  return /^https?:\/\//i.test(url || "");
}

function isMapUrl(url) {
  return /google\.[^/]+\/maps|maps\.apple\.com/i.test(url || "");
}

async function collect(page) {
  return page.evaluate(() => {
    const clean = (text) => String(text || "").replace(/\s+/g, " ").trim();
    const relTokens = (value) => String(value || "").toLowerCase().split(/\s+/).filter(Boolean);
    const linkInfo = (a) => (a ? {
      href: a.href || "",
      target: a.getAttribute("target") || "",
      rel: relTokens(a.getAttribute("rel")),
      label: clean(a.textContent),
    } : null);
    const cards = [...document.querySelectorAll('[data-station] article.card')].map(card => {
      const anchors = [...card.querySelectorAll("a")];
      const maps = anchors.find(a => clean(a.textContent) === "Open in Maps") || null;
      const start = anchors.find(a => /^Start\/check/.test(clean(a.textContent))) || null;
      const pill = card.querySelector(".pill");
      const decisionMatch = /(?:^|\s)pill-(go|check|verify)(?:\s|$)/.exec(pill?.className || "");
      const detailsButton = [...card.querySelectorAll("button")].find(b => /details/i.test(b.textContent || ""));
      return {
        name: card.querySelector("h3")?.textContent.trim() || "",
        lead: [...card.querySelectorAll(".badge")].some(b => clean(b.textContent) === "Not live"),
        decision: decisionMatch ? decisionMatch[1] : "",
        decisionLabel: clean(pill?.textContent),
        maps: linkInfo(maps),
        start: linkInfo(start),
        detailsExpanded: detailsButton?.getAttribute("aria-expanded") === "true",
        // Provenance links revealed by Details: claim "view source" + Public sources list.
        detailLinks: anchors.filter(a => a !== maps && a !== start).map(linkInfo),
      };
    });
    return {
      cards,
      count: cards.length,
      liveStatus: clean(document.querySelector(".live-dot")?.parentElement?.textContent),
      countsBadge: [...document.querySelectorAll(".badge")].map(el => clean(el.textContent))
        .find(text => /\d+\s*live\s*·\s*\d+\s*leads/i.test(text)) || "",
      honestyFooter: [...document.querySelectorAll("details summary")].some(el => /data honesty/i.test(el.textContent || "")),
      overflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    };
  });
}

function hasSafeExternalAttrs(link) {
  return link.target === "_blank" && link.rel.includes("noopener") && link.rel.includes("noreferrer");
}

async function main() {
  const browser = await chromium.launch({ headless: true, executablePath: chromePath });
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    isMobile: true,
  });
  const page = await context.newPage();
  const logs = [];
  page.on("console", msg => logs.push(`${msg.type()}: ${msg.text()}`));
  page.on("pageerror", err => logs.push(`pageerror: ${err.message}`));

  await page.goto(`${baseUrl}/`, { waitUntil: "domcontentloaded", timeout: 20000 });
  // Default open = national overview; virtualized list renders lead cards.
  await page.waitForSelector('[data-station] article.card', { timeout: 30000 });

  // Expand Details on the first card so provenance (view source / Public sources) links mount.
  const firstCard = page.locator('[data-station] article.card').first();
  const anchorsBeforeDetails = await firstCard.locator("a").count();
  await firstCard.getByRole("button", { name: "Details" }).click({ timeout: 5000 });
  await page.waitForFunction((before) => {
    const card = document.querySelector('[data-station] article.card');
    return card && card.querySelectorAll("a").length > before;
  }, anchorsBeforeDetails, { timeout: 8000 }).catch(() => {});
  await page.waitForTimeout(350); // let the height animation settle

  const beforeClick = await collect(page);
  const appUrlBefore = page.url();
  const tabCountBefore = context.pages().length;

  // Click the first card's public start action if present, otherwise Maps. Directory/store
  // source pages live in Details and must not masquerade as card-level start handoffs.
  const startCount = await firstCard.locator('a:text-matches("^Start/check")').count();
  const clickedAction = startCount ? "start" : "maps";
  const handoffLink = startCount
    ? firstCard.locator('a:text-matches("^Start/check")').first()
    : firstCard.getByRole("link", { name: /Open .* in Maps|Open in Maps/i });
  const handoffTargetUrl = await handoffLink.evaluate(link => link.href);
  const expectedLink = beforeClick.cards[0]?.[clickedAction];
  if (!expectedLink || handoffTargetUrl !== expectedLink.href || !isHttpUrl(handoffTargetUrl)
    || new URL(handoffTargetUrl).origin === new URL(baseUrl).origin) {
    throw new Error(`unexpected ${clickedAction} handoff target: ${handoffTargetUrl}`);
  }
  // Popup events wait for the first document response. Fulfill only the exact
  // clicked destination so Google/provider latency cannot turn a valid click into
  // a timeout. Fragments stay in the popup URL but are never sent over HTTP.
  const handoffDocument = new URL(handoffTargetUrl);
  handoffDocument.hash = "";
  const interceptedHandoffUrls = [];
  await context.route(url => url.href === handoffDocument.href, async route => {
    const request = route.request();
    if (!request.isNavigationRequest() || request.resourceType() !== "document") {
      await route.continue();
      return;
    }
    interceptedHandoffUrls.push(request.url());
    await route.fulfill({
      status: 200,
      contentType: "text/html",
      body: "<!doctype html><title>Volta handoff fixture</title><p>Local browser handoff fixture.</p>",
    });
  });
  const pagePromise = context.waitForEvent("page", { timeout: 10000 });
  await handoffLink.click({ timeout: 5000 });
  const externalPage = await pagePromise;
  const tabCountDuring = context.pages().length;
  await externalPage.waitForURL(
    url => url.toString() === handoffTargetUrl,
    { timeout: 15000 },
  ).catch(() => {});
  await externalPage.waitForLoadState("domcontentloaded", { timeout: 15000 }).catch(() => {});
  const externalPageUrl = externalPage.url();
  await externalPage.close().catch(() => {});
  const appUrlAfter = page.url();
  const tabCountAfter = context.pages().length;
  const appListAlive = (await page.locator("[data-station]").count().catch(() => 0)) > 0;

  await browser.close();

  const failures = [];
  if (!beforeClick.count) failures.push("no station cards rendered");
  const badStart = beforeClick.cards.filter(card => card.start && (!isHttpUrl(card.start.href) || isMapUrl(card.start.href)));
  const unsafeStart = beforeClick.cards.filter(card => card.start && !hasSafeExternalAttrs(card.start));
  const missingMap = beforeClick.cards.filter(card => !card.maps || !isMapUrl(card.maps.href));
  const unsafeMap = beforeClick.cards.filter(card => card.maps && !hasSafeExternalAttrs(card.maps));
  const openOptions = beforeClick.cards[0]?.detailLinks || [];
  const unsafeOptions = openOptions.filter(option => !isHttpUrl(option.href) || !hasSafeExternalAttrs(option));
  const nonGoNavigate = beforeClick.cards.filter(card => card.decision !== "go" && card.maps && card.maps.label === "Navigate");

  if (badStart.length) failures.push(`card start/check links must be non-map HTTP start pages: ${badStart.slice(0, 3).map(card => card.name).join(", ")}`);
  if (unsafeStart.length) failures.push(`start/check links missing target/rel safety: ${unsafeStart.slice(0, 3).map(card => card.name).join(", ")}`);
  if (missingMap.length) failures.push(`cards missing Google/Apple map links: ${missingMap.slice(0, 3).map(card => card.name).join(", ")}`);
  if (unsafeMap.length) failures.push(`map links missing target/rel safety: ${unsafeMap.slice(0, 3).map(card => card.name).join(", ")}`);
  if (!openOptions.length) failures.push("first card Details expose no source provenance links");
  if (unsafeOptions.length) failures.push(`detail provenance links missing HTTP/target/rel safety: ${unsafeOptions.map(option => option.label).join(", ")}`);
  if (nonGoNavigate.length) failures.push(`non-go cards are labeled Navigate: ${nonGoNavigate.slice(0, 3).map(card => card.name).join(", ")}`);
  if (tabCountDuring <= tabCountBefore) failures.push(`${clickedAction} click did not grow the tab count (before=${tabCountBefore}, during=${tabCountDuring})`);
  if (appUrlAfter !== appUrlBefore) failures.push(`clicking ${clickedAction} navigated the Volta app tab away`);
  if (!appListAlive) failures.push(`app tab lost its station list after the ${clickedAction} handoff`);
  if (!isHttpUrl(externalPageUrl) || externalPageUrl.startsWith(baseUrl)) failures.push(`${clickedAction} click did not open an external page: ${externalPageUrl}`);
  if (externalPageUrl !== handoffTargetUrl) failures.push(`${clickedAction} opened a different destination: ${externalPageUrl}`);
  if (interceptedHandoffUrls.length !== 1 || interceptedHandoffUrls[0] !== handoffDocument.href) {
    failures.push(`${clickedAction} did not request the exact handoff document once: ${JSON.stringify(interceptedHandoffUrls)}`);
  }
  if (tabCountAfter !== 1) failures.push(`unexpected tab count after closing source page: ${tabCountAfter}`);
  if (beforeClick.overflowX) failures.push("mobile viewport has horizontal overflow");
  const errors = logs.filter(line => /^error:|^pageerror:/.test(line));
  if (errors.length) failures.push(`browser console contains errors: ${errors.join(" | ")}`);

  console.log(JSON.stringify({
    ok: failures.length === 0,
    baseUrl,
    cards: beforeClick.count,
    firstCard: beforeClick.cards[0] || null,
    clickedAction,
    handoffTargetUrl,
    interceptedHandoffUrls,
    externalResponse: "local fixture; external availability not checked",
    openedExternalUrl: externalPageUrl,
    appUrlBefore,
    appUrlAfter,
    tabCountDuring,
    tabCountAfter,
    liveStatus: beforeClick.liveStatus,
    countsBadge: beforeClick.countsBadge,
    honestyFooter: beforeClick.honestyFooter,
    logs,
    failures,
  }, null, 2));
  process.exit(failures.length ? 1 : 0);
}

main().catch(err => {
  console.error(JSON.stringify({ ok: false, baseUrl, error: `${err.name}: ${err.message}` }, null, 2));
  process.exit(1);
});
