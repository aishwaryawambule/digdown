import { useMemo } from 'react'
import { toast } from 'sonner'
import { Copy, Download, Clock, Wrench, ShieldCheck, ListChecks, FileText } from 'lucide-react'
import { SignalIcon } from '@/components/signal-icon'
import { StateBadge } from '@/components/state-badge'
import {
  signalMeta, absTime, relTime, formatDuration, isTerminal, liveStatus,
  executionOutcome, incidentTimeline, postmortemMarkdown, actionLabel, ranByAutoHeal, keepFixHint } from '@/lib/domain'

function Fact({ label, children }) {
  return (
    <div>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="font-data mt-0.5 text-sm text-foreground">{children}</div>
    </div>
  )
}

function Block({ icon: Icon, title, children }) {
  return (
    <section className="space-y-2">
      <h3 className="label-eyebrow flex items-center gap-2 text-muted-foreground">
        <Icon className="size-3.5" /> {title}
      </h3>
      {children}
    </section>
  )
}

// The incident's record as a shareable report. Reads the same `record` the case
// file uses; all logic (timeline, markdown) lives in domain.js.
export function Postmortem({ incident, record, track, onOpenHistory }) {
  const timeline = useMemo(() => incidentTimeline(incident, record), [incident, record])
  const meta = signalMeta(incident.signal?.type)
  const term = isTerminal(incident.state)
  const { diagnosis, plan, execution } = record
  const outcome = execution ? executionOutcome(execution) : null

  const exportMd = () => postmortemMarkdown(incident, record, timeline, track)
  const copyMd = async () => {
    try {
      await navigator.clipboard.writeText(exportMd())
      toast('Report copied')
    } catch {
      toast.error('Your browser blocked copying. Use the download button instead.')
    }
  }
  const downloadMd = () => {
    const blob = new Blob([exportMd()], { type: 'text/markdown' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `postmortem-${incident.target}-${incident.id}.md`
    a.click()
    URL.revokeObjectURL(url)
  }

  const recurrence =
    track && track.occurrencesTotal > 1 ? `${track.occurrencesTotal} times` : 'First time'

  return (
    <div className="space-y-6">
      {/* header + export */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex min-w-0 items-start gap-3">
          <SignalIcon type={incident.signal?.type} size="lg" />
          <div className="min-w-0">
            <h2 className="font-data truncate text-lg text-foreground">{incident.target}</h2>
            <p className="mt-0.5 text-sm" style={{ color: `var(--${meta.token})` }}>{meta.label}</p>
          </div>
          <StateBadge incident={incident} className="ml-1 shrink-0" />
        </div>
        <div className="flex shrink-0 gap-2">
          <button
            type="button"
            onClick={copyMd}
            className="glass glass-raise flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs text-foreground"
          >
            <Copy className="size-3.5" /> Copy as Markdown
          </button>
          <button
            type="button"
            onClick={downloadMd}
            aria-label="Download report as Markdown"
            className="glass glass-raise flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs text-foreground"
          >
            <Download className="size-3.5" /> .md
          </button>
        </div>
      </div>

      {/* in-progress banner */}
      {!term && (
        <div
          className="flex items-center gap-2 rounded-lg border px-4 py-2.5 text-sm"
          style={{
            color: 'var(--sev-unhealthy)',
            borderColor: 'color-mix(in oklch, var(--sev-unhealthy) 28%, transparent)',
            backgroundColor: 'color-mix(in oklch, var(--sev-unhealthy) 8%, transparent)',
          }}
        >
          Still in progress: {liveStatus(incident).text}. This report fills in as it goes.
        </div>
      )}

      {/* summary key facts */}
      <div className="glass grid grid-cols-2 gap-4 rounded-xl p-3.5 sm:grid-cols-3 lg:grid-cols-5">
        <Fact label="Detected">{relTime(incident.created_at)} ago</Fact>
        <Fact label="Ended">{term ? relTime(incident.updated_at) + ' ago' : 'Not yet'}</Fact>
        <Fact label="Time to fix">{timeline.totalMs != null ? formatDuration(timeline.totalMs) : 'Not yet'}</Fact>
        <Fact label="Outcome">{outcome ? outcome.label : term ? 'Closed' : 'In progress'}</Fact>
        <button type="button" onClick={() => onOpenHistory?.(incident.target)} className="text-left">
          <Fact label="Recurrence">{recurrence}</Fact>
        </button>
      </div>

      {/* headline root cause */}
      <p className="text-[15px] leading-relaxed text-foreground">
        {diagnosis?.root_cause || 'No cause has been recorded yet.'}
      </p>

      {/* timeline */}
      <Block icon={Clock} title="What happened, in order">
        <ol className="relative space-y-3 border-l border-border pl-5">
          {timeline.steps.map((s) => (
            <li key={s.key} className="relative">
              <span
                className="absolute -left-[1.4rem] top-1 size-2 rounded-full"
                style={{ backgroundColor: s.inProgress ? 'var(--sev-unhealthy)' : 'var(--live)' }}
              />
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span className="text-sm text-foreground">{s.label}</span>
                {s.actor && <span className="text-xs text-muted-foreground">{s.actor}</span>}
                {s.sinceMs != null && (
                  <span className="font-data text-xs text-muted-foreground/70">+{formatDuration(s.sinceMs)}</span>
                )}
                <span className="font-data ml-auto text-xs text-muted-foreground" title={absTime(s.ts)}>
                  {relTime(s.ts)}
                </span>
              </div>
            </li>
          ))}
        </ol>
      </Block>

      {/* evidence */}
      {diagnosis?.evidence?.length > 0 && (
        <Block icon={FileText} title="Evidence">
          <ul className="space-y-1.5">
            {diagnosis.evidence.map((e, i) => (
              <li key={i} className="font-data flex gap-2 text-[13px] text-muted-foreground">
                <span className="mt-2 size-1 shrink-0 rounded-full bg-muted-foreground/60" /> {e}
              </li>
            ))}
          </ul>
        </Block>
      )}

      {/* fix */}
      {plan && (
        <Block icon={Wrench} title="The fix">
          {plan.kind === 'manual' ? (
            <p className="text-sm leading-relaxed text-foreground">{plan.manual_fix || 'Manual fix recommended.'}</p>
          ) : (
            <>
              {plan.summary && <p className="mb-2 text-sm leading-relaxed text-foreground">{plan.summary}</p>}
              <ul className="space-y-1.5">
                {(plan.actions || []).map((a, i) => (
                  <li key={i} className="rounded-md border bg-background/40 px-3 py-1.5 text-sm">
                    <span className="label-eyebrow text-foreground">{actionLabel(a.type)}</span>{' '}
                    <span className="font-data text-foreground">{a.target}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </Block>
      )}

      {/* outcome */}
      {(execution || term) && (
        <Block icon={ListChecks} title="How it ended">
          <p className="text-sm" style={{ color: outcome ? outcome.tone : 'var(--muted-foreground)' }}>
            {outcome ? outcome.label : 'Closed'}
            {execution?.detail ? `. ${execution.detail}` : ''}
          </p>
          {ranByAutoHeal(record) && (
            <p className="mt-1 text-sm text-muted-foreground">Auto heal ran this fix without waiting for approval.</p>
          )}
          {execution?.verified && keepFixHint(plan) && (
            <p className="mt-2 rounded-md border bg-background px-3 py-2 text-sm text-foreground">{keepFixHint(plan)}</p>
          )}
        </Block>
      )}

      {/* critic */}
      {(plan?.critic2_reason || diagnosis?.critic_reason) && (
        <Block icon={ShieldCheck} title="Second opinion">
          <p className="border-l-2 pl-3 text-[13px] italic leading-relaxed text-foreground/85" style={{ borderColor: 'var(--sev-unhealthy)' }}>
            &quot;{plan?.critic2_reason || diagnosis?.critic_reason}&quot;
          </p>
        </Block>
      )}
    </div>
  )
}
