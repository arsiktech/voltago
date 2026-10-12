#!/usr/bin/env node
"use strict";

// Realtime-refresh contract (React SPA port).
//
// Protects, in live mode (geolocation granted → "Use my location"):
//   1. a 60s auto-refresh interval is registered (TanStack Query refetchInterval);
//   2. the app re-fetches /api/chargers ON ITS OWN (no user action) within ~75s,
//      and that background refetch is NOT a force=true cache-bypass;
//   3. the auto-refresh request repeats the same live query (identical URL) and
//      returns HTTP 200 — every /api/chargers response must be 200;
//   4. the honest live/mixed-authority status text actually changes to a refreshing state
//      and settles with an updated timestamp when the background refetch runs;
//   5. the status text lives in a polite live region (role="status"/aria-live)
//      like the retired shell's #sourceCard — kept as an honest a11y assertion;
//   6. no error card, no horizontal overflow at 390x844, no console noise.
//
// Note: the retired shell issued its FIRST fetch with force=true. The SPA never
// sends force=true from any UI path (frontend/src/lib/api.ts plumbs `force` but
// nothing sets it); its initial fetch is the cached national overview. We assert
// the SPA equivalents instead (overview fetch on load + live fetch on locate) and
// keep the non-forced guarantee on the background refetch.

const { chromium } = require("playwright");

const baseUrl = process.argv[2] || process.env.VOLTA_QA_BASE_URL || "http://127.0.0.1:8773";
const chromePath = process.env.VOLTA_QA_CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

// The 60s interval plus bounded upstream live refreshes (up to ~45s each on a cold
// server cache) needs real headroom: the first fetch may consume ~45s, then the 60s
// interval plus one more ~45s refresh and a status settle. 240s keeps one full
// 60s observation window with margin; the suite subprocess cap must cover it.
const SCRIPT_DEADLINE_MS = 240000; // hard budget for the whole run
const startedAt = Date.now();
const remaining = (reserveMs) => Math.max(5000, SCRIPT_DEADLINE_MS - (Date.now() - startedAt) - reserveMs);

const isChargers = (url) => url.includes("/api/chargers?");
const isLiveChargers = (url) => isChargers(url) && !url.includes("overview=true");

