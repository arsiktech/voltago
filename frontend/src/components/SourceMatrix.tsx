import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getJSON, useGeocode } from '../lib/api'
import type { GeocodeResult } from '../types'
import {
  STATE_LABEL,
  bboxAround,
  type CellState,
  type MatrixCell,
  type MatrixColumn,
  type MatrixLocation,
  type MatrixResponse,
} from '../lib/sourceMatrix'
import './SourceMatrix.css'

function CellDetail({ column, location, cell }: { column: MatrixColumn; location: MatrixLocation; cell: MatrixCell }) {
  return (
    <section className="matrix-detail" aria-live="polite">
      <h2>
        {column.label} · {location.name}
      </h2>
      <p className={`matrix-chip matrix-${cell.state}`}>{STATE_LABEL[cell.state]}</p>
      {cell.reason && <p>{cell.reason}</p>}
      {cell.candidate && (
        <p>
          This source lists “{cell.candidate.name}” {cell.candidate.distance_m} m away, but it did not pass the identity
          checks, so it is not treated as the same location.
        </p>
      )}
      {cell.entries?.map((entry, index) => (
        <dl key={index}>
          {Object.entries(entry).map(([key, value]) => (
            <div key={key}>
              <dt>{key.replaceAll('_', ' ')}</dt>
              <dd>
                {typeof value === 'string' && /^https:\/\//.test(value) ? (
                  <a href={value} target="_blank" rel="noopener noreferrer">
                    {value}
                  </a>
                ) : (
                  String(value)
                )}
              </dd>
            </div>
          ))}
        </dl>
      ))}
      {cell.state === 'not_listed' && (
        <p>This source covers this area and its latest snapshot is current, and it does not list this location.</p>
      )}
    </section>
  )
}

export function MatrixTable({ data }: { data: MatrixResponse }) {
  const [selected, setSelected] = useState<{ location: string; column: string } | null>(null)
  const location = data.locations.find((item) => item.id === selected?.location)
  const column = data.columns.find((item) => item.id === selected?.column)
  return (
    <>
      <div className="matrix-scroll" role="region" aria-label="Location by source coverage" tabIndex={0}>
        <table className="matrix-table">
          <thead>
            <tr>
              <th scope="col">Location</th>
              {data.columns.map((col) => (
                <th scope="col" key={col.id} title={col.degraded ?? undefined}>
                  {col.label}
                  {col.degraded ? ' (stale)' : ''}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.locations.map((loc) => (
              <tr key={loc.id}>
                <th scope="row">
                  <span className="matrix-name">{loc.name}</span>
                  <span className="matrix-address">{[loc.address, loc.city].filter(Boolean).join(' · ')}</span>
                </th>
                {data.columns.map((col) => {
                  const cell = loc.cells[col.id]
                  const active = selected?.location === loc.id && selected.column === col.id
                  return (
                    <td key={col.id}>
                      <button
                        type="button"
                        className={`matrix-cell matrix-${cell.state}${active ? ' is-active' : ''}`}
                        aria-label={`${col.label}: ${STATE_LABEL[cell.state]}`}
                        aria-pressed={active}
                        onClick={() => setSelected({ location: loc.id, column: col.id })}
                      />
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {location && column && <CellDetail column={column} location={location} cell={location.cells[column.id]} />}
    </>
  )
}

export default function SourceMatrix() {
  const [query, setQuery] = useState('')
  const [place, setPlace] = useState<GeocodeResult | null>(null)
  const geocode = useGeocode(place ? '' : query)
  const bbox = place ? bboxAround(place) : null
  const matrix = useQuery({
    queryKey: ['location-sources', bbox],
    queryFn: ({ signal }) => getJSON<MatrixResponse>(`/api/location-sources?bbox=${bbox}`, 20000, signal),
    enabled: Boolean(bbox),
  })
  return (
    <main className="matrix-page">
      <header>
        <h1>Charger source coverage</h1>
        <p>
          Which public sources list each charging location. A cell shows coverage, not availability, price or whether
          charging is free.
        </p>
      </header>
      <form
        className="matrix-search"
        onSubmit={(event) => {
          event.preventDefault()
          const first = geocode.data?.results?.[0]
          if (first) setPlace(first)
        }}
      >
        <label htmlFor="matrix-query">Address or place</label>
        <input
          id="matrix-query"
          value={place ? place.label : query}
          onChange={(event) => {
            setPlace(null)
            setQuery(event.target.value)
          }}
          placeholder="Calle Mayor 1, Madrid"
          autoComplete="off"
        />
        {!place && (geocode.data?.results?.length ?? 0) > 0 && (
          <ul className="matrix-suggestions">
            {geocode.data!.results.slice(0, 5).map((result) => (
              <li key={`${result.lat},${result.lon}`}>
                <button type="button" onClick={() => setPlace(result)}>
                  {result.label}
                </button>
              </li>
            ))}
          </ul>
        )}
        {!place && geocode.isSuccess && geocode.data.results.length === 0 && (
          <p role="status">No place found. Try a town or a shorter address.</p>
        )}
      </form>
      <ul className="matrix-legend" aria-label="Cell states">
        {(Object.keys(STATE_LABEL) as CellState[]).map((state) => (
          <li key={state}>
            <span className={`matrix-cell matrix-${state}`} aria-hidden="true" /> {STATE_LABEL[state]}
          </li>
        ))}
      </ul>
      {matrix.isPending && bbox && <p>Loading sources…</p>}
      {matrix.isError && <p role="alert">The coverage table could not be loaded. Try again.</p>}
      {matrix.data && matrix.data.locations.length === 0 && <p>No charging locations are known within about 700 m.</p>}
      {matrix.data && matrix.data.locations.length > 0 && (
        <>
          <p className="matrix-meta">
            {matrix.data.locations.length} locations nearest first
            {matrix.data.truncated ? ' (more exist; search closer to narrow)' : ''}. Data built{' '}
            {matrix.data.generated_at?.slice(0, 16).replace('T', ' ') ?? 'at an unknown time'} UTC.
          </p>
          <MatrixTable data={matrix.data} />
        </>
      )}
    </main>
  )
}
