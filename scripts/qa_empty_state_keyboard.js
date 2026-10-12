#!/usr/bin/env node
/* Keyboard-only recovery contract for empty list states (React SPA).
 *
 * Ported from the retired static shell. Intent preserved:
 *  1. A keyboard/screen-reader flow can TAB to the "Open now" chip and toggle it with Space.
 *  2. When that filters everything out, a SAFE empty state renders (0 cards, honest copy,
 *     recovery actions present and keyboard-reachable).
 *  3. Activating a recovery action with Enter must actually restore results — the old shell's
 *     "Show trusted leads"/"Broaden search" released the Open now filter and brought cards
 *     back. The SPA equivalents are "Show all leads" / "Broaden price"; we assert the same
 *     user outcome (Open now released OR results restored) and keep the assertion red if the
 *     SPA leaves the keyboard user stuck.
 *  4. A recovery affordance that IS designed for its state must work keyboard-only:
 *     Leads-off empty -> "Show all leads" restores and broadens; a no-match home lookup
 *     keeps known chargers visible and exposes a keyboard-reachable "Clear search" action.
 *     Passes are isolated by fresh loads because Chrome resumes sequential focus from
 *     removed empty-state controls at their old list position.
 *  5. Restored cards keep the honesty guards: leads say "Not live", the decision pill is
 *     never "Go now" for a lead, and no drive ETA is fabricated without a location
 *     ("Locate to see ETA").
 *  6. Filtering out a selected card clears selection; restoring the row does not reselect it.
 *  7. No horizontal overflow at 390x844, no console/page errors.
 */
const { chromium } = require("playwright");
const {
  installDeterministicBasemap,
  unexpectedBrowserErrors,
} = require("./qa_browser_harness");

const baseUrl = (process.argv[2] || process.env.VOLTA_QA_BASE_URL || "http://127.0.0.1:8773").replace(/\/$/, "");
const chromePath = process.env.VOLTA_QA_CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

const EMPTY_RX_SRC = "No chargers match these filters|No charger nearby yet";

async function collect(page) {
  return page.evaluate((emptyRxSrc) => {
    const emptyRx = new RegExp(emptyRxSrc);
    const all = (sel, root = document) => Array.from(root.querySelectorAll(sel));
    const chips = {};
    for (const b of all('div[role="group"][aria-label="Filters"] dialog button.chip')) {
      chips[(document.getElementById(b.getAttribute("aria-labelledby"))?.textContent || "").trim()] = b.getAttribute("aria-pressed") === "true";
    }
    const emptyCard = all("section .card").find(
      (c) => !c.closest("[data-station]") && emptyRx.test(c.textContent || ""),
    );
    const wraps = all("[data-station]");
    const rendered = wraps.map((w) => {
      const a = w.querySelector("article.card");
      const text = (a && a.textContent) || "";
      const pillEl = a && a.querySelector(".pill");
      return {
        id: w.getAttribute("data-station"),
        selected: a?.querySelector('[role="button"][aria-current="true"]') != null,
        pill: pillEl ? (pillEl.textContent || "").trim() : "",
        notLive: /Not live/.test(text),
        locateEta: /Locate to see ETA/.test(text),
        claimsDrive: /min drive/.test(text),
        maps: all("a", a || w).some((x) => /Open in Maps/.test(x.textContent || "")),
      };
    });
    const h1n = document.querySelector("h1 span.tabular-nums");
    const input = document.querySelector('input[inputmode="search"]');
    return {
      total: h1n ? parseInt(h1n.textContent, 10) : -1,
      renderedCount: wraps.length,
      rendered,
      chips,
      noPriceLimit: all("dialog button[aria-pressed]").some(button => (button.textContent || "").trim() === "No limit" && button.getAttribute("aria-pressed") === "true"),
      emptyState: Boolean(emptyCard),
      emptyText: emptyCard ? (emptyCard.textContent || "").replace(/\s+/g, " ").trim().slice(0, 180) : "",
      recovery: emptyCard ? all("button", emptyCard).map((b) => (b.textContent || "").trim()) : [],
      searchValue: input ? input.value : "",
      searchExpanded: input ? input.getAttribute("aria-expanded") === "true" : false,
      homeSearchNoMatch: /No matching home area found\./.test(document.body.textContent || ""),
      overflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    };
  }, EMPTY_RX_SRC);
}