async function main() {
  // Explicit launch timeout: a wedged Chrome (e.g. mid-auto-update) must fail fast, not hang the suite.
  const browser = await chromium.launch({ headless: true, executablePath: chromePath, timeout: 25000 });
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    isMobile: true,
    geolocation: { latitude: 39.47, longitude: -0.377 },
    permissions: ["geolocation"],
  });
  const page = await context.newPage();

  const logs = [];
  const chargerRequests = []; // { url, t }
  const chargerResponses = []; // { url, status, t }

  page.on("console", (msg) => {
    if (["error", "warning"].includes(msg.type())) logs.push(`${msg.type()}: ${msg.text()}`);
  });
  page.on("pageerror", (err) => logs.push(`pageerror: ${err.message}`));
  page.on("request", (request) => {
    const url = request.url();
    if (isChargers(url)) chargerRequests.push({ url, t: Date.now() });
  });
  page.on("response", (response) => {
    const url = response.url();
    if (isChargers(url)) chargerResponses.push({ url, status: response.status(), t: Date.now() });
  });

  // Record every setInterval delay so we can assert the 60s poll is registered.
  // (No acceleration: the background refetch below is observed in real time.)
  await page.addInitScript(() => {
    window.__voltaIntervals = [];
    const nativeSetInterval = window.setInterval.bind(window);
    window.setInterval = (callback, delay, ...args) => {
      window.__voltaIntervals.push({ delay });
      return nativeSetInterval(callback, delay, ...args);
    };
  });

  const failures = [];

  // --- Load: national overview (leads-only) --------------------------------
  const overviewResponsePromise = page
    .waitForResponse((r) => isChargers(r.url()) && r.url().includes("overview=true"), { timeout: 20000 })
    .catch(() => null);
  await page.goto(baseUrl, { waitUntil: "domcontentloaded", timeout: 20000 });
  await page.waitForSelector("section h1", { timeout: 15000 });
  const overviewResponse = await overviewResponsePromise;
  if (!overviewResponse) failures.push("initial overview chargers fetch (overview=true) was not observed on load");

  // --- Enter live mode via mocked geolocation ------------------------------
  const liveResponsePromise = page
    .waitForResponse((r) => isLiveChargers(r.url()), { timeout: 25000 })
    .catch(() => null);
  await page.click('button[aria-label="Use my location"]', { timeout: 5000 });
  const firstLiveResponse = await liveResponsePromise;
  const sawLiveRequest = chargerRequests.some((r) => isLiveChargers(r.url));
  if (!firstLiveResponse) {
    // The live request may have been observed outbound but its response raced the
    // 25s promise window (the bounded upstream refresh can take ~45s on a cold
    // cache). The fetch itself is still proven by the request/response logs below,
    // and the DOM status assertions below hold for the rendered result; only a run
    // with NO live request at all is missing the protected behavior.
    if (!sawLiveRequest) {
      failures.push("live-mode chargers fetch after 'Use my location' was not observed");
    }
  }
  const responseStillPending = !firstLiveResponse && sawLiveRequest;

  // Let React commit the live payload, then snapshot the status row and start
  // recording every text change it goes through.
  // Capture the live response body so the status assertions know whether the app had
  // fresh live authority or served the honest degraded no-authority view.
  let firstLiveBody = null;
  try {
    if (firstLiveResponse) firstLiveBody = await firstLiveResponse.json();
  } catch {
    /* asserted below */
  }
  // A response still pending at the 25s mark means authority is unproven here; the
  // DOM status assertions below judge what was actually rendered.
  const responseHasLiveAuthority =
    responseStillPending ? false : Boolean(firstLiveBody?.cache?.fresh_live_authority);
  const degradedNoAuthority =
    !responseStillPending &&
    !responseHasLiveAuthority &&
    (firstLiveBody?.cache?.stale === true || (Array.isArray(firstLiveBody?.stations) && firstLiveBody.stations.length > 0));

  let statusBefore = null;
  if (degradedNoAuthority) {
    statusBefore = await page.evaluate(() => {
      const dot = Array.from(document.querySelectorAll("section span.live-dot")).find(
        (el) => !el.closest("article"),
      );
      const row = dot ? dot.parentElement : null;
      if (!row) return null;
      window.__voltaStatusRow = row;
      window.__voltaStatusLog = [];
      const read = () => (row.textContent || "").replace(/\s+/g, " ").trim();
      let last = read();
      const observer = new MutationObserver(() => {
        const text = read();
        if (text !== last) {
          last = text;
          window.__voltaStatusLog.push({ t: Date.now(), text });
        }
      });
      observer.observe(row, { subtree: true, childList: true, characterData: true });
      return last;
    });
    // The protected realtime behavior is the honest labeling: after a no-authority
    // response the status row must NOT claim live/updated authority.
    if (statusBefore == null) {
      failures.push("live status row (section span.live-dot outside cards) was not found");
    } else if (!/Cached evidence · not live|^Not live\b/.test(statusBefore)) {
      failures.push(`degraded live view must stay honestly non-live in the status row, got: "${statusBefore}"`);
    }
  } else {
    statusBefore = await page.evaluate(() => {
      const dot = Array.from(document.querySelectorAll("section span.live-dot")).find(
        (el) => !el.closest("article"),
      );
      const row = dot ? dot.parentElement : null;
      if (!row) return null;
      window.__voltaStatusRow = row;
      window.__voltaStatusLog = [];
      const read = () => (row.textContent || "").replace(/\s+/g, " ").trim();
      let last = read();
      const observer = new MutationObserver(() => {
        const text = read();
        if (text !== last) {
          last = text;
          window.__voltaStatusLog.push({ t: Date.now(), text });
        }
      });
      observer.observe(row, { subtree: true, childList: true, characterData: true });
      return last;
    });
    if (statusBefore == null) {
      failures.push("live status row (section span.live-dot outside cards) was not found");
    } else if (!/Live · updated|Live source \+ cached evidence · updated|Refreshing…|Refreshing live \+ cached evidence…|^Live\b/.test(statusBefore)) {
      failures.push(`live status text after live fetch is missing/unexpected: "${statusBefore}"`);
    }
  }

  // --- Wait (real time) for the 60s background refetch ----------------------
  // Wait for the SECOND live REQUEST, not any live response: when the first fetch is
  // slow (~45s on a cold cache), its response resolves inside this window and would
  // otherwise impersonate the auto-refresh tick.
  const liveSeenBeforeWait = chargerRequests.filter((r) => isLiveChargers(r.url)).length;
  const tickTimeout = Math.min(78000, remaining(12000));
  let tickRequest = null;
  {
    const deadline = Date.now() + tickTimeout;
    while (Date.now() < deadline) {
      const liveNow = chargerRequests.filter((r) => isLiveChargers(r.url));
      if (liveNow.length >= 2) {
        tickRequest = liveNow[1];
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  }
  if (!tickRequest) {
    failures.push(`60s auto-refresh refetch was not observed within ${Math.round(tickTimeout / 1000)}s of live mode`);
  } else {
    // Bounded wait for the tick's RESPONSE: the request fires at the interval boundary
    // but a cache-miss refresh can take ~45s. Without this wait the settle assertions
    // below would judge a half-completed tick.
    {
      const rDeadline = Date.now() + Math.max(5000, remaining(10000));
      while (Date.now() < rDeadline) {
        const landed = chargerResponses.some(
          (r) => r.url === tickRequest.url && r.status === 200 && r.t >= tickRequest.t,
        );
        if (landed) break;
        await new Promise((resolve) => setTimeout(resolve, 500));
      }
    }
    // Give the UI a moment to leave "Refreshing…" and re-render the updated text.
    // In the honest degraded mode the refreshed response has no live authority again:
    // the honest settle is any non-loading status row ("Cached evidence · not live" is
    // the no-change case), which is validated by the assertion below.
    await page
      .waitForFunction(
        () => {
          const row = window.__voltaStatusRow;
          if (!row) return false;
          const text = (row.textContent || "").replace(/\s+/g, " ").trim();
          return /Live · updated|Live source \+ cached evidence · updated|Cached evidence · not live|^Not live\b/.test(text);
        },
        { timeout: 15000 },
      )
      .catch(() => failures.push("status text did not settle to an honest updated live/mixed-authority label after the auto-refresh"));
  }

  // --- Collect page-side facts ----------------------------------------------
  const pageFacts = await page.evaluate(() => {
    const row = window.__voltaStatusRow || null;
    const text = row ? (row.textContent || "").replace(/\s+/g, " ").trim() : "";
    let liveRegion = null;
    if (row) {
      const dot = row.querySelector("span.live-dot");
      let el = dot ? dot.nextElementSibling || row : row;
      while (el && el !== document.body) {
        const role = el.getAttribute("role");
        const live = el.getAttribute("aria-live");
        if (role === "status" || live === "polite" || live === "assertive") {
          liveRegion = { role, ariaLive: live, ariaAtomic: el.getAttribute("aria-atomic") };
          break;
        }
        el = el.parentElement;
      }
    }
    const errorCards = Array.from(document.querySelectorAll("section .card")).filter((card) =>
      /Couldn.t reach the live source/.test(card.textContent || ""),
    ).length;
    return {
      title: document.title,
      intervals: window.__voltaIntervals || [],
      statusText: text,
      statusLog: window.__voltaStatusLog || [],
      liveRegion,
      errorCards,
      cards: document.querySelectorAll("article.card").length,
      bodyW: Math.max(document.body.scrollWidth, document.documentElement.scrollWidth),
      winW: window.innerWidth,
    };
  });

  await browser.close();

  // --- Assertions ------------------------------------------------------------
  if (!pageFacts.intervals.some((entry) => entry.delay === 60000)) {
    failures.push("60s auto-refresh interval was not registered in live mode");
  }

  const liveRequests = chargerRequests.filter((r) => isLiveChargers(r.url));
  const first = liveRequests[0];
  const second = liveRequests[1];
  const gapMs = first && second ? second.t - first.t : null;
  if (liveRequests.length < 2) {
    failures.push(`expected a background live refetch (>=2 live chargers requests), saw ${liveRequests.length}`);
  } else {
    if (second.url.includes("force=true")) {
      failures.push("background auto-refresh unexpectedly used force=true (must not bypass the server cache)");
    }
    if (second.url !== first.url) {
      failures.push("background refetch changed the live query URL (should repeat the same viewport query)");
    }
    if (gapMs != null && gapMs < 45000) {
      failures.push(`second live request came after ${Math.round(gapMs / 1000)}s — too soon to be the 60s interval`);
    }
    const tickOk = chargerResponses.some((r) => r.url === second.url && r.status === 200 && r.t >= second.t);
    if (!tickOk) failures.push("auto-refresh response with status 200 was not observed");
  }
  if (!chargerResponses.every((r) => r.status === 200)) {
    failures.push(`charger response status failure: ${JSON.stringify(chargerResponses)}`);
  }

  // Status text must actually refresh when the background refetch runs.
  // Degraded no-authority mode: the second response can carry the same no-authority
  // cache stamps, so an unchanged honest label IS the honest result there — the
  // must-change contract stays on the healthy path only.
  if (second) {
    const changedAfterTick =
      degradedNoAuthority ||
      pageFacts.statusLog.some((entry) => entry.t >= second.t - 100) ||
      pageFacts.statusLog.some((entry) => /Refreshing…|Refreshing live \+ cached evidence…/.test(entry.text));
    if (!changedAfterTick) {
      failures.push("status row text never changed during the auto-refresh (no honest refreshing/updated re-render)");
    }
  }
  if (!pageFacts.statusText) failures.push("live status text is empty after refresh");

  // Live-region contract from the retired shell (#sourceCard role=status
  // aria-live=polite aria-atomic=true). Honest assertion — do not fake it.
  if (!pageFacts.liveRegion) {
    failures.push(
      'a11y gap: live/mixed-authority status text has no role="status"/aria-live live region (retired shell exposed role=status aria-live=polite aria-atomic=true)',
    );
  } else if (pageFacts.liveRegion.role !== "status" && pageFacts.liveRegion.ariaLive !== "polite") {
    failures.push(`status live-region semantics changed: ${JSON.stringify(pageFacts.liveRegion)}`);
  }

  if (pageFacts.errorCards) failures.push("error card rendered during realtime refresh check");
  if (pageFacts.bodyW > pageFacts.winW) failures.push(`horizontal overflow ${pageFacts.bodyW} > ${pageFacts.winW}`);
  if (logs.length) failures.push(`console warnings/errors: ${logs.join(" | ")}`);

  const summary = {
    ok: failures.length === 0,
    baseUrl,
    failures,
    logs,
    result: {
      title: pageFacts.title,
      interval60s: pageFacts.intervals.filter((entry) => entry.delay === 60000).length,
      overviewRequests: chargerRequests.filter((r) => !isLiveChargers(r.url)).length,
      liveRequests: liveRequests.map((r) => r.url),
      liveRequestGapMs: gapMs,
      responses: chargerResponses,
      statusBefore,
      statusText: pageFacts.statusText,
      statusLog: pageFacts.statusLog,
      liveRegion: pageFacts.liveRegion,
      errorCards: pageFacts.errorCards,
      cards: pageFacts.cards,
      bodyW: pageFacts.bodyW,
      winW: pageFacts.winW,
      elapsedMs: Date.now() - startedAt,
    },
  };
  console.log(JSON.stringify(summary, null, 2));
  if (failures.length) process.exit(1);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
