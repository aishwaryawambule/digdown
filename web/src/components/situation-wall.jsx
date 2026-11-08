import { useMemo } from 'react'
import { ArrowRight, Check, HeartPulse, Loader2, RefreshCw, ShieldCheck, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { DismissButton } from '@/components/dismiss-button'
import { ConfirmAction } from '@/components/confirm-action'
import { SignalIcon } from '@/components/signal-icon'
import { StateBadge } from '@/components/state-badge'
import { useIncidentRecord } from '@/hooks/useIncidentRecord'
import {
  signalMeta,
  isActive,
  bySeverityThenTime,
  firedDetail,
  liveStatus,
  lifecyclePhase,
  posture,
  resolvedWithin,
  trackRecord,
  cameBackLine,
  emptyMessage,
  recentHeals,
  autoHealSkipReason,
  relTime,
} from '@/lib/domain'
import { RecurrenceChip } from '@/components/recurrence-chip'
import { RepeatOffenders } from '@/components/repeat-offenders'

const RISK_COLOR = { low: 'var(--live)', medium: 'var(--sev-unhealthy)', high: 'var(--sev-crash)' }
const POSTURE = {
  nominal: { label: 'All good', color: 'var(--live)' },
  elevated: { label: 'Worth a look', color: 'var(--sev-unhealthy)' },
  critical: { label: 'Something is down', color: 'var(--sev-oom)' },
}

// ── Hero ────────────────────────────────────────────────────────────────
// The page's thesis: the single thing that wants you right now. A pending fix
// becomes a decisive command panel (decide in place); otherwise the hardest
// active incident leads; an empty board reads as a calm watch-floor.
function SituationHero({ incident, record, track, status: appStatus, services, onOpen, onDecision, onRetry, onDismiss, onOpenHistory, busy }) {
  if (!incident) return <AllClearHero status={appStatus} services={services} />

  const meta = signalMeta(incident.signal?.type)
  const status = liveStatus(incident)
  const proposed = incident.state === 'proposed'
  const failed = incident.state === 'diagnosis_failed'
  const accent = `var(--${meta.token})`
  const suggestOnly = appStatus?.mode === 'propose'
  const { diagnosis, plan } = record
  // Until a root cause lands, give the body a bit of context — what actually
  // fired — under the status line, rather than leaving it bare.
  const fired = firedDetail(incident.signal)
  const cameBack = cameBackLine(track)
  const explainOnlyNote = incident.state === 'diagnosed' && appStatus?.mode === 'observe'
  // Only a decision or a retry needs buttons. Otherwise the card itself opens it.
  const sideColumn = (proposed && plan) || failed || explainOnlyNote

  return (
    <div className="glass relative flex flex-col rounded-xl p-3.5 transition-colors hover:border-ring/40">
      <div className="flex items-center gap-2" style={{ color: accent }}>
        {(proposed || status.active) && (
          <span className="size-1.5 rounded-full" style={{ backgroundColor: accent }} />
        )}
        <span className="label-eyebrow">
          {proposed ? 'Needs your decision' : status.active ? 'Working on it' : 'Most urgent'}
        </span>
        <span className="relative z-10 ml-auto flex items-center gap-2">
          <RecurrenceChip
            record={track}
            onClick={onOpenHistory ? () => onOpenHistory(incident.target) : undefined}
          />
          <StateBadge incident={incident} />
        </span>
      </div>

      <div className={cn('mt-2 grid grid-cols-1 gap-x-5', sideColumn && 'lg:grid-cols-2')}>
      <div className="min-w-0">
      <div className="flex items-start gap-3">
        <SignalIcon type={incident.signal?.type} />
        <div className="min-w-0 flex-1 pt-0.5">
          <h2 className="font-data truncate text-base leading-tight font-medium tracking-normal text-foreground" title={incident.target}>
            {/* Stretched over the whole card, so clicking anywhere on it opens the incident. */}
            <button
              type="button"
              onClick={() => onOpen(incident)}
              aria-label={`Open ${incident.target}: ${meta.label}`}
              className="text-left outline-none after:absolute after:inset-0 after:rounded-xl focus-visible:after:outline-2 focus-visible:after:outline-ring"
            >
              {incident.target}
            </button>
          </h2>
          <p className="mt-0.5 text-sm" style={{ color: accent }}>
            {meta.label}
          </p>
        </div>
      </div>

      {/* Root cause — the headline finding, shown big when we have it.
          Before it lands, the status line plus a line of context on what fired. */}
      {diagnosis?.root_cause ? (
        <p className="mt-2.5 max-w-[80ch] text-sm leading-relaxed text-foreground">{diagnosis.root_cause}</p>
      ) : (
        <div className="mt-4 flex flex-col gap-2">
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            {status.active && <Loader2 className="size-4 shrink-0 animate-spin text-primary" />}
            {status.active ? 'Digdown is working out why…' : status.text}
          </p>
          {fired && (
            <p className="line-clamp-2 text-sm leading-relaxed text-muted-foreground/80">
              {fired.summary}
            </p>
          )}
        </div>
      )}

      {cameBack && (
        <p className="mt-2 text-sm" style={{ color: 'var(--sev-unhealthy)' }}>
          {cameBack}
        </p>
      )}

      {proposed && autoHealSkipReason(record) && (
        <p className="mt-2 text-sm text-muted-foreground">
          Auto heal left this for you. {autoHealSkipReason(record)}
        </p>
      )}

      {!sideColumn && (
        <p className="mt-3 flex items-center gap-1 text-sm text-primary" aria-hidden>
          See details <ArrowRight className="size-4" />
        </p>
      )}
      </div>
      {sideColumn && (
      <div className="relative z-10 flex min-w-0 flex-col">
      {/* Proposed fix at a glance (full detail lives in the case file). */}
      {proposed && plan && plan.kind !== 'manual' && (
        <div className="mt-2.5 rounded-lg border bg-background px-3 py-2 lg:mt-0">
          <div className="label-eyebrow mb-1.5 flex items-center gap-2 text-muted-foreground">
            Proposed fix
            {plan.risk && (
              <span
                className="rounded-sm border px-1.5 py-px"
                style={{
                  color: RISK_COLOR[plan.risk] || 'var(--muted-foreground)',
                  borderColor: `color-mix(in oklch, ${RISK_COLOR[plan.risk] || 'var(--muted-foreground)'} 26%, transparent)`,
                }}
              >
                {plan.risk === 'low' ? 'Low' : plan.risk === 'medium' ? 'Medium' : 'High'} risk
              </span>
            )}
          </div>
          <p className="text-sm leading-relaxed text-foreground">{plan.summary}</p>
        </div>
      )}

      {explainOnlyNote && (
        <p className="mt-3 text-sm text-muted-foreground">
          Digdown is set to explain only. To get a suggested fix next time, change this in Settings.
        </p>
      )}

      {/* Actions: decide in place when it's a pending fix; otherwise dive in. */}
      <div className="mt-auto pt-3">
        {proposed ? (
          <>
            <div className="flex flex-wrap items-center gap-2.5">
              <Button disabled={busy} onClick={() => onDecision(incident, 'approve')}>
                {busy ? <Loader2 className="size-4 animate-spin" /> : <Check className="size-4" />}
                Approve fix
              </Button>
              <ConfirmAction
                title="Reject the proposed fix?"
                description="The fix won't be applied, nothing changes, and this incident is closed. It won't reopen unless the problem clears and comes back."
                confirmLabel="Reject"
                onConfirm={() => onDecision(incident, 'reject')}
              >
                <Button variant="outline" disabled={busy}>
                  <X className="size-4" />
                  Reject
                </Button>
              </ConfirmAction>
              <Button variant="ghost" onClick={() => onOpen(incident)}>
                See details
                <ArrowRight className="size-4" />
              </Button>
            </div>
            <p className="mt-2.5 flex items-center gap-1.5 text-xs text-muted-foreground">
              <ShieldCheck className="size-3.5 shrink-0" style={{ color: 'var(--live)' }} />
              {suggestOnly
                ? "Digdown is only suggesting fixes, so approving won't change anything. Change this in Settings."
                : "Digdown checks that the fix worked and undoes any changes if it didn't."}
            </p>
          </>
        ) : failed ? (
          <div className="flex flex-wrap items-center gap-2.5">
            <Button className="flex-1" disabled={busy} onClick={() => onRetry(incident)}>
              {busy ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
              Retry diagnosis
            </Button>
            <DismissButton onDismiss={() => onDismiss(incident)} disabled={busy} />
            <Button variant="ghost" onClick={() => onOpen(incident)}>
              See details
              <ArrowRight className="size-4" />
            </Button>
          </div>
        ) : null}
      </div>
      </div>
      )}
      </div>
    </div>
  )
}

// Nothing to act on. Says so only as far as Digdown can actually tell.
function AllClearHero({ status, services }) {
  const { title, body } = emptyMessage(status, services)
  return (
    <div className="glass flex flex-col rounded-xl p-3.5">
      <h2 className="panel-title">{title}</h2>
      <p className="mt-1 max-w-[80ch] text-sm text-muted-foreground">{body}</p>
    </div>
  )
}

// ── Metric strip ──────────────────────────────────────────────────────────
// The state of everything in one line, as on an instrument panel. Each figure is
// also a way in: click it to see what it counts.
function Metric({ value, label, tone, onClick }) {
  const body = (
    <>
      <span className="font-data block text-xl leading-tight font-medium tabular-nums" style={tone ? { color: tone } : undefined}>
        {value}
      </span>
      <span className="block text-xs text-muted-foreground">{label}</span>
    </>
  )
  const cell = 'min-w-[6.5rem] flex-1 border-r px-3.5 py-2 text-left last:border-r-0'
  if (!onClick) return <div className={cell}>{body}</div>
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={`${value} ${label}. View them.`}
      className={`${cell} transition-colors hover:bg-accent/40 focus-visible:ring-2 focus-visible:ring-ring/60 focus-visible:outline-none focus-visible:ring-inset`}
    >
      {body}
    </button>
  )
}

function MetricStrip({ incidents, status, onNavigate }) {
  const active = incidents.filter(isActive)
  // "All good" is a claim Digdown can only make while it can see the containers.
  const blind = status && !status.docker?.ok
  const p = blind
    ? { label: "Can't tell", color: 'var(--muted-foreground)' }
    : POSTURE[posture(incidents)] || POSTURE.nominal
  const fixed = resolvedWithin(incidents)
  const stages = [0, 0, 0]
  for (const i of active) {
    const index = lifecyclePhase(i.state).index
    if (index < 3) stages[index] += 1
  }
  // Being diagnosed and being fixed are both Digdown working. Containers with
  // problems are on the Containers page, and how many are watched is in the menu.
  const working = stages[0] + stages[2]
  const lit = (n, color) => (n ? color : undefined)
  return (
    <div className="glass flex flex-wrap overflow-hidden rounded-xl">
      <div className="min-w-[9rem] flex-[1.4] border-r px-3.5 py-2 max-sm:basis-full max-sm:border-r-0 max-sm:border-b">
        <span className="flex items-center gap-2 text-[1.05rem] leading-[1.6rem] font-semibold" style={{ color: p.color }}>
          <span className="size-2 shrink-0 rounded-full" style={{ backgroundColor: p.color }} />
          {p.label}
        </span>
        <span className="block text-xs text-muted-foreground">Right now</span>
      </div>
      <Metric value={stages[1]} label="Need you" tone={lit(stages[1], 'var(--sev-unhealthy)')} onClick={() => onNavigate({ to: 'incidents', stage: 1 })} />
      <Metric value={working} label="Digdown is on it" onClick={() => onNavigate({ to: 'incidents', scope: 'active' })} />
      <Metric value={fixed} label="Fixed today" tone={lit(fixed, 'var(--live)')} onClick={() => onNavigate({ to: 'incidents', scope: 'resolved' })} />
    </div>
  )
}

// ── Auto heal's note ────────────────────────────────────────────────────────
// What it did while you weren't looking. Reads as a note to you, not a log.
function HealedNote({ incidents, onOpenIncident }) {
  const heals = recentHeals(incidents)
  if (heals.length === 0) return null
  return (
    <div className="glass min-w-0 rounded-xl p-3.5">
      <h2 className="panel-title flex items-center gap-2">
        <HeartPulse className="size-4 shrink-0" style={{ color: 'var(--live)' }} aria-hidden />
        Auto heal fixed {heals.length === 1 ? '1 thing' : `${heals.length} things`} in the last day
        <a href="#/auto-heal" className="ml-auto shrink-0 text-xs font-normal text-muted-foreground hover:text-foreground">See all</a>
      </h2>
      <ul className="mt-2 flex flex-col gap-1">
        {heals.slice(0, 5).map((i) => (
          <li key={i.id}>
            <button
              type="button"
              onClick={() => onOpenIncident(i)}
              className="-mx-2 block w-[calc(100%+1rem)] rounded-md px-2 py-1.5 text-left transition-colors hover:bg-accent/40 focus-visible:ring-2 focus-visible:ring-ring/60 focus-visible:outline-none"
            >
              <span className="flex items-baseline justify-between gap-3">
                <span className="font-data min-w-0 truncate text-sm text-foreground">{i.target}</span>
                <span className="font-data shrink-0 text-xs text-muted-foreground">{relTime(i.updated_at)}</span>
              </span>
              {i.heal_summary && <span className="mt-0.5 block text-sm text-muted-foreground">{i.heal_summary}</span>}
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

// ── The wall ────────────────────────────────────────────────────────────────
export function SituationWall({ incidents, services = [], status, mock, onOpenIncident, onDecision, onRetry, onDismiss, onNavigate, onOpenHistory, busy }) {
  const featured = useMemo(() => {
    const active = incidents.filter(isActive)
    const proposed = active.filter((i) => i.state === 'proposed').sort(bySeverityThenTime)
    if (proposed.length) return proposed[0]
    return [...active].sort(bySeverityThenTime)[0] || null
  }, [incidents])

  // Always called (hooks rule); resolves to nulls when nothing is featured.
  const record = useIncidentRecord(featured, true, mock)
  const track = useMemo(
    () =>
      featured
        ? trackRecord(incidents, featured.target, {
            signalType: featured.signal?.type,
            signature: featured.error_signature,
          })
        : null,
    [incidents, featured],
  )

  return (
    <section aria-label="Summary" className="flex min-w-0 flex-col gap-3">
      <MetricStrip incidents={incidents} status={status} onNavigate={onNavigate} />
      <SituationHero
        status={status}
        services={services}
        incident={featured}
        record={record}
        track={track}
        onOpen={onOpenIncident}
        onDecision={onDecision}
        onRetry={onRetry}
        onDismiss={onDismiss}
        onOpenHistory={onOpenHistory}
        busy={busy}
      />
      <div className="grid grid-cols-1 items-start gap-3 lg:grid-cols-2">
        <HealedNote incidents={incidents} onOpenIncident={onOpenIncident} />
        <RepeatOffenders incidents={incidents} onOpenHistory={onOpenHistory} />
      </div>
    </section>
  )
}
