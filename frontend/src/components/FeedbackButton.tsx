import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import { CircleAlert, FaceNeutral, FaceSlightlyFrowning, FaceSlightlySmiling, MessageSquare, type LucideIcon } from 'lucide-react'
import {
  captureFeedbackView,
  FEEDBACK_MOODS,
  FEEDBACK_NOTE_MAX,
  feedbackCaptureAllowed,
  fetchFeedbackConfig,
  sendFeedback,
  type FeedbackCapture,
  type FeedbackCaptureConfig,
  type FeedbackEndpoint,
  type FeedbackMood,
} from '../lib/feedback-client'
import { feedbackButtonEnabled, feedbackEndpoint, feedbackSubmission } from '../lib/feedback'
import { holdMapSnapshot } from '../lib/mapSnapshot'

const MOOD_ICON: Record<FeedbackMood, LucideIcon> = {
  happy: FaceSlightlySmiling,
  neutral: FaceNeutral,
  unhappy: FaceSlightlyFrowning,
  exclamation: CircleAlert,
}

const MOOD_LABEL: Record<FeedbackMood, string> = {
  happy: 'Happy',
  neutral: 'Neutral',
  unhappy: 'Unhappy',
  exclamation: 'Exclamation',
}

function FeedbackComposer({
  endpoint,
  config,
  theme,
}: {
  endpoint: FeedbackEndpoint
  config: FeedbackCaptureConfig
  theme: 'light' | 'dark'
}) {
  const panelId = useId()
  const rootRef = useRef<HTMLDivElement>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const noteRef = useRef<HTMLTextAreaElement>(null)
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState('')
  const [mood, setMood] = useState<FeedbackMood | null>(null)
  const [shot, setShot] = useState<FeedbackCapture | null>(null)
  const [dropped, setDropped] = useState(false)
  const [sending, setSending] = useState(false)
  const [toast, setToast] = useState<string | null>(null)
  const allowShot = config.screenshots && feedbackCaptureAllowed(config.capture, window.location.pathname)
  const closePopover = useCallback(() => {
    setOpen(false)
    setShot(null)
    setDropped(false)
    buttonRef.current?.focus()
  }, [])

  useEffect(() => {
    if (!toast) return
    const timer = window.setTimeout(() => setToast(null), 2400)
    return () => window.clearTimeout(timer)
  }, [toast])

  useEffect(() => {
    if (!open) return
    noteRef.current?.focus()
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      event.stopImmediatePropagation()
      closePopover()
    }
    const onPointer = (event: PointerEvent) => {
      if (rootRef.current?.contains(event.target as Node)) return
      closePopover()
    }
    document.addEventListener('keydown', onKey)
    document.addEventListener('pointerdown', onPointer)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('pointerdown', onPointer)
    }
  }, [open, closePopover])

  useEffect(() => {
    if (!open || !allowShot || dropped) return
    let live = true
    const handle = { release: () => {} }
    void (async () => {
      const release = await holdMapSnapshot()
      handle.release = release
      if (!live) {
        release()
        return
      }
      try {
        const next = await captureFeedbackView()
        if (live) setShot(next)
      } finally {
        release()
        handle.release = () => {}
      }
    })()
    return () => {
      live = false
      handle.release()
    }
  }, [open, allowShot, dropped])

  async function submit() {
    const note = draft.trim()
    if (!note || sending) return
    setSending(true)
    const result = await sendFeedback(endpoint, feedbackSubmission({
      note: draft,
      path: `${window.location.pathname}${window.location.search}${window.location.hash}`,
      theme,
      language: navigator.language,
      mood,
      shot,
      screenshots: config.screenshots,
      capture: config.capture,
    }))
    setSending(false)
    if (!result.ok) {
      setToast('Not sent')
      return
    }
    setDraft('')
    setMood(null)
    setToast('Sent')
    closePopover()
  }

  return (
    <div ref={rootRef} className="feedback-anchor">
      <button
        ref={buttonRef}
        type="button"
        className="feedback-button"
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-controls={open ? panelId : undefined}
        onClick={() => (open ? closePopover() : setOpen(true))}
      >
        <MessageSquare size={16} aria-hidden="true" />
        <span>Feedback</span>
      </button>
      {open && (
        <form
          id={panelId}
          data-fb-self
          role="dialog"
          aria-label="Feedback"
          className="feedback-popover"
          onSubmit={(event) => {
            event.preventDefault()
            void submit()
          }}
        >
          <textarea
            ref={noteRef}
            className="feedback-note"
            aria-label="Feedback"
            maxLength={FEEDBACK_NOTE_MAX}
            value={draft}
            autoComplete="off"
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key !== 'Enter' || (!event.metaKey && !event.ctrlKey)) return
              event.preventDefault()
              if (!draft.trim() || sending) return
              event.currentTarget.form?.requestSubmit()
            }}
          />
          <div className="feedback-row">
            <div className="feedback-moods">
              {FEEDBACK_MOODS.map((item) => {
                const Icon = MOOD_ICON[item]
                return (
                  <button
                    key={item}
                    type="button"
                    className="feedback-mood"
                    aria-label={MOOD_LABEL[item]}
                    aria-pressed={mood === item}
                    onClick={() => setMood((current) => (current === item ? null : item))}
                  >
                    <Icon size={16} aria-hidden="true" />
                  </button>
                )
              })}
            </div>
            <div className="feedback-end">
              {shot && (
                <span className="feedback-shot">
                  <img src={shot.dataUrl} alt="" />
                  <button type="button" aria-label="Remove screenshot" onClick={() => { setShot(null); setDropped(true) }}>×</button>
                </span>
              )}
              <button type="submit" className="feedback-send" disabled={sending || !draft.trim()}>Send</button>
            </div>
          </div>
        </form>
      )}
      {toast && <p className="feedback-toast" data-fb-self role="status">{toast}</p>}
    </div>
  )
}

export function FeedbackButton({ theme }: { theme: 'light' | 'dark' }) {
  const endpoint = useMemo(() => feedbackEndpoint(), [])
  const [config, setConfig] = useState<FeedbackCaptureConfig | null>(null)

  useEffect(() => {
    if (!endpoint) return
    let live = true
    fetchFeedbackConfig(endpoint).then((next) => {
      if (live) setConfig(next)
    })
    return () => {
      live = false
    }
  }, [endpoint])

  if (!endpoint || !config || !feedbackButtonEnabled(endpoint, config.enabled)) return null
  return <FeedbackComposer endpoint={endpoint} config={config} theme={theme} />
}
