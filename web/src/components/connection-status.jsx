import { cn } from '@/lib/utils'

const MAP = {
  live: { color: 'var(--live)', label: 'Live', pulse: false },
  connecting: { color: 'var(--sev-unhealthy)', label: 'Connecting', pulse: true },
  down: { color: 'var(--destructive)', label: 'Disconnected', pulse: false },
}

export function ConnectionStatus({ conn, className }) {
  const s = MAP[conn] || MAP.connecting
  return (
    <span
      className={cn('inline-flex items-center gap-2', className)}
      role="status"
      aria-label={`Live updates: ${s.label}`}
    >
      <span className="relative grid size-2.5 place-items-center">
        <span
          className={cn('absolute inset-0 rounded-full', s.pulse && 'pulse-ring')}
          style={{ backgroundColor: s.color }}
        />
      </span>
      <span className="text-xs font-medium" style={{ color: s.color }}>
        {s.label}
      </span>
    </span>
  )
}
