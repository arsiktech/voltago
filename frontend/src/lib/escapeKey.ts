/** A page-level Escape closes the panel only when no other control or dialog owns the key. */
export function escapeClosesPanel(event: KeyboardEvent, panel: Element | null, page: Pick<Document, 'querySelector'> = document): boolean {
  if (event.key !== 'Escape' || event.defaultPrevented || !panel) return false
  if (event.target && panel.contains(event.target as Node)) return true
  return !page.querySelector('dialog[open], [aria-modal="true"]')
}
