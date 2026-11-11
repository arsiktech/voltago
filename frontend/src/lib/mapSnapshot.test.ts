import { describe, expect, it, vi } from 'vitest'
import { frameHasMapDetail, holdMapSnapshot, mountMapShot, unhideAncestors, type SnapshotMap } from './mapSnapshot'

function pixels(colors: Array<[number, number, number, number]>): Uint8ClampedArray {
  const data = new Uint8ClampedArray(colors.length * 4)
  colors.forEach(([r, g, b, a], index) => {
    data.set([r, g, b, a], index * 4)
  })
  return data
}

function fakeMap(detail: boolean) {
  const listeners: Record<string, Array<() => void>> = {}
  const mounted: Array<{ attrs: Record<string, string>; removed: boolean }> = []
  const canvas = {
    width: 80,
    height: 60,
    style: { visibility: '' },
    insertAdjacentElement(_position: 'afterend', element: HTMLElement) {
      mounted.push(element as unknown as { attrs: Record<string, string>; removed: boolean })
      return element
    },
    toDataURL() {
      return 'data:image/jpeg;base64,bWFw'
    },
  }
  const map = {
    repaints: 0,
    areTilesLoaded: () => true,
    triggerRepaint() {
      this.repaints += 1
      for (const listener of [...(listeners.render ?? [])]) listener()
    },
    once(type: string, listener: () => void) {
      ;(listeners[type] ??= []).push(listener)
    },
    off(type: string, listener: () => void) {
      listeners[type] = (listeners[type] ?? []).filter((item) => item !== listener)
    },
    getCanvas: () => canvas,
  }
  return { map, canvas, mounted, detail, listeners }
}

describe('map snapshot', () => {
  it('treats a flat or empty frame as having no map detail', () => {
    expect(frameHasMapDetail(pixels([[238, 240, 237, 255], [238, 240, 237, 255], [238, 240, 237, 255]]))).toBe(false)
    expect(frameHasMapDetail(pixels([[0, 0, 0, 0], [0, 0, 0, 0]]))).toBe(false)
    expect(frameHasMapDetail(pixels([[120, 160, 90, 255], [40, 90, 170, 255]]))).toBe(true)
  })

  it('covers the canvas with the still and restores it exactly once', () => {
    const canvas = {
      style: { visibility: 'visible' },
      insertAdjacentElement: vi.fn(),
    } as unknown as HTMLCanvasElement
    const image = documentCreate()
    const restore = mountMapShot(canvas, image.element as unknown as HTMLElement)
    expect(canvas.style.visibility).toBe('hidden')
    expect(image.element.attrs['data-fb-map-shot']).toBe('')
    expect(image.element.attrs.alt).toBe('')
    expect(image.element.attrs.hidden).toBeUndefined()
    expect(image.element.style.visibility).toBe('visible')
    restore()
    restore()
    expect(canvas.style.visibility).toBe('visible')
    expect(image.removed).toBe(1)
  })

  it('reads the frame from the render callback and does not keep the map repainting', async () => {
    const fake = fakeMap(true)
    const images: ReturnType<typeof documentCreate>[] = []
    const release = await holdMapSnapshot(fake.map as unknown as SnapshotMap, {
      sample: () => fake.detail,
      createImage: () => {
        const image = documentCreate()
        images.push(image)
        return image.element as unknown as HTMLElement
      },
    })
    expect(fake.map.repaints).toBe(1)
    expect(fake.canvas.style.visibility).toBe('hidden')
    expect(images[0]?.element.attrs.src).toBe('data:image/jpeg;base64,bWFw')
    const during = fake.map.repaints
    release()
    expect(fake.canvas.style.visibility).toBe('')
    expect(fake.map.repaints).toBe(during + 1)
    expect(fake.listeners.render ?? []).toHaveLength(0)
  })

  it('reveals a hidden map ancestor only while the still is up', () => {
    const ancestor = {
      parentElement: null,
      attrs: { 'aria-hidden': 'true', hidden: '' } as Record<string, string>,
      getAttribute(name: string) { return this.attrs[name] ?? null },
      setAttribute(name: string, value: string) { this.attrs[name] = value },
      removeAttribute(name: string) { delete this.attrs[name] },
      hasAttribute(name: string) { return name in this.attrs },
    }
    const restore = unhideAncestors(ancestor)
    expect(ancestor.attrs['aria-hidden']).toBeUndefined()
    expect(ancestor.attrs.hidden).toBeUndefined()
    restore()
    expect(ancestor.attrs['aria-hidden']).toBe('true')
    expect(ancestor.attrs.hidden).toBe('')
  })

  it('leaves the canvas alone when the frame has no detail', async () => {
    const fake = fakeMap(false)
    const release = await holdMapSnapshot(fake.map as unknown as SnapshotMap, {
      sample: () => false,
      createImage: () => {
        throw new Error('no image')
      },
    })
    expect(fake.canvas.style.visibility).toBe('')
    expect(fake.mounted).toHaveLength(0)
    release()
    expect(fake.map.repaints).toBe(1)
  })
})

function documentCreate() {
  const element = {
    attrs: {} as Record<string, string>,
    style: {} as Record<string, string>,
    removed: 0,
    setAttribute(name: string, value: string) {
      this.attrs[name] = value
    },
    removeAttribute(name: string) {
      delete this.attrs[name]
    },
    remove() {
      this.removed += 1
    },
  }
  return { element, get removed() { return element.removed } }
}
