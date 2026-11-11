import {
  feedbackCaptureAllowed,
  type FeedbackCapture,
  type FeedbackEndpoint,
  type FeedbackMood,
} from './feedback-client'

export type FeedbackEnv = {
  VITE_FEEDBACK_URL?: string
  VITE_FEEDBACK_APP?: string
  VITE_FEEDBACK_KEY?: string
}

function clean(value: string | undefined): string {
  return typeof value === 'string' ? value.trim() : ''
}

/** Missing URL, app, or key keeps the control off. An empty string counts as missing. */
export function feedbackEndpointFrom(env: FeedbackEnv): FeedbackEndpoint | null {
  const url = clean(env.VITE_FEEDBACK_URL).replace(/\/+$/, '')
  const app = clean(env.VITE_FEEDBACK_APP)
  const key = clean(env.VITE_FEEDBACK_KEY)
  if (!url || !app || !key) return null
  return { url, app, key }
}

export function feedbackEndpoint(): FeedbackEndpoint | null {
  return feedbackEndpointFrom({
    VITE_FEEDBACK_URL: import.meta.env.VITE_FEEDBACK_URL,
    VITE_FEEDBACK_APP: import.meta.env.VITE_FEEDBACK_APP,
    VITE_FEEDBACK_KEY: import.meta.env.VITE_FEEDBACK_KEY,
  })
}

/** The button stays hidden until the service says this app may send. */
export function feedbackButtonEnabled(endpoint: FeedbackEndpoint | null, enabled: boolean | undefined): boolean {
  return endpoint !== null && enabled === true
}

const LOCALE = /^[a-z]{2}(?:-[A-Z]{2})?$/

export function feedbackLocale(language: string | undefined): string | undefined {
  if (!language) return undefined
  const [lang, region] = language.replace('_', '-').split('-')
  if (!lang) return undefined
  const locale = region ? `${lang.toLowerCase()}-${region.toUpperCase()}` : lang.toLowerCase()
  return LOCALE.test(locale) ? locale : undefined
}

export type FeedbackSubmission = {
  note: string
  path: string
  locale?: string
  mood?: FeedbackMood
  shot?: FeedbackCapture
  context: Record<string, string>
}

/** Note, path, optional mood and screenshot, plus theme. No name, email, or account id. */
export function feedbackSubmission(input: {
  note: string
  path: string
  theme: 'light' | 'dark'
  language?: string
  mood?: FeedbackMood | null
  shot?: FeedbackCapture | null
  screenshots: boolean
  capture: readonly string[]
}): FeedbackSubmission {
  const pathOnly = input.path.split(/[?#]/)[0] || '/'
  const locale = feedbackLocale(input.language)
  const shot = input.screenshots && input.shot && feedbackCaptureAllowed(input.capture, pathOnly)
    ? input.shot
    : undefined
  return {
    note: input.note.trim(),
    path: input.path,
    ...(locale ? { locale } : {}),
    ...(input.mood ? { mood: input.mood } : {}),
    ...(shot ? { shot } : {}),
    context: { theme: input.theme },
  }
}
