import { tickerSummary } from '@/lib/domain'

// The live "NOW" band: a one-line narration of in-flight agent activity, so the
// digdown can see what the modes are doing without opening any incident.
// Renders nothing when there is no in-flight work and nothing awaiting.
export function LiveTicker({ incidents }) {
  const { items, awaiting } = tickerSummary(incidents)
  if (items.length === 0 && awaiting === 0) return null

  return (
    <div
      className="flex items-center gap-3 overflow-hidden border-b bg-card px-4 py-1 text-xs lg:px-5"
      role="status"
      aria-live="polite"
    >
      <span
        className="label-eyebrow flex shrink-0 items-center gap-1.5"
        style={{ color: 'var(--live)' }}
      >
        <span
          className="size-1.5 rounded-full pulse-ring"
          style={{ backgroundColor: 'var(--live)' }}
        />
        Now
      </span>
      <span className="min-w-0 flex-1 truncate text-muted-foreground">
        {items.map((it, i) => (
          <span key={`${it.target}-${i}`}>
            {i > 0 && ', '}
            {it.verb} <span className="text-foreground/80">{it.target}</span>
          </span>
        ))}
        {awaiting > 0 && (
          <>
            {items.length > 0 && ', '}
            <span style={{ color: 'var(--sev-unhealthy)' }}>
              {awaiting} waiting for you
            </span>
          </>
        )}
      </span>
    </div>
  )
}
