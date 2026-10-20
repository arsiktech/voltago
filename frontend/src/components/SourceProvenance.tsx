import type { ReactNode } from 'react'
import { motion, useReducedMotion } from 'framer-motion'
import { ExternalLink, Plug, ShieldCheck, SquareParking, TriangleAlert } from 'lucide-react'
import type { Station } from '../types'
import { connectorInventoryText, corroboration, publicWebUrl, sourcePageConnectors, sourcePhotoLinks, tariffNotes } from '../lib/format'
import { fadeSlide } from '../lib/motion'

function Ext({ href, children }: { href?: string | null; children: ReactNode }) {
  const safeHref = publicWebUrl(href)
  if (!safeHref) return <span className="text-faint">{children}</span>
  return (
    <a
      href={safeHref}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex min-h-11 items-center gap-1 font-medium text-brand-strong hover:underline"
    >
      {children}
      <ExternalLink size={11} />
    </a>
  )
}

export function SourceProvenance({ st }: { st: Station }) {
  const reduceMotion = useReducedMotion()
  const charge = st.charge_claim
  const parking = st.parking_claim
  const conflict = st.claim_conflict
  const sources = st.sources || []
  const corr = corroboration(st)
  const photoLinks = sourcePhotoLinks(st)
  const details = st.source_details
  const contact = details?.contact
  const conditions = tariffNotes(st)
  const pageConnectors = sourcePageConnectors(st)

  return (
    <motion.div
      variants={fadeSlide(Boolean(reduceMotion), 6)}
      initial="hidden"
      animate="show"
      className="mt-3 space-y-3 border-t border-border pt-3"
    >
      {st.decision?.reason && (
        <div className="text-xs text-muted">
          <span className="font-semibold uppercase tracking-wide">Why “{st.decision.label}”:</span>{' '}
          {st.decision.reason}
        </div>
      )}
      {conflict?.summary && (
        <div className="flex items-start gap-2 rounded-lg border border-[color-mix(in_oklab,var(--danger)_36%,transparent)] bg-[color-mix(in_oklab,var(--danger)_12%,transparent)] px-2.5 py-2 text-xs text-danger">
          <TriangleAlert size={14} className="mt-0.5 flex-none" />
          <span>{conflict.summary}</span>
        </div>
      )}

      {details && (
        <section className="space-y-1.5 text-sm text-fg-soft" aria-label="Charger information">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">Charger information</h3>
          {details.description && <p className="whitespace-pre-line">{details.description}</p>}
          {details.opening_hours && <p><span className="font-medium">Opening hours:</span> {details.opening_hours}</p>}
          {details.access && <p><span className="font-medium">Access:</span> {details.access}</p>}
          {contact && (
            <div className="space-y-1">
              <p className="font-medium">Source contact</p>
              {[contact.name, contact.phone, contact.email].filter(Boolean).map((value, index) => <p key={index}>{value}</p>)}
              {publicWebUrl(contact.website) && <Ext href={contact.website}>Contact website</Ext>}
            </div>
          )}
          {details.notes && details.notes.length > 0 && <ul className="list-disc space-y-1 pl-4">
            {details.notes.map((note, index) => <li key={index}>{note}</li>)}
          </ul>}
          {publicWebUrl(details.source_url) && <div className="inline-flex min-h-11 items-center text-xs"><Ext href={details.source_url}>View information source</Ext></div>}
        </section>
      )}

      {pageConnectors.length > 0 && (
        <section className="space-y-1.5 text-sm text-fg-soft" aria-label="Source-page connectors">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">Source-page connectors</h3>
          {details?.source_name && <p className="text-xs text-muted">{details.source_name}</p>}
          {details?.connector_count != null && Number.isInteger(details.connector_count) && details.connector_count > 0 && (
            <p className="text-xs text-muted">Published sockets: {details.connector_count}</p>
          )}
          <ul className="space-y-1">
            {pageConnectors.map((connector, index) => <li key={index}>
              {publicWebUrl(connector.source_url)
                ? <Ext href={connector.source_url}>{connectorInventoryText(connector)}</Ext>
                : connectorInventoryText(connector)}
            </li>)}
          </ul>
        </section>
      )}

      {st.tariff_details && (st.tariff_details.text || conditions.length > 0) && (
        <section className="space-y-1.5 text-sm text-fg-soft" aria-label="Charging tariff">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">Charging tariff</h3>
          {st.tariff_details.text && <p className="whitespace-pre-line">{st.tariff_details.text}</p>}
          {conditions.length > 0 && <ul className="list-disc space-y-1 pl-4">
            {conditions.map((condition) => <li key={condition}>{condition}</li>)}
          </ul>}
          {publicWebUrl(st.tariff_details.source_url) && <div className="inline-flex min-h-11 items-center text-xs"><Ext href={st.tariff_details.source_url}>View tariff source</Ext></div>}
        </section>
      )}

      {charge && (
        <div className="text-sm">
          <div className="mb-0.5 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted">
            <Plug size={12} /> Charging claim
          </div>
          <p className="text-fg-soft">{charge.summary}</p>
          <div className="mt-0.5 text-xs text-muted">
            via {charge.source || 'source'} · <Ext href={charge.url}>view source</Ext>
          </div>
        </div>
      )}

      {parking && (
        <div className="text-sm">
          <div className="mb-0.5 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted">
            <SquareParking size={12} /> Parking claim
          </div>
          <p className="text-fg-soft">{parking.summary}</p>
          <div className="mt-0.5 text-xs text-muted">
            via {parking.source || 'source'} · <Ext href={parking.url}>view source</Ext>
          </div>
        </div>
      )}

      {st.access?.note && (
        <div className="flex items-start gap-2 text-xs text-muted">
          <ShieldCheck size={13} className="mt-0.5 flex-none" />
          <span>
            <span className="font-medium text-fg-soft">{st.access.label}.</span> {st.access.note}
          </span>
        </div>
      )}

      {st.insight && <p className="text-xs leading-relaxed text-muted">{st.insight}</p>}

      {photoLinks.length > 0 && (
        <section className="space-y-1 text-xs text-muted" aria-label="Source photos">
          <h3 className="font-semibold uppercase tracking-wide">Source photos</h3>
          {photoLinks.map((photo, index) => <div key={`${photo.href}:${index}`}>
            {photo.caption && <p>{photo.caption}</p>}
            <div className="inline-flex min-h-11 items-center"><Ext href={photo.href}>View photo at {photo.source}</Ext></div>
          </div>)}
        </section>
      )}

      {corr.count >= 2 && (
        <div className="flex items-start gap-2 rounded-lg border border-[color-mix(in_oklab,var(--brand)_30%,transparent)] bg-[color-mix(in_oklab,var(--brand)_10%,transparent)] px-2.5 py-2 text-xs">
          <ShieldCheck size={14} className="mt-0.5 flex-none text-brand-strong" />
          <span className="text-fg-soft">
            <span className="font-semibold">Corroboration: {corr.count} independent sources</span>{' '}
            agree on price &amp; location{corr.families.length ? ` (${corr.families.join(', ')})` : ''}.
            This raises confidence the station exists — it doesn&apos;t make it live.
          </span>
        </div>
      )}

      {sources.length > 0 && (
        <div>
          <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">
            Public sources ({sources.length})
          </div>
          <ul className="space-y-1.5">
            {sources.map((s, i) => (
              <motion.li
                key={i}
                className="text-xs"
                initial={reduceMotion ? { opacity: 0 } : { opacity: 0, x: -4 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ duration: 0.14, delay: Math.min(i, 6) * 0.025 }}
              >
                <Ext href={s.url}>{s.name || 'source'}</Ext>
                {s.evidence && <div className="mt-0.5 text-faint">{s.evidence}</div>}
                {(s.attribution || s.license) && (
                  <div className="mt-0.5 text-faint">
                    {s.attribution ? <>Attribution: {s.attribution}</> : null}
                    {s.attribution && s.license ? ' · ' : null}
                    {s.license ? <Ext href={s.license_url}>{s.license}</Ext> : null}
                  </div>
                )}
                {s.transformation && (
                  <div className="mt-0.5 text-faint">Transformation: {s.transformation}</div>
                )}
                {s.source_last_updated && (
                  <div className="mt-0.5 text-faint">
                    Source updated: {s.source_last_updated}
                  </div>
                )}
                {!s.source_last_updated && s.source_fetched_at && (
                  <div className="mt-0.5 text-faint">
                    Retrieved: {s.source_fetched_at}
                  </div>
                )}
              </motion.li>
            ))}
          </ul>
        </div>
      )}
    </motion.div>
  )
}
