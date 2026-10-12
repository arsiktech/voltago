#!/usr/bin/env node
"use strict";

/*
 * qa_ax_tree_contract.js — accessibility-tree contract for the Volta React SPA.
 *
 * Ported from the retired vanilla shell. Protected behaviors (intent kept, surface adapted):
 *  - The panel's controls are exposed in the Chrome AX tree with proper names/roles:
 *    h1 heading with result count, search textbox (aria-label), Filters group with
 *    aria-pressed toggle chips (and the documented default pressed states), and the
 *    icon buttons (Use my location / context-aware Refresh / theme switch).
 *  - Station card content is readable in the AX tree: station name (h3 heading), price,
 *    availability ("Not live" for leads / "N free" live), a route fact, and the card's
 *    select target (div[role=button]) exposes those facts through name-from-content
 *    (replaces the old shell's per-card aria-label with Price/Parking/Source facts).
 *  - Per-card actions are exposed with STATION-SPECIFIC accessible names, like the old
 *    shell: "Open <name> in Maps" link, "Provider source for <name>" link, and
 *    "Source details for <name>" button with an expanded state — so a screen-reader
 *    links list is never just "Open in Maps" x N.
 *  - The map backdrop (aria-hidden wrapper around the MapLibre canvas) is IGNORED in the
 *    AX tree — screen readers never land on the canvas.
 *  - Status surface: the live-status row and "N live · M leads" badge carry text, the
 *    compact live/leads badge and the "Data honesty" disclosure are in the tree. The
 *    driver guidance and compact live/source-backed counts remain accessible. The
 *    CoverageBanner role=status live region is verified deterministically
 *    by re-loading with the /api/chargers response patched to cache.stale=true
 *    (replaces the old always-on #sourceCard role=status assertion).
 *  - No horizontal overflow at 390x844; no console errors/warnings.
 */

const { chromium } = require("playwright");
const { openFilters, closeFilters } = require("./qa_browser_harness");
const {
  installDeterministicBasemap,
  unexpectedBrowserErrors,
} = require("./qa_browser_harness");

const baseUrl = (process.argv[2] || process.env.VOLTA_QA_BASE_URL || "http://127.0.0.1:8773").replace(/\/$/, "");
const chromePath = process.env.VOLTA_QA_CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

const WATCHDOG_MS = 100000;

function compact(text) {
  return String(text || "").replace(/\s+/g, " ").trim();
}

function normalizeAxNodes(rawNodes) {
  return (rawNodes || []).map((node) => {
    const props = {};
    for (const p of node.properties || []) props[p.name] = p.value ? p.value.value : undefined;
    return {
      role: node.role ? String(node.role.value || "") : "",
      name: compact(node.name ? node.name.value : ""),
      ignored: Boolean(node.ignored),
      backendDOMNodeId: node.backendDOMNodeId,
      props,
      value: node.value ? node.value.value : undefined,
    };
  });
}

function findAx(nodes, { name, role, prop } = {}) {
  return nodes.find((n) => {
    if (n.ignored) return false;
    if (name != null) {
      if (name instanceof RegExp) {
        if (!name.test(n.name)) return false;
      } else if (n.name !== name) return false;
    }
    if (role && !role.test(n.role)) return false;
    if (prop && !(prop in n.props)) return false;
    return true;
  });
}

async function fullAxTree(cdp) {
  const ax = await cdp.send("Accessibility.getFullAXTree");
  return normalizeAxNodes(ax.nodes);
}

async function backendNodeIdFor(cdp, selector) {
  const doc = await cdp.send("DOM.getDocument", { depth: 1 });
  const q = await cdp.send("DOM.querySelector", { nodeId: doc.root.nodeId, selector });
  if (!q.nodeId) return null;
  const d = await cdp.send("DOM.describeNode", { nodeId: q.nodeId });
  return d.node ? d.node.backendNodeId : null;
}

