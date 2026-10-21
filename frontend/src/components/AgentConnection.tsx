import { useId, useState } from 'react'
import { Check, Copy } from 'lucide-react'
import prompt from '../../../contracts/agent_connection_prompt.txt?raw'

const setupPrompt = prompt.trim()

/** Copy setup in one tap; the receiving AI completes the connection. */
export function AgentConnection() {
  const id = useId()
  const [copyStatus, setCopyStatus] = useState<'idle' | 'copying' | 'copied' | 'failed'>('idle')

  async function copyPrompt() {
    setCopyStatus('copying')
    try {
      await navigator.clipboard.writeText(setupPrompt)
      setCopyStatus('copied')
    } catch {
      setCopyStatus('failed')
    }
  }

  return <>
    <button
      type="button"
      onClick={copyPrompt}
      disabled={copyStatus === 'copying'}
      aria-describedby={id + '-hint'}
      title="Copy setup instructions to paste into ChatGPT, Claude, Grok or another AI agent."
      className="agent-connect inline-flex min-h-11 items-center gap-2 rounded-lg py-1 text-left text-xs text-fg-soft hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand disabled:opacity-70"
    >
      {copyStatus === 'copied'
        ? <Check size={16} className="shrink-0" aria-hidden="true" />
        : <Copy size={16} className="shrink-0" aria-hidden="true" />}
      <span>
        <span className="block font-semibold underline decoration-border-strong underline-offset-4">
          {copyStatus === 'copied' ? 'Prompt copied' : copyStatus === 'copying' ? 'Copying prompt…' : 'Copy AI prompt'}
        </span>
        <span id={id + '-hint'} className="mt-0.5 block text-[11px] leading-snug text-muted">Paste into your AI chat</span>
      </span>
    </button>
    <span role="status" className="sr-only">
      {copyStatus === 'copied' ? 'Prompt copied. Paste it into ChatGPT, Claude, Grok or your AI agent.' : ''}
    </span>
    {copyStatus === 'failed' && <div className="order-last w-full pb-3 text-xs text-muted">
      <p role="status" id={id + '-error'} className="mb-2">Couldn’t copy automatically. Select the prompt below and copy it.</p>
      <textarea
        readOnly
        aria-label="AI setup prompt"
        aria-describedby={id + '-error'}
        value={setupPrompt}
        rows={6}
        onFocus={(event) => event.currentTarget.select()}
        className="block w-full resize-y rounded-xl border border-border-strong bg-surface-2 p-3 text-sm leading-relaxed text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand"
      />
    </div>}
  </>
}
