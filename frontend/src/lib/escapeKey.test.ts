import { describe, expect, it } from 'vitest'
import { escapeClosesPanel } from './escapeKey'

const inside = { id: 'inside' }
const outside = { id: 'outside' }
const panel = { contains: (node: unknown) => node === inside } as unknown as Element
const page = (openDialog: boolean) => ({ querySelector: () => (openDialog ? {} : null) }) as unknown as Pick<Document, 'querySelector'>
const key = (target: unknown, init: { key?: string; defaultPrevented?: boolean } = {}) =>
  ({ key: init.key ?? 'Escape', defaultPrevented: init.defaultPrevented ?? false, target }) as unknown as KeyboardEvent

describe('Escape for the vehicle panel', () => {
  it('closes from inside the panel, even while another dialog is open', () => {
    expect(escapeClosesPanel(key(inside), panel, page(false))).toBe(true)
    expect(escapeClosesPanel(key(inside), panel, page(true))).toBe(true)
  })
  it('still closes from the page when nothing else owns the key', () => {
    expect(escapeClosesPanel(key(outside), panel, page(false))).toBe(true)
  })
  it('leaves Escape to another open dialog', () => {
    expect(escapeClosesPanel(key(outside), panel, page(true))).toBe(false)
  })
  it('ignores a key press another control already handled', () => {
    expect(escapeClosesPanel(key(inside, { defaultPrevented: true }), panel, page(false))).toBe(false)
    expect(escapeClosesPanel(key(outside, { defaultPrevented: true }), panel, page(false))).toBe(false)
  })
  it('ignores other keys and a missing panel', () => {
    expect(escapeClosesPanel(key(inside, { key: 'Enter' }), panel, page(false))).toBe(false)
    expect(escapeClosesPanel(key(inside), null, page(false))).toBe(false)
  })
})
