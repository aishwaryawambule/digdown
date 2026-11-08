import { useMemo } from 'react'
import { ChevronRight, Loader2 } from 'lucide-react'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { SignalIcon } from '@/components/signal-icon'
import { LifecycleTracker } from '@/components/lifecycle-tracker'
import { signalMeta, detailSummary, relTime, absTime, isActive, liveStatus, trackRecord } from '@/lib/domain'
import { RecurrenceChip } from '@/components/recurrence-chip'
import { cn } from '@/lib/utils'

export function IncidentCard({ incident, incidents = [], onOpen, fresh, focused }) {
  const meta = signalMeta(incident.signal?.type)
  const summary = detailSummary(incident.signal)
  const record = useMemo(
    () => trackRecord(incidents, incident.target, {
      signalType: incident.signal?.type,
      signature: incident.error_signature,
    }),
    [incidents, incident.target, incident.signal?.type, incident.error_signature],
  )
  const dimmed = !isActive(incident)
  const status = liveStatus(incident)
  const STATUS_COLOR = {
    diagnose: 'var(--sev-disk)', await: 'var(--sev-unhealthy)', apply: 'var(--primary)',
    done: 'var(--live)', bad: 'var(--sev-crash)', muted: 'var(--muted-foreground)',
  }

  return (
    <button
      type="button"
      id={`inc-${incident.id}`}
      onClick={() => onOpen(incident)}
      aria-current={focused || undefined}
      className={cn(
        'group glass-lite glass-raise relative flex w-full scroll-mt-4 items-center gap-3.5 overflow-hidden rounded-lg py-3 pr-4 pl-0 text-left',
        'hover:border-ring/40 hover:bg-accent/40',
        'focus-visible:ring-ring/60 focus-visible:ring-2 focus-visible:outline-none',
        dimmed && 'opacity-65 hover:opacity-100',
        focused && 'border-ring/60 bg-accent/40 opacity-100 ring-2 ring-ring/70',
        fresh && 'flash-in',
      )}
    >
      {/* severity stripe — the row's left edge is the signal hue */}
      <span
        aria-hidden
        className="h-[34px] w-[3px] shrink-0 rounded-r"
        style={{ backgroundColor: `var(--${meta.token})` }}
      />
      <SignalIcon type={incident.signal?.type} size="sm" />

      <div className="min-w-0 flex-1">
        <div className="flex min-w-0 items-center gap-2.5">
          <span className="font-data truncate text-[14px] font-medium text-foreground">
            {incident.target}
          </span>
          <span className="label-eyebrow shrink-0" style={{ color: `var(--${meta.token})` }}>
            {meta.short}
          </span>
          <RecurrenceChip record={record} className="shrink-0 text-[0.625rem]" />
        </div>
        <div className="mt-1 flex min-w-0 items-center gap-2.5">
          <LifecycleTracker state={incident.state} tone={status.tone} />
          <span
            className="flex min-w-0 flex-wrap items-center gap-x-1.5 text-[13px]"
            style={{ color: STATUS_COLOR[status.tone] || 'var(--muted-foreground)' }}
          >
            {status.active && <Loader2 className="size-3 shrink-0 animate-spin" />}
            {status.text}
            {summary && (
              <span className="font-data text-muted-foreground/70">{summary}</span>
            )}
          </span>
        </div>
      </div>

      <div className="flex shrink-0 items-center gap-3">
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="font-data w-9 text-right text-sm tabular-nums text-muted-foreground">
              {relTime(incident.created_at)}
            </span>
          </TooltipTrigger>
          <TooltipContent>Detected {absTime(incident.created_at)}</TooltipContent>
        </Tooltip>
        <ChevronRight className="size-4 text-muted-foreground/50 transition-transform group-hover:translate-x-0.5 group-hover:text-muted-foreground" />
      </div>
    </button>
  )
}
