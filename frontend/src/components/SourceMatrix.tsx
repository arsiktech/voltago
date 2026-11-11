import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getJSON, useGeocode } from '../lib/api'
import { useSettledQuery } from '../hooks/useSettledQuery'
import type { GeocodeResult } from '../types'
import {
  DEFAULT_RADIUS_KM,
  RADIUS_OPTIONS_KM,
  STATE_LABEL,
  areaText,
  clockText,
  searchArea,
  type CellState,
  type MatrixCell,
  type MatrixColumn,
  type MatrixLocation,
  type MatrixResponse,
} from '../lib/sourceMatrix'
import './SourceMatrix.css'

function Clock({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{clockText(value)}</dd>
    </div>
  )
}

function CellDetail({ column, location, cell }: { column: MatrixColumn; location: MatrixLocation; cell: MatrixCell }) {
  const fetch = column.last_fetch
  return (
    <section className="matrix-detail" aria-live="polite">
      <h2>
        {column.label} · {location.name}
      </h2>
      <p className={`matrix-chip matrix-${cell.state}`}>{STATE_LABEL[cell.state]}</p>
      {cell.reason && <p>{cell.reason}</p>}
      <dl>
        <Clock label="Source last fetched" value={fetch?.at} />
        {fetch && fetch.parts > 1 && <Clock label={`Oldest of ${fetch.parts} files`} value={fetch.oldest_part_at} />}
        {fetch && fetch.failed_parts > 0 && (
          <div>
            <dt>Failed fetches</dt>
            <dd>
              {fetch.failed_parts} of {fetch.parts} files, using an older cached copy
            </dd>
          </div>
        )}
      </dl>
      {cell.candidate && (
        <p>
          This source lists “{cell.candidate.name}” {cell.candidate.distance_m} m away, but it did not pass the identity
          checks, so it is not treated as the same location.
        </p>
      )}
      {cell.entries?.map((entry, index) => (
        <dl key={index}>
          <Clock label="Listing fetched" value={entry.clocks?.fetched_at} />
          <Clock label="Published by source" value={entry.clocks?.published_at} />
          <Clock label="Updated at source" value={entry.clocks?.source_last_updated} />
          {Object.entries(entry)
            .filter(([key]) => key !== 'clocks')
            .map(([key, value]) => (
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
        <p>This source covers this area, and its latest snapshot does not list this location.</p>
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
                  <span className="matrix-fetch">fetched {col.last_fetch ? col.last_fetch.at.slice(0, 10) : 'unknown'}</span>
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
  const [radiusKm, setRadiusKm] = useState(DEFAULT_RADIUS_KM)
  const geocode = useGeocode(useSettledQuery(place ? '' : query).query)
  const area = place ? searchArea(place, radiusKm) : null
  const bbox = area?.bbox ?? null
  const choosePlace = (result: GeocodeResult) => {
    setRadiusKm(DEFAULT_RADIUS_KM)
    setPlace(result)
  }
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
          if (first) choosePlace(first)
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
                <button type="button" onClick={() => choosePlace(result)}>
                  {result.label}
                </button>
              </li>
            ))}
          </ul>
        )}
        {!place && geocode.isSuccess && geocode.data.results.length === 0 && (
          <p role="status">No place found. Try a town or a shorter address.</p>
        )}
        {area && !area.fromPlace && (
          <>
            <label htmlFor="matrix-radius">Search radius</label>
            <select id="matrix-radius" value={radiusKm} onChange={(event) => setRadiusKm(Number(event.target.value))}>
              {RADIUS_OPTIONS_KM.map((km) => (
                <option key={km} value={km}>
                  {km} km
                </option>
              ))}
            </select>
          </>
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
      {area && matrix.data && matrix.data.locations.length === 0 && (
        <p>No charging locations are known in {areaText(area)}.</p>
      )}
      {area && matrix.data && matrix.data.locations.length > 0 && (
        <>
          <p className="matrix-meta">
            {matrix.data.locations.length} locations nearest first in {areaText(area)}
            {area.capped ? ', the largest area one search covers' : ''}
            {matrix.data.truncated ? ' (more exist; search closer to narrow)' : ''}. Data built{' '}
            {matrix.data.generated_at?.slice(0, 16).replace('T', ' ') ?? 'at an unknown time'} UTC.
          </p>
          <MatrixTable data={matrix.data} />
        </>
      )}
    </main>
  )
}
