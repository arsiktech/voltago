/**
 * MapLibre draws into a WebGL canvas and the browser clears that buffer after the
 * frame is shown. A later DOM capture therefore sees an empty canvas. Reading the
 * canvas inside the render callback, while the buffer is still intact, and placing
 * that image over the canvas keeps tiles and pins in the picture.
 *
 * The map context is left as created (preserveDrawingBuffer stays off). The read
 * happens once, at capture time, and the stand-in image is removed when the
 * capture finishes.
 */

export type SnapshotMap = {
  triggerRepaint(): void
  once(type: 'render' | 'idle', listener: () => void): unknown
  off(type: 'render' | 'idle', listener: () => void): unknown
  getCanvas(): HTMLCanvasElement
  areTilesLoaded(): boolean
}

let activeMap: SnapshotMap | null = null

export function setFeedbackMap(map: SnapshotMap | null): void {
  activeMap = map
}

export function frameHasMapDetail(pixels: Uint8ClampedArray): boolean {
  const step = Math.max(4, Math.floor(pixels.length / 256) * 4) || 4
  let first = -1
  for (let index = 0; index + 3 < pixels.length; index += step) {
    if (pixels[index + 3] < 16) continue
    const color = (pixels[index] << 16) | (pixels[index + 1] << 8) | pixels[index + 2]
    if (first < 0) first = color
    else if (color !== first) return true
  }
  return false
}

function sampleCanvas(canvas: HTMLCanvasElement): boolean {
  const source = canvas
  const probe = document.createElement('canvas')
  probe.width = 32
  probe.height = 32
  const context = probe.getContext('2d', { willReadFrequently: true })
  if (!context) return true
  context.drawImage(source, 0, 0, canvas.width, canvas.height, 0, 0, probe.width, probe.height)
  return frameHasMapDetail(context.getImageData(0, 0, probe.width, probe.height).data)
}

function onceEvent(map: SnapshotMap, type: 'render' | 'idle', timeoutMs: number): Promise<boolean> {
  return new Promise((resolve) => {
    let settled = false
    const finish = (ok: boolean) => {
      if (settled) return
      settled = true
      globalThis.clearTimeout(timer)
      map.off(type, onEvent)
      resolve(ok)
    }
    const onEvent = () => finish(true)
    const timer = globalThis.setTimeout(() => finish(false), timeoutMs)
    map.once(type, onEvent)
  })
}

type Hideable = {
  parentElement: Hideable | null
  getAttribute(name: string): string | null
  setAttribute(name: string, value: string): void
  removeAttribute(name: string): void
  hasAttribute(name: string): boolean
}

/** The capture filter drops script, noscript, template and hidden nodes, and may
 * skip an aria-hidden subtree. The still image stays visible and not hidden.
 * Ancestors are revealed only until the capture returns. */
export function unhideAncestors(start: Hideable | null): () => void {
  const restore: Array<() => void> = []
  let current = start
  while (current) {
    const aria = current.getAttribute('aria-hidden')
    const hidden = current.hasAttribute('hidden')
    if (aria === 'true' || hidden) {
      const node = current
      if (aria === 'true') node.removeAttribute('aria-hidden')
      if (hidden) node.removeAttribute('hidden')
      restore.push(() => {
        if (aria === 'true') node.setAttribute('aria-hidden', 'true')
        if (hidden) node.setAttribute('hidden', '')
      })
    }
    current = current.parentElement
  }
  return () => {
    for (const undo of restore) undo()
  }
}

export function mountMapShot(canvas: HTMLCanvasElement, image: HTMLElement): () => void {
  const previous = canvas.style.visibility
  image.setAttribute('data-fb-map-shot', '')
  image.setAttribute('alt', '')
  image.removeAttribute('hidden')
  image.style.position = 'absolute'
  image.style.inset = '0'
  image.style.width = '100%'
  image.style.height = '100%'
  image.style.objectFit = 'fill'
  image.style.pointerEvents = 'none'
  image.style.visibility = 'visible'
  canvas.insertAdjacentElement('afterend', image)
  // Hide the WebGL canvas for the still, without the hidden attribute: that
  // attribute would also drop a child, and it changes layout. The still itself
  // stays visible so a filter that skips hidden elements keeps the map.
  canvas.style.visibility = 'hidden'
  let removed = false
  return () => {
    if (removed) return
    removed = true
    image.remove()
    canvas.style.visibility = previous
  }
}

function defaultImage(): HTMLImageElement {
  const image = document.createElement('img')
  image.alt = ''
  return image
}

/** Keeps a still of the current map in the page until the returned function runs. */
export async function holdMapSnapshot(
  map: SnapshotMap | null = activeMap,
  options: {
    sample?: (canvas: HTMLCanvasElement) => boolean
    createImage?: () => HTMLElement
  } = {},
): Promise<() => void> {
  if (!map) return () => {}
  const canvas = map.getCanvas()
  if (canvas.width < 2 || canvas.height < 2) return () => {}
  if (!map.areTilesLoaded()) {
    await onceEvent(map, 'idle', 2500)
    if (canvas.width < 2 || canvas.height < 2) return () => {}
  }

  const sample = options.sample ?? sampleCanvas
  const pending = new Promise<string>((resolve) => {
    let settled = false
    const finish = (value: string) => {
      if (settled) return
      settled = true
      globalThis.clearTimeout(timer)
      map.off('render', onRender)
      resolve(value)
    }
    const onRender = () => {
      let next = ''
      try {
        if (sample(canvas)) next = canvas.toDataURL('image/jpeg', 0.82)
      } catch {
        next = ''
      }
      finish(next)
    }
    const timer = globalThis.setTimeout(() => finish(''), 1500)
    map.once('render', onRender)
  })
  map.triggerRepaint()
  const url = await pending
  if (!url) return () => {}

  const image = options.createImage?.() ?? defaultImage()
  if (typeof HTMLImageElement !== 'undefined' && image instanceof HTMLImageElement) image.src = url
  else image.setAttribute('src', url)
  const host = canvas.parentElement ?? null
  const restoreAncestors = unhideAncestors(host)
  const unmount = mountMapShot(canvas, image)
  return () => {
    unmount()
    restoreAncestors()
    map.triggerRepaint()
  }
}
