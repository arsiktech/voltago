import { LEGAL_LINKS } from '../lib/legalLinks'

/** Plain same-origin links: the Android app (a Trusted Web Activity) keeps them inside
 * the app, and each page links back to the map. */
export function LegalLinks() {
  return (
    <nav aria-label="Legal and support" className="legal-links flex items-center gap-x-3">
      {LEGAL_LINKS.map((link) => (
        <a
          key={link.href}
          href={link.href}
          className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-lg text-xs font-medium text-muted hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand"
        >
          {link.label}
        </a>
      ))}
    </nav>
  )
}