async function focusState(page) {
  return page.evaluate((emptyRxSrc) => {
    const emptyRx = new RegExp(emptyRxSrc);
    const el = document.activeElement;
    if (!el) return { tag: "", text: "", aria: "", chip: "", emptyAction: "" };
    const text = (el.getAttribute("aria-labelledby") ? document.getElementById(el.getAttribute("aria-labelledby"))?.textContent || "" : el.textContent || "").trim().slice(0, 60);
    const card = el.closest ? el.closest(".card") : null;
    const inEmptyCard = Boolean(card && !el.closest("[data-station]") && emptyRx.test(card.textContent || ""));
    return {
      tag: el.tagName || "",
      text,
      aria: (el.getAttribute && el.getAttribute("aria-label")) || "",
      chip: el.classList && el.classList.contains("chip") ? text : "",
      emptyAction: inEmptyCard && el.tagName === "BUTTON" ? text : "",
      stationId: el.getAttribute("role") === "button" ? el.closest("[data-station]")?.getAttribute("data-station") || "" : "",
    };
  }, EMPTY_RX_SRC);
}

/** Keyboard-only: press Tab until the focused element matches. No mouse, no locator.focus(). */
async function tabUntil(page, predicate, limit = 40, key = "Tab") {
  const seen = [];
  for (let i = 0; i < limit; i += 1) {
    await page.keyboard.press(key);
    const state = await focusState(page);
    seen.push(
      state.chip
        ? `chip:${state.chip}`
        : state.emptyAction
          ? `empty:${state.emptyAction}`
          : state.aria || state.text || state.tag || "?",
    );
    if (predicate(state)) return { ok: true, steps: i + 1, seen };
  }
  return { ok: false, steps: limit, seen };
}

async function openApp(page) {
  await page.goto(`${baseUrl}/`, { waitUntil: "domcontentloaded", timeout: 20000 });
  await page.waitForSelector("[data-station] article.card", { timeout: 20000 });
  await page
    .waitForFunction(
      () => {
        const n = document.querySelector("h1 span.tabular-nums");
        return n && parseInt(n.textContent, 10) > 0 && document.querySelector(".maplibregl-canvas");
      },
      null,
      { timeout: 15000 },
    )
    .catch(() => null);
  await page.waitForTimeout(500); // let the map commit its viewport so list counts are stable
}

/** Keyboard-only dialog entry, toggle, Escape and return to the trigger. */
async function toggleChipByKeyboard(page, label, key = "Tab") {
  const trigger = await tabUntil(page, (s) => s.text.startsWith("All filters"), 40, key);
  if (!trigger.ok) return trigger;
  await page.keyboard.press("Enter");
  await page.getByRole("dialog", { name: "All filters", exact: true }).waitFor();
  const reach = await tabUntil(page, (s) => s.chip === label, 40);
  if (reach.ok) await page.keyboard.press("Space");
  await page.keyboard.press("Escape");
  await page.getByRole("dialog", { name: "All filters", exact: true }).waitFor({ state: "hidden" });
  const returned = (await focusState(page)).text.startsWith("All filters");
  return { ...reach, ok: reach.ok && returned, seen: [...trigger.seen, "dialog:opened", ...reach.seen, returned ? "dialog:returned" : "dialog:focus-lost"] };
}

/** Tab to an empty-state recovery button and activate it with Enter. */
async function activateRecoveryByKeyboard(page, label) {
  const reach = await tabUntil(page, (s) => s.emptyAction === label, 50);
  if (!reach.ok) return reach;
  await page.keyboard.press("Enter");
  return reach;
}

