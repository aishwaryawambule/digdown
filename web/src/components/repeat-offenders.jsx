import { repeatOffenders } from '@/lib/domain'
import { RecurrenceChip } from '@/components/recurrence-chip'

// The aggregate face of memory: targets that keep failing, ranked, each opening
// its track record. Renders nothing on a calm board (no offenders).
export function RepeatOffenders({ incidents, onOpenHistory }) {
  const rows = repeatOffenders(incidents)
  if (rows.length === 0) return null
  return (
    <div className="glass rounded-xl p-3.5">
      <h3 className="label-eyebrow text-muted-foreground">Keeps failing</h3>
      <ul className="mt-3 flex flex-col gap-1">
        {rows.map((r) => (
          <li key={r.target}>
            <button
              type="button"
              onClick={() => onOpenHistory?.(r.target)}
              aria-label={`${r.target} history`}
              className="flex w-full items-center gap-2 rounded-md px-1.5 py-1 text-left transition-colors hover:bg-accent/40 focus-visible:ring-2 focus-visible:ring-ring/60 focus-visible:outline-none"
            >
              <span className="font-data min-w-0 flex-1 truncate text-sm text-foreground">{r.target}</span>
              <RecurrenceChip record={r} />
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}