async function main() {
  const watchdog = setTimeout(() => {
    console.error(JSON.stringify({ ok: false, baseUrl, error: "watchdog: script exceeded time budget" }));
    process.exit(1);
  }, WATCHDOG_MS);
  watchdog.unref();

  const logs = [];
  const browser = await chromium.launch({ headless: true, executablePath: chromePath });
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    isMobile: true,
    // This contract patches API responses after first paint. Keep that deterministic;
    // the dedicated offline phase in qa_cdn_blocked_ui.js is the service-worker test.
    serviceWorkers: "block",
  });
  const page = await context.newPage();
  // This contract owns semantics, not third-party tile availability. Serve a known-valid
  // local tile so unrelated network weather cannot produce an unattributable fetch error.
  await installDeterministicBasemap(page);
  page.on("console", (msg) => {
    const url = (msg.location() || {}).url || "";
    if (["error", "warning"].includes(msg.type())) {
      logs.push(`${msg.type()}: ${msg.text()}${url ? ` [${url}]` : ""}`);
    }
  });
  page.on("pageerror", (err) => logs.push(`pageerror: ${err.message}`));
  await page.goto(`${baseUrl}/`, { waitUntil: "domcontentloaded", timeout: 20000 });
  await page.waitForSelector("[data-station] article.card", { timeout: 30000 });
  const mapMounted = await page
    .waitForSelector(".maplibregl-canvas", { timeout: 15000, state: "attached" })
    .then(() => true)
    .catch(() => false);
  // Let entrance animations/name computation settle.
  await page.waitForTimeout(600);

  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Accessibility.enable").catch(() => {});
  const nodes = await fullAxTree(cdp);
  await openFilters(page);
  const filterNodes = await fullAxTree(cdp);
  await closeFilters(page);
  const canvasBackendId = mapMounted ? await backendNodeIdFor(cdp, ".maplibregl-canvas") : null;

  const dom = await page.evaluate(() => {
    const compactText = (value) => String(value || "").replace(/\s+/g, " ").trim();
    const panel = document.querySelector("section");
    const cardWrap = document.querySelector("[data-station]");
    const card = cardWrap ? cardWrap.querySelector("article.card") : null;
    const cardText = card ? compactText(card.innerText) : "";
    const priceSpan = card
      ? [...card.querySelectorAll("span")].find((s) => /^(Free|€\d+[.,]\d+)$/.test(compactText(s.textContent)))
      : null;
    const mapsLink = card ? [...card.querySelectorAll("a")].find((a) => /Open in Maps/.test(a.textContent)) : null;
    const sourceLink = card
      ? [...card.querySelectorAll("a")].find((a) => /^Start\/check/.test(compactText(a.textContent)))
      : null;
    const detailsBtn = card ? [...card.querySelectorAll("button")].find((el) => /Details/.test(el.textContent)) : null;
    const canvas = document.querySelector(".maplibregl-canvas");
    const liveRow = document.querySelector("span.live-dot");
    return {
      panelExists: Boolean(panel),
      h1: compactText(panel && panel.querySelector("h1") ? panel.querySelector("h1").textContent : ""),
      searchAria: (document.querySelector('input[inputmode="search"]') || {}).getAttribute
        ? document.querySelector('input[inputmode="search"]').getAttribute("aria-label") || ""
        : "",
      chips: [...document.querySelectorAll('div[role="group"][aria-label="Filters"] dialog button[aria-pressed]')].map(
        (b) => ({ label: compactText(document.getElementById(b.getAttribute("aria-labelledby"))?.textContent), pressed: b.getAttribute("aria-pressed") }),
      ),
      firstCard: card
        ? {
            id: cardWrap.getAttribute("data-station") || "",
            name: compactText(card.querySelector("h3") ? card.querySelector("h3").textContent : ""),
            price: priceSpan ? compactText(priceSpan.textContent) : "",
            tier: (() => {
              const badges = [...card.querySelectorAll(".badge")].map((b) => compactText(b.textContent));
              return badges.find((t) => ["Live", "Official", "Directory", "Community"].includes(t)) || "";
            })(),
            pill: card.querySelector(".pill") ? compactText(card.querySelector(".pill").textContent) : "",
            pillReason: card.querySelector(".pill") ? card.querySelector(".pill").getAttribute("title") || "" : "",
            availability: (cardText.match(/(\d+ free|Not live|Live · status unknown)/) || [""])[0],
            routeFact: (cardText.match(/(Locate to see ETA|\d+(?:[.,]\d+)?\s*k?m · \d+\s?(?:min|h))/) || [""])[0],
            hasMapsLink: Boolean(mapsLink),
            hasSourceLink: Boolean(sourceLink),
            mapsAria: mapsLink ? compactText(mapsLink.getAttribute("aria-label") || "") : "",
            sourceAria: sourceLink ? compactText(sourceLink.getAttribute("aria-label") || "") : "",
            detailsAria: detailsBtn ? compactText(detailsBtn.getAttribute("aria-label") || "") : "",
            detailsExpanded: detailsBtn ? detailsBtn.getAttribute("aria-expanded") : null,
            text: cardText.slice(0, 900),
          }
        : null,
      liveStatusText: liveRow && liveRow.parentElement ? compactText(liveRow.parentElement.textContent) : "",
      driverGuidance: compactText(document.querySelector("[data-driver-guidance]")?.textContent),
      // Scope to the panel: MapLibre's attribution control is also a <details>/<summary>.
      honestySummary: compactText(document.querySelector('button[aria-controls="data-honesty-panel"]')?.textContent),
      mapWrapperHidden: canvas ? Boolean(canvas.closest('[aria-hidden="true"]')) : null,
      bodyW: document.body.scrollWidth,
      winW: window.innerWidth,
    };
  });

  const failures = [];

  // --- Panel heading with count -------------------------------------------------
  if (!dom.panelExists) failures.push("panel <section> missing");
  if (!/^\d+ (public chargers|free chargers|chargers ≤ €\d+\.\d{2}\/kWh|nearby)$/.test(dom.h1)) {
    failures.push(`h1 heading does not carry a count: "${dom.h1}"`);
  }
  const axHeading = findAx(nodes, { name: /^\d+ (public chargers|free chargers|chargers ≤ €\d+\.\d{2}\/kWh|nearby)$/, role: /heading/i });
  if (!axHeading) failures.push("AX tree missing h1 heading with result count");

  // --- Search textbox -------------------------------------------------------------
  const searchLabel = "Enter home address or city";
  if (dom.searchAria !== searchLabel) failures.push(`search input aria-label drifted: "${dom.searchAria}"`);
  if (!findAx(nodes, { name: searchLabel, role: /textbox|searchbox|combobox/i })) {
    failures.push("AX tree missing search textbox with its aria-label");
  }

  // --- Icon buttons ----------------------------------------------------------------
  for (const label of ["Use my location", "Refresh known charger leads"]) {
    if (!findAx(nodes, { name: label, role: /button/i })) failures.push(`AX tree missing button named ${label}`);
  }
  if (!findAx(nodes, { name: /^Switch to (dark|light) mode$/, role: /button/i })) {
    failures.push("AX tree missing theme switch button (Switch to dark/light mode)");
  }

  // --- Filters group with pressed-state chips ---------------------------------------
  if (!findAx(nodes, { name: "Filters", role: /group/i })) failures.push("AX tree missing Filters group");
  const expectedChips = {
    "Open now": "false",
    "Source checked": "false",
    "Easy parking": "false",
    "Tesla OK": "false",
    Fast: "false",
    "Leads (Verify first)": "true",
  };
  if (!findAx(nodes, { name: "All filters", role: /button/i })) failures.push("AX tree missing All filters dialog trigger");
  if (!findAx(filterNodes, { name: "All filters", role: /dialog/i })) failures.push("AX tree missing named All filters dialog");
  const free = findAx(nodes, { name: "Free", role: /button/i, prop: "pressed" });
  if (!free || String(free.props.pressed) !== "false") failures.push("AX quick Free button must default off");
  if (!findAx(nodes, { name: "Charging price", role: /button/i })) failures.push("AX tree missing Charging price dialog opener");
  const noLimit = findAx(filterNodes, { name: "No limit", role: /button/i, prop: "pressed" });
  if (!noLimit || String(noLimit.props.pressed) !== "true") failures.push("AX No limit must default on");
  const priceSlider = findAx(filterNodes, { name: "Maximum charging price", role: /^slider$/i });
  if (!priceSlider || priceSlider.value !== "101" && priceSlider.value !== 101) failures.push("AX tree missing native price slider with No limit value");
  for (const [label, pressed] of Object.entries(expectedChips)) {
    const chip = findAx(filterNodes, { name: label, role: /button/i, prop: "pressed" });
    if (!chip) {
      failures.push(`AX tree missing pressed-state filter chip ${label}`);
    } else if (String(chip.props.pressed) !== pressed) {
      failures.push(`filter chip ${label} default pressed=${chip.props.pressed}, expected ${pressed}`);
    }
  }
  const domChipLabels = dom.chips.map((c) => c.label);
  for (const label of Object.keys(expectedChips)) {
    if (!domChipLabels.includes(label)) failures.push(`DOM Filters group missing chip ${label}`);
  }

  // --- First station card content readable in the tree ------------------------------
  if (!dom.firstCard) {
    failures.push("no station cards rendered");
  } else {
    const fc = dom.firstCard;
    if (!fc.name) failures.push("first station has no visible h3 name");
    if (!fc.price) failures.push("first station card has no price (Free / €0.NN)");
    if (!fc.availability) failures.push("first card misses availability facts (Not live / N free)");
    if (!fc.routeFact) failures.push("first card misses route fact (Locate to see ETA / km · min drive)");
    // Provenance facts (the old shell's Proof:/Source: lines): tier badge + decision pill.
    if (!fc.tier) failures.push("first card misses source-tier badge (Live/Official/Directory/Community)");
    if (!fc.pill) failures.push("first card misses decision pill (Go now / Check first / Verify first…)");

    if (fc.name && !findAx(nodes, { name: fc.name, role: /heading/i })) {
      failures.push(`AX tree missing first station name heading "${fc.name}"`);
    }
    // The card select target is a div[role=button]; name-from-content must surface the
    // decision facts a screen reader needs (station name + price + availability + route).
    const cardBtn = nodes.find(
      (n) => !n.ignored && /button/i.test(n.role) && fc.name && n.name.includes(fc.name),
    );
    if (!cardBtn) {
      failures.push("AX tree missing card select target (div[role=button] named from card content)");
    } else {
      for (const [what, token] of [
        ["price", fc.price],
        ["availability", fc.availability],
        ["route fact", fc.routeFact],
        ["source tier", fc.tier],
        ["decision pill", fc.pill],
      ]) {
        if (token && !cardBtn.name.includes(token)) {
          failures.push(`card select target AX name misses ${what} "${token}"`);
        }
      }
    }

    // Per-card actions in the tree. The SPA names them per station (aria-label
    // "Open <name> in Maps" / "Provider source for <name>" / "Source details for <name>"),
    // matching the old shell's station-specific action names.
    if (!fc.hasMapsLink) failures.push("first card has no Open in Maps link");
    if (fc.detailsExpanded !== "false" && fc.detailsExpanded !== "true") {
      failures.push("first card Details button misses aria-expanded");
    }
    if (!/^Open .+ in Maps$/.test(fc.mapsAria)) {
      failures.push(`Open in Maps link aria-label not station-specific: "${fc.mapsAria}"`);
    } else if (!findAx(nodes, { name: fc.mapsAria, role: /link/i })) {
      failures.push(`AX tree missing Open in Maps link named "${fc.mapsAria}"`);
    }
    if (fc.hasSourceLink) {
      if (!/^Open public provider page for .+$/.test(fc.sourceAria)) {
        failures.push(`Source link aria-label not station-specific: "${fc.sourceAria}"`);
      } else if (!findAx(nodes, { name: fc.sourceAria, role: /link/i })) {
        failures.push(`AX tree missing Source link named "${fc.sourceAria}"`);
      }
    }
    if (!/^Source details for .+$/.test(fc.detailsAria)) {
      failures.push(`Details button aria-label not station-specific: "${fc.detailsAria}"`);
    } else if (!findAx(nodes, { name: fc.detailsAria, role: /button/i, prop: "expanded" })) {
      failures.push(`AX tree missing Details button (expanded state) named "${fc.detailsAria}"`);
    }
  }

  // --- Map backdrop must stay out of the AX tree -------------------------------------
  if (!mapMounted) {
    failures.push("MapLibre canvas never mounted");
  } else {
    if (dom.mapWrapperHidden !== true) failures.push("map canvas is not wrapped in aria-hidden backdrop");
    if (canvasBackendId == null) {
      failures.push("could not resolve map canvas backend node for the AX ignore check");
    } else {
      const canvasAx = nodes.filter((n) => n.backendDOMNodeId === canvasBackendId);
      // Absent from the full tree, or present-but-ignored, both mean “not exposed”.
      if (canvasAx.some((n) => !n.ignored)) failures.push("map canvas is exposed (not ignored) in the AX tree");
    }
    if (nodes.some((n) => !n.ignored && /^canvas$/i.test(n.role))) {
      failures.push("a canvas element is exposed in the AX tree (map backdrop leak)");
    }
  }

  // --- Status / trust surface ---------------------------------------------------------
  if (!/(Source-backed leads · not live|Refreshing known chargers)/.test(dom.liveStatusText)) {
    failures.push(`overview trust status row text is empty or drifted: "${dom.liveStatusText}"`);
  }
  if (!/\d+ leads · not live/.test(dom.liveStatusText)) {
    failures.push(`overview not-live count badge missing from status row: "${dom.liveStatusText}"`);
  }
  if (!findAx(nodes, { name: /^(Source-backed leads · not live|Refreshing known chargers…)$/ })) {
    failures.push("AX tree missing overview trust status text");
  }
  if (!findAx(nodes, { name: /^\d+ leads · not live$/ })) {
    failures.push("AX tree missing the compact overview not-live count badge");
  }
  if (!dom.driverGuidance || !findAx(nodes, { name: /Search a city or use your location to check nearby chargers/ })) {
    failures.push("driver guidance missing from the DOM or accessibility tree");
  }
  if (dom.honestySummary !== "Data honesty") failures.push("Data honesty disclosure summary missing");
  if (!findAx(nodes, { name: "Data honesty" })) failures.push("AX tree missing Data honesty disclosure");

  // --- Layout + console hygiene --------------------------------------------------------
  if (dom.bodyW > dom.winW) failures.push(`horizontal overflow ${dom.bodyW} > ${dom.winW}`);

  // --- Phase 2: coverage status live region (deterministic via stale-cache patch) ------
  // The SPA renders its role=status CoverageBanner only when coverage degrades or the
  // cache is stale, so we force the stale branch by patching the API response.
  let statusRegion = { rendered: false, axExposed: false, text: "" };
  await page.route("**/api/chargers*", async (route) => {
    try {
      const resp = await route.fetch();
      const json = await resp.json();
      json.cache = Object.assign({}, json.cache, { stale: true });
      json.warning = json.warning || "QA: simulated stale cache to exercise the status live region.";
      await route.fulfill({ response: resp, json });
    } catch (err) {
      await route.continue().catch(() => {});
    }
  });
  await page.goto(`${baseUrl}/`, { waitUntil: "domcontentloaded", timeout: 20000 });
  await page.waitForSelector("[data-station] article.card", { timeout: 30000 });
  // The live-status row is ALSO role=status (always present), so wait for the specific
  // role=status element that carries the CoverageBanner title.
  const bannerHandle = await page
    .waitForFunction(
      () => {
        const els = [...document.querySelectorAll('section [role="status"]')];
        return els.find((el) => /Showing cached data/.test(el.textContent || "")) || null;
      },
      { timeout: 12000 },
    )
    .catch(() => null);
  if (bannerHandle) {
    statusRegion.rendered = true;
    statusRegion.text = compact(await bannerHandle.evaluate((el) => el.textContent));
    const staleNodes = await fullAxTree(cdp);
    // The banner's title text must itself be exposed in the AX tree (a role=status node
    // alone is trivially true — the live row already has one).
    statusRegion.axExposed = Boolean(findAx(staleNodes, { name: /Showing cached data/ }));
  }
  await page.unroute("**/api/chargers*").catch(() => {});
  if (!statusRegion.rendered) {
    failures.push("stale-cache coverage banner (role=status with 'Showing cached data') never rendered");
  } else {
    if (!/Showing cached data/.test(statusRegion.text)) {
      failures.push(`coverage status banner text drifted: "${statusRegion.text}"`);
    }
    if (!statusRegion.axExposed) failures.push("coverage banner content not exposed in AX tree");
  }

  const unexpectedLogs = unexpectedBrowserErrors(logs, {
    includeWarnings: true,
    allowServiceWorkerRegistrationBlocked: true,
  });
  if (unexpectedLogs.length) failures.push(`console warnings/errors: ${unexpectedLogs.join(" | ")}`);

  await browser.close();

  const namedNodes = nodes.filter((n) => n.name && !n.ignored);
  const summary = {
    ok: failures.length === 0,
    baseUrl,
    failures,
    logs,
    namedNodeCount: namedNodes.length,
    sampleNamedNodes: namedNodes.slice(0, 30).map((n) => ({ role: n.role, name: n.name.slice(0, 90) })),
    statusRegion,
    dom: {
      h1: dom.h1,
      chips: dom.chips,
      firstCard: dom.firstCard && {
        id: dom.firstCard.id,
        name: dom.firstCard.name,
        price: dom.firstCard.price,
        tier: dom.firstCard.tier,
        pill: dom.firstCard.pill,
        pillReason: dom.firstCard.pillReason,
        availability: dom.firstCard.availability,
        routeFact: dom.firstCard.routeFact,
        mapsAria: dom.firstCard.mapsAria,
        sourceAria: dom.firstCard.sourceAria,
        detailsAria: dom.firstCard.detailsAria,
      },
      liveStatusText: dom.liveStatusText,
      bodyW: dom.bodyW,
      winW: dom.winW,
    },
  };
  console.log(JSON.stringify(summary, null, 2));
  if (failures.length) process.exit(1);
}

main().catch((err) => {
  console.error(JSON.stringify({ ok: false, baseUrl, error: `${err.name}: ${err.message}` }, null, 2));
  process.exit(1);
});
