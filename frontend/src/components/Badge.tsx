import type { ReactNode } from 'react'

type Tone = 'default' | 'brand' | 'warn' | 'danger'

const CLS: Record<Tone, string> = {
  default: 'badge',
  brand: 'badge badge-brand',
  warn: 'badge badge-warn',
  danger: 'badge badge-danger',
}

export function Badge({
  tone = 'default',
  children,
  title,
}: {
  tone?: Tone
  children: ReactNode
  title?: string
}) {
  return (
    <span className={CLS[tone]} title={title}>
      {children}
    </span>
  )
}
