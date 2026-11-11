/**
 * Browser side of the feedback service. Each app vendors a byte-identical copy of this file (it
 * holds no hostnames or keys; the endpoint comes from the app's environment). Framework-free: each
 * app draws its own button and popover in its own design system and calls these functions.
 *
 * The screenshot is the page as the user sees it. Nothing is painted over: names, typed text and
 * figures stay. The clone leaves out noscript, script, template, hidden elements, and anything
 * that is not displayed (a browser hides noscript while JavaScript is on, but a clone would draw it).
 * Mark the feedback popover with `data-fb-self` so the form itself stays out of the picture.
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

const CAPTURE_TIMEOUT_MS = 8_000;
const MAX_NODES = 40_000;
const MAX_WIDTH = 1600;

const EXCLUDED_SELECTOR = "[data-fb-self]";
const SKIP_TAGS = new Set(["NOSCRIPT", "SCRIPT", "TEMPLATE"]);

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

/** Drops elements the user is not shown. Does not paint over or rewrite anything that stays. */
export function maskFeedbackClone(root: Element) {
  if (feedbackCloneSkips(root)) {
    root.remove();
    return;
  }
  dropUndrawn(root);
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
 * A JPEG of the visible view, or null when it could not be made in time. Never throws.
 * Mark the app's own feedback popover with `data-fb-self` so it stays out of the picture.
 */
export async function captureFeedbackView(): Promise<FeedbackCapture | null> {
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
    onCloneNode: (cloned: Node) => { if (cloned instanceof Element) maskFeedbackClone(cloned); },
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