async function waitForEmptyState(page) {
  await page
    .waitForFunction(
      (emptyRxSrc) => {
        const emptyRx = new RegExp(emptyRxSrc);
        const cards = document.querySelectorAll("[data-station]").length;
        const empty = Array.from(document.querySelectorAll("section .card")).some(
          (c) => !c.closest("[data-station]") && emptyRx.test(c.textContent || ""),
        );
        return cards === 0 && empty;
      },
      EMPTY_RX_SRC,
      { timeout: 10000 },
    )
    .catch(() => null);
}

async function waitForCardsBack(page) {
  await page
    .waitForFunction(() => document.querySelectorAll("[data-station]").length > 0, null, { timeout: 10000 })
    .catch(() => null);
  await page.waitForTimeout(400);
}

function checkCardHonesty(snapshot, failures, label) {
  const cards = snapshot.rendered;
  if (!cards.length) return;
  if (!cards.every((c) => c.pill)) failures.push(`${label}: a rendered card is missing its decision pill`);
  if (!cards.every((c) => c.notLive)) failures.push(`${label}: a lead card is not marked "Not live"`);
  if (cards.some((c) => c.pill === "Go now")) {
    failures.push(`${label}: a lead card claims "Go now" without live proof`);
  }
  if (!cards.every((c) => c.locateEta && !c.claimsDrive)) {
    failures.push(`${label}: a card fabricates a drive ETA without a located user (expected "Locate to see ETA")`);
  }
  if (!cards.every((c) => c.maps)) failures.push(`${label}: a rendered card is missing its Open in Maps handoff`);
}

