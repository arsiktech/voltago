/**
 * Browser side of the feedback service. Each app vendors a byte-identical copy of this file (it
 * holds no hostnames or keys; the endpoint comes from the app's environment). Framework-free: each
 * app draws its own button and popover in its own design system and calls these functions.
 *
 * An app may add its own private selectors to the built-in ones. The page is
 * cloned, the clone is masked, and only the masked clone is drawn, so unmasked pixels never exist.
 * A field is hidden when:
 *  - it or a parent carries `data-fb-private` (add it to every name, address, balance you want hidden);
 *  - it matches the app's extra private selectors, an open dialog, or is a form field's value;
 *  - its text looks like an IBAN, email, international phone, card, long number or wallet address.
 * The clone never draws noscript, script, template, a hidden element, or anything that is not displayed.
 * Requires the `modern-screenshot` package (loaded on first capture only).
 */

export type FeedbackMood = "happy" | "neutral" | "unhappy" | "exclamation";
export type FeedbackPin = { x: number; y: number; w: number; h: number };
export type FeedbackCapture = { dataUrl: string; viewport: { width: number; height: number } };
export type FeedbackEndpoint = { url: string; app: string; key: string };
export type FeedbackCaptureConfig = { enabled: boolean; screenshots: boolean; capture: string[] };

export const FEEDBACK_MOODS: readonly FeedbackMood[] = ["happy", "neutral", "unhappy", "exclamation"];
export const FEEDBACK_NOTE_MAX = 2_000;
export const FEEDBACK_IMAGE_MAX_BYTES = 1_000_000;

const MASK_COLOR = "#8a8f98";
const CAPTURE_TIMEOUT_MS = 8_000;
const MAX_NODES = 40_000;
const MAX_WIDTH = 1600;

const PRIVATE_SELECTOR = "[data-fb-private], [role='dialog']:not([data-fb-self]), [role='alertdialog'], [aria-modal='true']:not([data-fb-self])";
const EXCLUDED_SELECTOR = "[data-fb-self], video, audio, iframe, embed, object";
const SKIP_TAGS = new Set(["NOSCRIPT", "SCRIPT", "TEMPLATE"]);
const BLANK_PIXEL = "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==";
const MEDIA_SELECTOR = "img, svg, canvas, picture";
const VALUE_KEPT_INPUT_TYPES = new Set(["checkbox", "radio", "range", "button", "submit", "reset", "color"]);

const TEXT_PATTERNS: readonly RegExp[] = [
  /\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{2,4}){3,8}\b/g, // IBAN
  /[^\s@<>()]+@[^\s@<>()]+\.[^\s@<>()]{2,}/g, // email
  /\+\d[\d\s().-]{7,}\d/g, // international phone
  /\b\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{1,7}\b/g, // card
  /\b\d{9,}\b/g, // account number, UID, order number
  /\b0x[a-fA-F0-9]{16,}\b/g, // EVM address or hash
  /\b(?:T[1-9A-HJ-NP-Za-km-z]{33}|bc1[a-z0-9]{20,}|[13][1-9A-HJ-NP-Za-km-z]{25,34})\b/g, // TRON, Bitcoin address
];

function paintOver(element: Element) {
  for (const node of [element, ...Array.from(element.querySelectorAll("*"))]) {
    const style = (node as HTMLElement).style;
    if (!style) continue;
    style.setProperty("color", "transparent", "important");
    style.setProperty("-webkit-text-fill-color", "transparent", "important");
    style.setProperty("background", MASK_COLOR, "important");
    style.setProperty("border-color", MASK_COLOR, "important");
    style.setProperty("box-shadow", "none", "important");
    style.setProperty("text-shadow", "none", "important");
  }
}

function blankText(root: Node) {
  const walker = root.ownerDocument!.createTreeWalker(root, 4 /* NodeFilter.SHOW_TEXT */);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) (node as Text).data = (node as Text).data.replace(/\S/g, "x");
}

function maskField(field: Element) {
  const view = field.ownerDocument.defaultView!;
  if (field instanceof view.HTMLInputElement) {
    if (VALUE_KEPT_INPUT_TYPES.has(field.type)) return;
    field.value = "";
    field.setAttribute("value", "");
    field.removeAttribute("placeholder");
  } else if (field instanceof view.HTMLTextAreaElement) {
    field.value = "";
    field.textContent = "";
    field.removeAttribute("placeholder");
  } else {
    return;
  }
  paintOver(field);
}

