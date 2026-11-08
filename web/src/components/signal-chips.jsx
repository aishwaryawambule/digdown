import { SIGNALS, SIGNAL_CATEGORIES } from '@/lib/domain'
import { cn } from '@/lib/utils'

// Signal types grouped by their detecting source (container · http · logs ·
// metrics), hardest-down first within each group — the order a person
// triages in.
const TYPES_BY_CATEGORY = Object.values(SIGNAL_CATEGORIES)
  .sort((a, b) => a.order - b.order)
  .map((cat) => ({
    ...cat,
    types: Object.values(SIGNALS)
      .filter((s) => s.category === cat.key)
      .sort((a, b) => b.rank - a.rank),
  }))
  .filter((g) => g.types.length > 0)

// The by-signal cluster, horizontalised into filter chips and labelled by
// source. `incidents` is the already-scoped set (Active / Resolved / …), so a
// chip's count is how many of that type the current scope holds — click toggles
// the type filter. A source's row appears only when that scope actually holds
// one of its signals, so you never get a wall of zeros: the row is hidden
// entirely when the scope is empty, and HTTP / log / metric rows slot in beside
// the container row the moment app-level probes fire (v1). Category labels show
// only when more than one source is in play, so a single-source view stays clean.
export function SignalChips({ incidents, typeFilter, onToggleType }) {
  const counts = {}
  const present = new Set()
  for (const i of incidents) {
    const type = i.signal?.type
    const meta = SIGNALS[type]
    if (meta) present.add(meta.category)
    counts[type] = (counts[type] || 0) + 1
  }

  // Nothing in this scope to filter — drop the row rather than show all zeros.
  if (incidents.length === 0) return null

  const groups = TYPES_BY_CATEGORY.filter((g) => present.has(g.key))
  const showLabels = groups.length > 1

  return (
    <div className="flex flex-col gap-2.5">
      {groups.map((g) => {
        // Only types that actually occur in this scope (plus any toggled on, so
        // an active filter can always be cleared) — never a row of zeros.
        const types = g.types.filter((sig) => (counts[sig.key] || 0) > 0 || typeFilter.has(sig.key))
        if (types.length === 0) return null
        return (
        <div key={g.key} className="flex flex-wrap items-center gap-2">
          {showLabels && (
            <span className="label-eyebrow w-16 shrink-0 text-muted-foreground">{g.label}</span>
          )}
          {types.map((sig) => {
            const n = counts[sig.key] || 0
            const on = typeFilter.has(sig.key)
            return (
              <button
                key={sig.key}
                type="button"
                aria-pressed={on}
                onClick={() => onToggleType(sig.key)}
                className={cn(
                  'flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs transition-colors',
                  'hover:border-ring/40 hover:bg-accent/40',
                  on && 'border-ring/60 bg-accent',
                  !n && !on && 'opacity-45',
                )}
              >
                <span
                  className="size-2 rounded-full"
                  style={{ backgroundColor: `var(--${sig.token})` }}
                />
                <span>{sig.short}</span>
                <span className={cn('font-data tabular-nums', n ? 'text-foreground' : 'text-muted-foreground/50')}>
                  {n}
                </span>
              </button>
            )
          })}
        </div>
        )
      })}
    </div>
  )
}
