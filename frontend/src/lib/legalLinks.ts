/** The public Privacy, Terms and Support pages, served as static pages by the same host.
 * Google Play requires the privacy policy to be reachable from inside the app. */
export const LEGAL_LINKS = [
  { href: '/privacy', label: 'Privacy' },
  { href: '/terms', label: 'Terms' },
  { href: '/support', label: 'Support' },
] as const