function maskSubtree(element: Element) {
  const view = element.ownerDocument.defaultView!;
  element.querySelectorAll(MEDIA_SELECTOR).forEach(node => node.remove());
  element.querySelectorAll("input, textarea, select").forEach(maskField);
  blankText(element);
  paintOver(element);
  if (element instanceof view.HTMLImageElement) {
    const blank = element.cloneNode(false) as HTMLImageElement;
    blank.removeAttribute("srcset");
    blank.removeAttribute("alt");
    blank.src = BLANK_PIXEL;
    element.replaceWith(blank);
  } else if (element.matches(MEDIA_SELECTOR)) {
    element.replaceChildren();
  }
}

function firstMatch(text: string): { start: number; end: number } | null {
  let best: { start: number; end: number } | null = null;
  for (const pattern of TEXT_PATTERNS) {
    pattern.lastIndex = 0;
    const found = pattern.exec(text);
    if (found && (!best || found.index < best.start)) best = { start: found.index, end: found.index + found[0].length };
  }
  return best;
}

function maskPatterns(root: Element) {
  const walker = root.ownerDocument.createTreeWalker(root, 4 /* NodeFilter.SHOW_TEXT */);
  const nodes: Text[] = [];
  for (let node = walker.nextNode(); node; node = walker.nextNode()) nodes.push(node as Text);
  for (const node of nodes) {
    let current: Text = node;
    for (let match = firstMatch(current.data); match; match = firstMatch(current.data)) {
      if (current.parentElement?.namespaceURI === "http://www.w3.org/2000/svg") {
        current.data = `${current.data.slice(0, match.start)}${"x".repeat(match.end - match.start)}${current.data.slice(match.end)}`;
        if (current.parentElement) paintOver(current.parentElement);
        break;
      }
      const hit = current.splitText(match.start);
      const rest = hit.splitText(match.end - match.start);
      const span = root.ownerDocument.createElement("span");
      span.textContent = hit.data.replace(/\S/g, "x");
      paintOver(span);
      hit.replaceWith(span);
      current = rest;
    }
  }
}

/**
 * True when the screenshot clone must not draw this node: noscript, script, template, anything
 * with the hidden attribute, anything whose display is none, or a descendant of one of those.
 * A browser hides noscript while JavaScript is on, but a cloned document draws that text.
 * `Element` is taken from the node's own window so this can be tested outside a browser.
 */
export function feedbackCloneSkips(node: Node): boolean {
  const view = node.ownerDocument?.defaultView;
  const ElementCtor = view?.Element;
  if (!ElementCtor || !(node instanceof ElementCtor)) return false;
  const element = node as Element;
  for (let current: Element | null = element; current; current = current.parentElement) {
    if (SKIP_TAGS.has(current.tagName.toUpperCase()) || current.hasAttribute("hidden") || current.matches(EXCLUDED_SELECTOR)) return true;
    if (!view) continue;
    try {
      if (view.getComputedStyle(current).display === "none") return true;
    } catch {
      // A node the engine cannot style is still judged by its tag and hidden attribute.
    }
  }
  return false;
}

function dropUndrawn(root: Element) {
  for (const child of [...root.children]) {
    if (feedbackCloneSkips(child)) child.remove();
    else dropUndrawn(child);
  }
}

/** Masks a finished clone in place. Runs before anything is drawn. */
export function maskFeedbackClone(root: Element, extraPrivateSelector?: string) {
  if (feedbackCloneSkips(root)) {
    root.remove();
    return;
  }
  dropUndrawn(root);
  const selector = extraPrivateSelector ? `${PRIVATE_SELECTOR}, ${extraPrivateSelector}` : PRIVATE_SELECTOR;
  if (root.matches(selector)) maskSubtree(root);
  root.querySelectorAll(selector).forEach(maskSubtree);
  root.querySelectorAll("input, textarea").forEach(maskField);
  maskPatterns(root);
}

/** True when the path is on the app's capture list (exact path, or a prefix ending in "/*"). */
export function feedbackCaptureAllowed(patterns: readonly string[], pathname: string): boolean {
  const path = pathname.replace(/\/+$/, "") || "/";
  return patterns.some(pattern => pattern.endsWith("/*")
    ? path === pattern.slice(0, -2) || path.startsWith(pattern.slice(0, -1))
    : path === (pattern.replace(/\/+$/, "") || "/"));
}

