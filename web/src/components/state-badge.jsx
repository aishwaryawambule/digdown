import { cn } from '@/lib/utils'
import { stateMeta, stateBadge } from '@/lib/domain'

// tone -> color var + how strongly to fill. open/good states earn a dot;
// terminal/muted states stay quiet.
const TONE = {
  open: { color: 'var(--sev-unhealthy)', dot: true },
  progress: { color: 'var(--sev-disk)', dot: true },
  good: { color: 'var(--live)', dot: false },
  bad: { color: 'var(--destructive)', dot: false },
  muted: { color: 'var(--muted-foreground)', dot: false },
}

// Pass `incident` for a resolution-aware label (an auto-clear reads "Cleared",
// not "Resolved"); `state` alone still works for state-only callers (e.g. a legend).
export function StateBadge({ state, incident, className }) {
  const meta = incident ? stateBadge(incident) : stateMeta(state)
  const tone = TONE[meta.tone] || TONE.progress
  return (
    <span className={cn('inline-flex items-center gap-1.5 text-sm whitespace-nowrap', className)} style={{ color: tone.color }}>
      {tone.dot && <span className="size-1.5 shrink-0 rounded-[1px]" style={{ backgroundColor: tone.color }} />}
      {meta.label}
    </span>
  )
}
