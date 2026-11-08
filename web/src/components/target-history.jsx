import { useMemo } from 'react'
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { SignalIcon } from '@/components/signal-icon'
import { StateBadge } from '@/components/state-badge'
import { signalMeta, relTime, absTime, trackRecord } from '@/lib/domain'

// Per-target memory: every incident this container has produced, newest first,
// each opening its case file. Pure read over the loaded incident list.
export function TargetHistory({ target, incidents, onOpenIncident, onOpenChange }) {
  const rows = useMemo(() => {
    if (!target) return []
    return incidents
      .filter((i) => i.target === target)
      .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
  }, [incidents, target])
  const record = useMemo(
    () => (target ? trackRecord(incidents, target) : null),
    [incidents, target],
  )

  return (
    <Sheet open={!!target} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full gap-0 sm:max-w-md">
        <SheetHeader>
          <SheetTitle className="font-data">{target}</SheetTitle>
          {record && (
            <p className="text-sm text-muted-foreground">
              Failed {record.occurrencesTotal} {record.occurrencesTotal === 1 ? 'time' : 'times'}.
              {record.lastSeenIso && <> Last time was {relTime(record.lastSeenIso)} ago.</>}
            </p>
          )}
        </SheetHeader>
        <div className="flex-1 overflow-y-auto px-4 pb-4">
          {rows.length === 0 ? (
            <p className="text-sm text-muted-foreground">First time Digdown has seen this.</p>
          ) : (
            <ul className="space-y-1.5">
              {rows.map((i) => (
                <li key={i.id}>
                  <button
                    type="button"
                    onClick={() => onOpenIncident(i)}
                    className="glass-lite glass-raise flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left"
                  >
                    <SignalIcon type={i.signal?.type} size="sm" />
                    <div className="min-w-0 flex-1">
                      <div className="text-[13px] font-medium text-foreground">
                        {signalMeta(i.signal?.type).label}
                      </div>
                      <div className="text-xs text-muted-foreground" title={absTime(i.created_at)}>
                        {relTime(i.created_at)} ago
                      </div>
                    </div>
                    <StateBadge incident={i} />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </SheetContent>
    </Sheet>
  )
}