/** Asks the service whether this app may send notes and screenshots. Never throws. */
export async function fetchFeedbackConfig(endpoint: FeedbackEndpoint): Promise<FeedbackCaptureConfig> {
  try {
    const response = await fetch(`${endpoint.url}/v1/apps/${endpoint.app}/capture`, { headers: { Accept: "application/json" }, credentials: "omit" });
    const body = await response.json().catch(() => null) as Partial<FeedbackCaptureConfig> | null;
    if (!response.ok || !body) return { enabled: false, screenshots: false, capture: [] };
    return { enabled: body.enabled === true, screenshots: body.screenshots === true, capture: Array.isArray(body.capture) ? body.capture : [] };
  } catch {
    return { enabled: false, screenshots: false, capture: [] };
  }
}

/**
 * A masked JPEG of the visible view, or null when it could not be made in time. Never throws.
 * Mark the app's own feedback popover with `data-fb-self` so it stays out of the picture.
 */
export async function captureFeedbackView(options: { privateSelector?: string } = {}): Promise<FeedbackCapture | null> {
  if (document.querySelectorAll("*").length > MAX_NODES) return null;
  const { domToJpeg } = await import("modern-screenshot");
  const viewport = { width: window.innerWidth, height: window.innerHeight };
  const scrollY = window.scrollY;
  const background = getComputedStyle(document.body).backgroundColor;
  const widthScale = Math.min(2, Math.max(1, MAX_WIDTH / viewport.width));
  const attempt = (scale: number, quality: number) => domToJpeg(document.body, {
    width: viewport.width,
    height: viewport.height,
    scale,
    quality,
    timeout: CAPTURE_TIMEOUT_MS,
    backgroundColor: background && background !== "rgba(0, 0, 0, 0)" ? background : "#ffffff",
    style: { margin: "0", marginTop: `-${scrollY}px` },
    filter: (node: Node) => !feedbackCloneSkips(node),
    onCloneNode: (cloned: Node) => { if (cloned instanceof Element) maskFeedbackClone(cloned, options.privateSelector); },
  });
  try {
    for (const [scale, quality] of [[widthScale, 0.8], [widthScale, 0.6], [1, 0.5]] as const) {
      const dataUrl = await attempt(scale, quality);
      const bytes = Math.floor((dataUrl.length - dataUrl.indexOf(",") - 1) * 3 / 4);
      if (dataUrl.startsWith("data:image/jpeg;base64,") && bytes <= FEEDBACK_IMAGE_MAX_BYTES) return { dataUrl, viewport };
    }
    return null;
  } catch {
    return null;
  }
}

export type FeedbackSendResult = { ok: true; imageDropped: boolean } | { ok: false; error: string };

/** One submission. The path is sent without its query or fragment (they can hold tokens). */
export async function sendFeedback(endpoint: FeedbackEndpoint, input: {
  note: string;
  path: string;
  locale?: string;
  mood?: FeedbackMood;
  shot?: FeedbackCapture | null;
  pin?: FeedbackPin | null;
  context?: Record<string, string>;
}): Promise<FeedbackSendResult> {
  try {
    const response = await fetch(`${endpoint.url}/v1/apps/${endpoint.app}/notes`, {
      method: "POST",
      credentials: "omit",
      headers: { "content-type": "application/json", Accept: "application/json", "x-feedback-key": endpoint.key },
      body: JSON.stringify({
        note: input.note.trim(),
        path: input.path.split(/[?#]/)[0] || "/",
        ...(input.locale ? { locale: input.locale } : {}),
        ...(input.mood ? { mood: input.mood } : {}),
        viewport: input.shot?.viewport ?? { width: window.innerWidth, height: window.innerHeight },
        ...(input.shot ? { screenshot: input.shot.dataUrl } : {}),
        ...(input.shot && input.pin ? { pin: input.pin } : {}),
        ...(input.context ? { context: input.context } : {}),
      }),
    });
    const body = await response.json().catch(() => null) as { error?: unknown; imageDropped?: unknown } | null;
    if (!response.ok) return { ok: false, error: typeof body?.error === "string" ? body.error : `http_${response.status}` };
    return { ok: true, imageDropped: body?.imageDropped === true };
  } catch {
    return { ok: false, error: "network" };
  }
}
