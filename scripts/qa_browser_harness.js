"use strict";

// Shared fail-closed console classifier for browser contracts. A failed basemap tile is
// optional product weather; an app/API failure is not. A line is waived only when that same
// line proves both the OpenFreeMap origin and MapLibre provenance. Batch-level correlation is not
// sufficient evidence: a simultaneous URL-less fetch failure could belong to app/API work.
const BASEMAP_RX = /https:\/\/tiles\.openfreemap\.org\//i;
const MAPLIBRE_BUNDLE_RX = /\/assets\/maplibre-[^\s'"\])]+\.js/i;

async function installDeterministicBasemap(page) {
  await page.route(/^https:\/\/tiles\.openfreemap\.org\//i, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ version: 8, sources: {}, layers: [] }),
    }),
  );
}

function isExpectedBasemapNoise(line) {
  const text = String(line || "");
  if (!/^(?:error|warning):/i.test(text) || !BASEMAP_RX.test(text)) return false;
  if (/Failed to load resource/i.test(text)) return true;
  if (!MAPLIBRE_BUNDLE_RX.test(text)) return false;
  return /AJAXError:\s*Failed to fetch|TypeError:\s*Failed to fetch/i.test(text);
}

// Chromium records this exact ANGLE readback performance diagnostic in its own
// WebGL tests: https://chromium.googlesource.com/chromium/src/+/3c071403b0115bb92ef085b4fc806930e712065c
// Recognition alone changes no error policy. A hermetic renderer test may explicitly
// preserve these diagnostics in its report while retaining all other warning gates.
// The final lookahead means end-of-input; JavaScript's $ also accepts a final newline.
const CHROMIUM_READ_PIXELS_DIAGNOSTIC = /^warning: (?:\[(?:supported|pending-theme|no-webgl2)\] )?\[\.WebGL-0x[0-9a-f]{1,16}\]GL Driver Message \(OpenGL, Performance, GL_CLOSE_PATH_NV, High\): GPU stall due to ReadPixels(?: \(this message will no longer repeat\))?(?![\s\S])/;

function isChromiumReadPixelsDiagnostic(line) {
  return typeof line === "string" && CHROMIUM_READ_PIXELS_DIAGNOSTIC.test(line);
}

function unexpectedBrowserErrors(
  logs,
  { includeWarnings = false, allowServiceWorkerRegistrationBlocked = false } = {},
) {
  const fatalPrefix = includeWarnings
    ? /^(?:warning|error|pageerror):/i
    : /^(?:error|pageerror):/i;
  return (logs || []).filter((line) => {
    const text = String(line || "");
    if (!fatalPrefix.test(text)) return false;
    if (
      allowServiceWorkerRegistrationBlocked &&
      /Service Worker registration blocked by Playwright/.test(text)
    ) {
      return false;
    }
    return !isExpectedBasemapNoise(text);
  });
}

// Use the driver's real modal interaction, never call showModal through evaluation.
async function openFilters(page) {
  const dialog = page.getByRole("dialog", { name: "All filters", exact: true });
  if (!(await dialog.isVisible())) {
    await page.getByRole("button", { name: /^All filters(?:\s|$)/ }).click();
  }
  await dialog.waitFor({ state: "visible" });
  return dialog;
}

async function closeFilters(page) {
  const dialog = page.getByRole("dialog", { name: "All filters", exact: true });
  if (await dialog.isVisible()) await dialog.getByRole("button", { name: "Done", exact: true }).click();
  await dialog.waitFor({ state: "hidden" });
}

async function setFilter(page, name, pressed) {
  const dialog = await openFilters(page);
  const option = dialog.getByRole("button", { name, exact: true });
  const current = await option.getAttribute("aria-pressed");
  if (current !== "true" && current !== "false") throw new Error(`Filter ${name} lacks pressed state`);
  if ((current === "true") !== pressed) await option.click();
  await closeFilters(page);
}

// The range uses integer cents, with 101 reserved for the explicit No limit state.
// Operate the native control through keyboard events rather than mutating React state.
async function openPriceFilters(page) {
  const dialog = page.getByRole("dialog", { name: "All filters", exact: true });
  if (!(await dialog.isVisible())) await page.locator('[data-price-filter]').click();
  await dialog.waitFor({ state: "visible" });
  const slider = dialog.getByRole("slider", { name: "Maximum charging price", exact: true });
  await slider.waitFor({ state: "visible" });
  return { dialog, slider };
}

async function setMaxPrice(page, maximum) {
  const { dialog, slider } = await openPriceFilters(page);
  if (maximum === null) {
    await dialog.getByRole("button", { name: "No limit", exact: true }).click();
  } else {
    const cents = Math.round(maximum * 100);
    if (!Number.isFinite(maximum) || maximum < 0 || maximum > 1 || Math.abs(cents / 100 - maximum) > 1e-8) {
      throw new Error(`Maximum charging price must be null or whole cents from 0 to 1: ${maximum}`);
    }
    await slider.focus();
    await page.keyboard.press("Home");
    for (let i = 0; i < cents; i += 1) await page.keyboard.press("ArrowRight");
  }
  await closeFilters(page);
}

module.exports = {
  openFilters,
  openPriceFilters,
  setMaxPrice,
  closeFilters,
  setFilter,
  installDeterministicBasemap,
  isExpectedBasemapNoise,
  isChromiumReadPixelsDiagnostic,
  unexpectedBrowserErrors,
};
