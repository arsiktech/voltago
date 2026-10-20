import type { Station } from '../types'
import { cardCautions } from '../lib/stationPresentation'
import { tariffNotes, tariffWindow } from '../lib/format'

/** Always available, including while optional source details are loading or offline. */
export function StationConditions({ st }: { st: Station }) {
  const cautions = cardCautions(st).filter(item => item.label !== 'Time-limited price')
  const notes = tariffNotes(st)
  const window = tariffWindow(st)
  return <section className="mt-3 space-y-2 border-t border-border pt-3 text-xs text-muted" aria-label="Access, parking and price conditions">
    {cautions.length > 0 && <ul className="space-y-2">
      {cautions.map(item => <li key={item.label}><strong className="font-semibold text-fg-soft">{item.label}. </strong>{item.detail}</li>)}
    </ul>}
    {st.parking?.label && <p data-route-fact="parking" aria-label={`Parking: ${st.parking.label}`}><strong className="font-semibold text-fg-soft">Parking: </strong>{st.parking.label}{st.parking.cost_hint ? `. ${st.parking.cost_hint}` : ''}</p>}
    {st.access?.label && <p>Access: {st.access.label}</p>}
    {window && <p>{window.currentlyCheap ? 'Current price window: ' : 'Price window, not active now: '}{window.label}</p>}
    {notes.length > 0 && <ul className="list-disc space-y-1 pl-4">{notes.map(note => <li key={note}>{note}</li>)}</ul>}
  </section>
}