async function main() {
  const logs = [];
  const failures = [];
  const browser = await chromium.launch({ headless: true, executablePath: chromePath });
  const page = await browser.newPage({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    isMobile: true,
    reducedMotion: "reduce",
  });
  // This contract owns keyboard recovery, not third-party tile availability. Serve a
  // deterministic local tile; the dedicated CDN-blocked contract owns the tiles-down story.
  await installDeterministicBasemap(page);
  page.setDefaultTimeout(12000);
  page.on("console", (msg) => {
    const url = (msg.location() || {}).url || "";
    logs.push(`${msg.type()}: ${msg.text()}${url ? ` [${url}]` : ""}`);
  });
  page.on("pageerror", (err) => logs.push(`pageerror: ${err.message}`));
  // Deterministic no-match home lookup for the keyboard-combobox pass below. Address
  // search is now an origin picker, not a local card filter.
  await page.route("**/api/geocode?*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ query: "", results: [] }),
    }),
  );

  // Same simulation as the retired shell: any "Open now" fetch reports zero live coverage,
  // so the empty state is deterministic. A recovery that correctly releases the Open now
  // filter re-fetches WITHOUT available=true and escapes the mock into real data.
  await page.route("**/api/chargers?**", (route) => {
    const url = route.request().url();
    if (url.includes("available=true")) {
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          stations: [],
          leads: [],
          warning: "Low live coverage: 0/3 live stations under cap. Refresh or widen filters before routing.",
          meta: {
            coverage: {
              mode: "degraded",
              is_degraded: true,
              live_route_ready: 0,
              lead_count: 0,
              minimum_live_route_ready: 3,
              summary: "Low live coverage: 0/3 route-ready live stations.",
              warning: "Low live coverage: 0/3 live stations under cap. Refresh or widen filters before routing.",
            },
          },
        }),
      });
      return;
    }
    route.continue();
  });

  // ---- Pass 1: Open now -> empty state -> "Show all leads" ------------------------------
  await openApp(page);
  const initial = await collect(page);
  if (initial.total <= 0 || initial.renderedCount === 0) failures.push("initial overview rendered no station cards");
  checkCardHonesty(initial, failures, "initial overview");

  const openNowReach = await toggleChipByKeyboard(page, "Open now");
  if (!openNowReach.ok) failures.push("keyboard Tab did not reach the Open now chip");
  await waitForEmptyState(page);
  const openNow = await collect(page);
  if (!openNow.chips["Open now"]) failures.push("Space on the focused Open now chip did not toggle it on");
  if (openNow.renderedCount !== 0 || !openNow.emptyState || !/No chargers match these filters/.test(openNow.emptyText)) {
    failures.push("Open now keyboard path did not render the safe empty state");
  }
  if (!openNow.recovery.includes("Show all leads") || !openNow.recovery.includes("Broaden price")) {
    failures.push("Open now empty state is missing its recovery actions (Show all leads / Broaden price)");
  }

  const showAllReach = await activateRecoveryByKeyboard(page, "Show all leads");
  if (!showAllReach.ok) failures.push("keyboard Tab did not reach the Show all leads recovery button");
  await waitForCardsBack(page);
  const afterShowAll = await collect(page);
  if (showAllReach.ok) {
    if (!afterShowAll.chips["Leads (Verify first)"] || afterShowAll.chips["Source checked"] || afterShowAll.chips["Easy parking"]) {
      failures.push("Show all leads did not apply its promised filter state (Leads on, Source checked/Easy parking off)");
    }
    // Old-shell contract: the recovery action released the availability filter and restored
    // results. Keep this red if the keyboard user stays stuck on an empty list.
    if (afterShowAll.chips["Open now"] !== false || afterShowAll.renderedCount === 0 || afterShowAll.total <= 0) {
      failures.push(
        "Show all leads did not recover from the Open now empty state (Open now stays active and the list stays empty — the retired shell's trusted recovery released it)",
      );
    }
  }

  // ---- Pass 2 (fresh load): Open now -> empty state -> "Broaden price" ------------------
  await page.reload({ waitUntil: "domcontentloaded", timeout: 20000 });
  await page.waitForSelector("[data-station] article.card", { timeout: 20000 });
  await page.waitForTimeout(500);
  const openNowReach2 = await toggleChipByKeyboard(page, "Open now");
  if (!openNowReach2.ok) failures.push("keyboard Tab did not reach the Open now chip on the second pass");
  await waitForEmptyState(page);
  const openNow2 = await collect(page);
  if (openNow2.renderedCount !== 0 || !openNow2.emptyState) {
    failures.push("second Open now keyboard pass did not render the safe empty state");
  }

  const broadenReach = await activateRecoveryByKeyboard(page, "Broaden price");
  if (!broadenReach.ok) failures.push("keyboard Tab did not reach the Broaden price recovery button");
  await waitForCardsBack(page);
  const afterBroaden = await collect(page);
  if (broadenReach.ok) {
    if (!afterBroaden.noPriceLimit) {
      failures.push("Broaden price did not restore No limit");
    }
    if (afterBroaden.chips["Open now"] !== false || afterBroaden.renderedCount === 0 || afterBroaden.total <= 0) {
      failures.push(
        "Broaden price did not recover from the Open now empty state (Open now stays active and the list stays empty — the retired shell's broad recovery released it)",
      );
    }
  }

  // ---- Pass 3 (fresh load): Leads off -> empty -> "Show all leads" actually recovers ----
  await page.reload({ waitUntil: "domcontentloaded", timeout: 20000 });
  await page.waitForSelector("[data-station] article.card", { timeout: 20000 });
  await page.waitForTimeout(500);
  const baseline = await collect(page);

  const leadsReach = await toggleChipByKeyboard(page, "Leads (Verify first)");
  if (!leadsReach.ok) failures.push("keyboard Tab did not reach the Leads chip");
  await waitForEmptyState(page);
  const leadsOff = await collect(page);
  if (leadsOff.renderedCount !== 0 || !leadsOff.emptyState) {
    failures.push("turning Leads off via keyboard did not render the safe empty state");
  }
  if (!leadsOff.recovery.includes("Show all leads")) {
    failures.push("Leads-off empty state is missing the Show all leads recovery action");
  }

  const recoverReach = await activateRecoveryByKeyboard(page, "Show all leads");
  if (!recoverReach.ok) failures.push("keyboard Tab did not reach Show all leads in the Leads-off empty state");
  await waitForCardsBack(page);
  const recovered = await collect(page);
  if (recovered.renderedCount === 0 || recovered.total <= 0) {
    failures.push("Show all leads keyboard activation did not restore cards from the Leads-off empty state");
  }
  if (!recovered.chips["Leads (Verify first)"] || recovered.chips["Source checked"] || recovered.chips["Easy parking"]) {
    failures.push("Show all leads recovery did not restore the promised filter state");
  }
  // Current contract: "Show all leads" re-enables leads, restoring the default trusted view
  // (Leads on, Source checked/Easy parking off). The retired shell's recovery ALSO broadened
  // scope, so the old strict "more rows than baseline" expectation no longer describes this
  // button; what must never happen is a recovery that comes back smaller than the promised view.
  if (baseline.total > 0 && recovered.total < baseline.total) {
    failures.push("Show all leads recovery returned fewer rows than the default trusted view");
  }
  checkCardHonesty(recovered, failures, "recovered list");
  if (recovered.overflowX) failures.push("recovered list has horizontal overflow at 390x844");

  // ---- Pass 4 (fresh load): no-match home lookup -> known list stays + Clear search ------
  // Fresh load on purpose: after a recovery button is activated and removed, Chrome resumes
  // sequential focus from its old position INSIDE the list, so tabbing forward would have to
  // walk hundreds of virtualized cards before ever reaching the search input again.
  await page.reload({ waitUntil: "domcontentloaded", timeout: 20000 });
  await page.waitForSelector("[data-station] article.card", { timeout: 20000 });
  await page.waitForTimeout(500);
  const searchReach = await tabUntil(page, (s) => s.aria === "Enter home address or city", 40);
  if (!searchReach.ok) failures.push("keyboard Tab did not reach the search input");
  await page.keyboard.type("zzqxplomo", { delay: 15 });
  await page.waitForFunction(
    () => {
      const input = document.querySelector('input[aria-label="Enter home address or city"]');
      return input?.getAttribute("aria-expanded") === "true" && /No matching home area found\./.test(document.body.textContent || "");
    },
    null,
    { timeout: 10000 },
  );
  const searchLookup = await collect(page);
  if (searchLookup.renderedCount === 0 || searchLookup.emptyState) {
    failures.push("no-match home lookup erased the known charger list instead of keeping it as a fallback");
  }
  if (!searchLookup.searchExpanded || !searchLookup.homeSearchNoMatch) {
    failures.push("home lookup did not expose its exact no-match combobox state");
  }
  if (searchLookup.overflowX) failures.push("home lookup state has horizontal overflow at 390x844");

  const clearReach = await tabUntil(page, (s) => s.aria === "Clear search", 12);
  if (!clearReach.ok) failures.push("keyboard Tab did not reach the Clear search recovery button");
  if (clearReach.ok) await page.keyboard.press("Enter");
  await page.waitForFunction(
    () => document.querySelector('input[aria-label="Enter home address or city"]')?.value === "",
    null,
    { timeout: 5000 },
  ).catch(() => null);
  const cleared = await collect(page);
  if (clearReach.ok) {
    if (cleared.searchValue !== "") failures.push("Clear search recovery did not clear the query");
    if (cleared.renderedCount === 0 || cleared.total <= 0) {
      failures.push("Clear search keyboard activation did not restore cards");
    }
  }

  // ---- Pass 5 (fresh load): selected row -> filter removes it -> recovery stays clear ----
  await openApp(page);
  // Weaker overview leads may intentionally lack a map pin and cannot retain selection.
  const sourceReach = await toggleChipByKeyboard(page, "Source checked");
  if (!sourceReach.ok) failures.push("keyboard Tab did not reach Source checked for the selection pass");
  await waitForCardsBack(page);
  const selectReach = await tabUntil(page, (s) => Boolean(s.stationId), 40);
  if (!selectReach.ok) failures.push("keyboard Tab did not reach a station selection control");
  const selectedId = (await focusState(page)).stationId;
  let selectionRecovery = { id: selectedId || "", selectedBefore: false, restored: false, selectedAfter: null };
  if (selectReach.ok) {
    await page.keyboard.press("Enter");
    await page.waitForFunction((id) => [...document.querySelectorAll("[data-station]")]
      .some((row) => row.getAttribute("data-station") === id && row.querySelector('article [role="button"][aria-current="true"]')), selectedId, { timeout: 5000 });
    selectionRecovery.selectedBefore = true;
    // Reverse through the card controls to the preceding filter rail, keeping the
    // regression keyboard-only without walking every virtualized result.
    const removeReach = await toggleChipByKeyboard(page, "Open now", "Shift+Tab");
    if (!removeReach.ok) failures.push("keyboard Shift+Tab did not reach Open now from the selected station");
    if (removeReach.ok) {
      await waitForEmptyState(page);
      const selectionEmpty = await collect(page);
      if (!selectionEmpty.emptyState || selectionEmpty.renderedCount !== 0) failures.push("Open now did not remove the selected row");
      const restoreReach = await activateRecoveryByKeyboard(page, "Show all leads");
      if (!restoreReach.ok) failures.push("keyboard Tab did not reach recovery after removing the selected station");
      await waitForCardsBack(page);
      // Recovery broadens source constraints. Restore the original source filter with
      // the keyboard so this virtualized row is visible again before checking selection.
      const sourceAgain = await toggleChipByKeyboard(page, "Source checked", "Shift+Tab");
      if (!sourceAgain.ok) failures.push("keyboard could not restore the original source constraint after recovery");
      await waitForCardsBack(page);
      const restored = (await collect(page)).rendered.find((row) => row.id === selectedId);
      selectionRecovery = { ...selectionRecovery, restored: Boolean(restored), selectedAfter: restored?.selected ?? null };
      if (!restored) failures.push("selection recovery did not restore the original station row");
      else if (restored.selected) failures.push("restoring a filtered-out station incorrectly restored its selection");
    }
  }

  await browser.close();

  // Third-party basemap tile weather is not this contract's concern (qa_cdn_blocked_ui owns
  // the tiles-down story); every app-origin error or pageerror still fails the run.
  const unexpectedErrors = unexpectedBrowserErrors(logs);
  if (unexpectedErrors.length) failures.push(`browser console contains errors: ${unexpectedErrors.join(" | ")}`);

  console.log(
    JSON.stringify(
      {
        ok: failures.length === 0,
        baseUrl,
        initial: { total: initial.total, rendered: initial.renderedCount, chips: initial.chips },
        openNow: { empty: openNow.emptyState, recovery: openNow.recovery, total: openNow.total },
        afterShowAllLeads: { total: afterShowAll.total, rendered: afterShowAll.renderedCount, chips: afterShowAll.chips },
        afterBroadenPrice: { total: afterBroaden.total, rendered: afterBroaden.renderedCount, chips: afterBroaden.chips },
        leadsOffRecovery: {
          baselineTotal: baseline.total,
          emptyRecovery: leadsOff.recovery,
          recoveredTotal: recovered.total,
          recoveredChips: recovered.chips,
          renderedHonesty: recovered.rendered.slice(0, 4),
        },
        clearSearch: {
          noMatchShown: searchLookup.homeSearchNoMatch,
          knownCardsKept: searchLookup.renderedCount,
          clearedTotal: cleared.total,
          searchValue: cleared.searchValue,
        },
        selectionRecovery,
        focus: {
          openNow: openNowReach.seen.slice(-6),
          showAllLeads: showAllReach.seen.slice(-6),
          broadenPrice: broadenReach.seen.slice(-6),
          leads: leadsReach.seen.slice(-6),
          clearSearch: clearReach.seen.slice(-6),
        },
        logs: logs.slice(0, 20),
        failures,
      },
      null,
      2,
    ),
  );
  process.exit(failures.length ? 1 : 0);
}

main().catch((err) => {
  console.error(JSON.stringify({ ok: false, baseUrl, error: `${err.name}: ${err.message}` }, null, 2));
  process.exit(1);
});
