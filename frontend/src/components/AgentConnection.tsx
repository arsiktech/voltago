import { useId, useRef, useState } from 'react'
import { Check, ChevronRight, Copy, Plug, X } from 'lucide-react'

const SERVER_ADDRESS = 'https://voltago.app/mcp'

/** A permanent discovery shortcut; setup stays behind an explicit tap. */
export function AgentConnection() {
  const id = useId()
  const dialogRef = useRef<HTMLDialogElement>(null)
  const openerRef = useRef<HTMLButtonElement>(null)
  const copyAttempt = useRef(0)
  const [open, setOpen] = useState(false)
  const [copyStatus, setCopyStatus] = useState<'idle' | 'copied' | 'failed'>('idle')

  function close() {
    copyAttempt.current += 1
    dialogRef.current?.close()
    setOpen(false)
    openerRef.current?.focus()
  }

  async function copyAddress() {
    const attempt = ++copyAttempt.current
    try {
      await navigator.clipboard.writeText(SERVER_ADDRESS)
      if (attempt === copyAttempt.current) setCopyStatus('copied')
    } catch {
      if (attempt === copyAttempt.current) setCopyStatus('failed')
    }
  }

  return (
    <>
      <button
        ref={openerRef}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => {
          setCopyStatus('idle')
          dialogRef.current?.showModal()
          setOpen(true)
        }}
        className="agent-connect inline-flex min-h-11 items-center gap-2 rounded-lg text-left text-xs font-semibold text-fg-soft underline decoration-border-strong underline-offset-4 hover:text-fg"
      >
        <Plug size={15} className="shrink-0" aria-hidden="true" />
        <span>Connect AI agent</span>
        <ChevronRight size={13} className="shrink-0" aria-hidden="true" />
      </button>
      <dialog
        ref={dialogRef}
        id={id}
        aria-labelledby={`${id}-title`}
        aria-describedby={`${id}-description`}
        className="agent-connection-dialog fixed inset-x-4 top-auto bottom-4 m-0 max-h-[calc(100dvh-32px)] w-auto max-w-none overflow-hidden rounded-3xl border border-border bg-surface p-0 text-fg shadow-2xl open:flex open:flex-col backdrop:bg-black/40 md:inset-0 md:m-auto md:h-fit md:w-[480px] md:max-w-[calc(100vw-48px)]"
        onCancel={(event) => {
          event.preventDefault()
          close()
        }}
        onClose={() => setOpen(Boolean(dialogRef.current?.open))}
      >
        <div className="flex shrink-0 items-start justify-between gap-4 border-b border-border px-5 py-5 md:px-6">
          <div>
            <h2 id={`${id}-title`} className="text-lg font-semibold">Connect your AI agent</h2>
            <p id={`${id}-description`} className="mt-2 text-sm leading-relaxed text-muted">
              Ask your agent about nearby chargers, prices and charging speeds.
            </p>
          </div>
          <button
            type="button"
            aria-label="Close connection setup"
            onClick={close}
            className="grid size-11 shrink-0 place-items-center rounded-full border border-border-strong bg-surface text-muted hover:bg-surface-2 hover:text-fg"
          >
            <X size={18} aria-hidden="true" />
          </button>
        </div>
        <div className="min-h-0 overflow-y-auto overscroll-contain px-5 py-5 md:px-6">
          <p className="text-sm leading-relaxed text-fg-soft">
            In your agent’s integrations, add a remote MCP server named Volta Go and paste this address.
          </p>
          <label htmlFor={`${id}-address`} className="mt-5 block text-xs font-semibold text-fg-soft">Server address</label>
          <input
            id={`${id}-address`}
            type="text"
            readOnly
            value={SERVER_ADDRESS}
            spellCheck={false}
            autoCapitalize="none"
            onFocus={(event) => event.currentTarget.select()}
            className="mt-2 min-h-11 w-full min-w-0 rounded-xl border border-border-strong bg-surface-2 px-3 font-mono text-sm text-fg"
          />
          <button
            type="button"
            onClick={copyAddress}
            className="mt-3 inline-flex min-h-11 w-full items-center justify-center gap-2 rounded-full border border-brand bg-brand px-4 text-sm font-semibold text-brand-contrast hover:opacity-90"
          >
            {copyStatus === 'copied' ? <Check size={16} aria-hidden="true" /> : <Copy size={16} aria-hidden="true" />}
            {copyStatus === 'copied' ? 'Copied' : 'Copy address'}
          </button>
          <p role="status" className="mt-2 min-h-10 text-xs leading-relaxed text-muted">
            {copyStatus === 'copied'
              ? 'Address copied. Paste it in your agent’s integrations to finish setup.'
              : copyStatus === 'failed'
                ? 'Could not copy. Select the address above and copy it manually.'
                : 'For agents that support remote MCP connections. No sign-in or API key needed.'}
          </p>
          <div className="mt-4 border-t border-border pt-4 text-xs leading-relaxed text-muted">
            <p>Read-only charger lookup. Your agent cannot start charging or take payment.</p>
            <p className="mt-2">Live availability depends on local source coverage.</p>
          </div>
        </div>
      </dialog>
    </>
  )
}
