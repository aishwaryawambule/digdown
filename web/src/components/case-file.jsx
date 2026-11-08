import { useState } from 'react'
import { Check, X, Loader2, ArrowRight, RefreshCw, ShieldCheck, ShieldAlert, Wrench, CheckCircle2, RotateCcw, AlertTriangle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { isTerminal, executionOutcome, relTime, canDismiss, actionParamsLabel, actionLabel, liveStatus, autoHealSkipReason, ranByAutoHeal, keepFixHint } from '@/lib/domain'
import { DismissButton } from '@/components/dismiss-button'
import { ConfirmAction } from '@/components/confirm-action'

const RISK_COLOR = { low: 'var(--live)', medium: 'var(--sev-unhealthy)', high: 'var(--sev-crash)' }
const OUTCOME_ICON = { ok: CheckCircle2, revert: RotateCcw, fail: AlertTriangle }

// Flatten the stored ReAct transcript into call/observation lines.
function traceItems(steps) {
  const items = []
  for (const m of steps || []) {
    if (m.role === 'assistant' && m.tool_calls) {
      for (const tc of m.tool_calls) {
        let args = ''
        try {
          args = Object.values(JSON.parse(tc.function?.arguments || '{}')).join(', ')
        } catch {
          /* ignore */
        }
        items.push({ kind: 'call', text: `${tc.function?.name}(${args})` })
      }
    } else if (m.role === 'tool') {
      items.push({ kind: 'obs', text: String(m.content || '').replace(/\s+/g, ' ').slice(0, 140) })
    }
  }
  return items
}

function Label({ children }) {
  return <h3 className="label-eyebrow mb-1.5 text-muted-foreground">{children}</h3>
}

// The decision surface: root cause → evidence → proposed fix → the Critic's
// challenge in its own voice → rollback guarantee → the full trace on demand,
// then the Approve/Reject gate (only while PROPOSED). Evidence is shown
// verbatim — redaction is applied upstream, never here.
export function CaseFile({ incident, record, onDecision, onRetry, onDismiss, busy, variant = 'review' }) {
  const { diagnosis, plan, trace, execution } = record
  const outcome = execution ? executionOutcome(execution) : null
  const OutcomeIcon = outcome ? OUTCOME_ICON[outcome.icon] ?? CheckCircle2 : null
  const [showTrace, setShowTrace] = useState(false)
  const proposed = incident.state === 'proposed'
  const failed = incident.state === 'diagnosis_failed'
  const hero = variant === 'hero'
  const items = traceItems(trace)
  // Nothing to render yet (still diagnosing, or no record) — show a calm
  // placeholder instead of an empty box.
  const hasContent =
    diagnosis?.root_cause ||
    diagnosis?.evidence?.length > 0 ||
    diagnosis?.critic_reason ||
    plan ||
    items.length > 0
  const status = liveStatus(incident)
  const investigating = !isTerminal(incident.state) && incident.state !== 'muted' && !failed
  const dismissible = canDismiss(incident.state)

  return (
    <div
      className={cn('rounded-lg p-5', hero ? 'border' : 'glass')}
    >
      {hero && (
        <div className="mb-3 flex items-center gap-2" style={{ color: 'var(--sev-unhealthy)' }}>
          <span className="size-1.5 rounded-full" style={{ backgroundColor: 'var(--sev-unhealthy)' }} />
          <span className="label-eyebrow">Waiting for your approval</span>
          <span className="font-data ml-auto text-sm text-foreground">{incident.target}</span>
        </div>
      )}

      {!hasContent && (
        <div className="flex items-center gap-3 py-2 text-sm text-muted-foreground">
          {failed ? (
            <span>Diagnosis failed: {incident.failure_reason || 'no details were recorded'}.</span>
          ) : investigating ? (
            <>
              {status.active && <Loader2 className="size-4 shrink-0 animate-spin text-primary" />}
              {status.active ? 'Digdown is working out why…' : status.text}
            </>
          ) : (
            <span>No diagnosis was recorded for this incident.</span>
          )}
        </div>
      )}

      {/* root cause */}
      {diagnosis?.root_cause && (
        <section>
          <Label>What went wrong</Label>
          <p className="max-w-[80ch] text-sm leading-relaxed text-foreground">{diagnosis.root_cause}</p>
        </section>
      )}

      {/* evidence (verbatim — already redacted upstream) */}
      {diagnosis?.evidence?.length > 0 && (
        <section className="mt-4">
          <Label>How Digdown knows</Label>
          <ul className="space-y-1.5">
            {diagnosis.evidence.map((e, i) => (
              <li key={i} className="flex gap-2 text-sm text-muted-foreground">
                <span className="mt-2 size-1 shrink-0 rounded-full bg-muted-foreground/60" />
                <span className="font-data min-w-0 break-words text-[13px] leading-relaxed">{e}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* proposed fix */}
      {plan && plan.kind !== 'manual' && plan.actions?.length > 0 && (
        <section className="mt-4">
          <Label>Proposed fix</Label>
          <p className="mb-2 text-sm leading-relaxed text-foreground">{plan.summary}</p>
          <ul className="space-y-2">
            {plan.actions.map((a, i) => (
              <li key={i} className="rounded-md border bg-background px-3 py-2">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="label-eyebrow text-foreground">{actionLabel(a.type)}</span>
                  <span className="font-data text-sm text-foreground">{a.target}</span>
                  <span
                    className="label-eyebrow ml-auto rounded-sm border px-1.5 py-px"
                    style={{
                      color: RISK_COLOR[plan.risk] || 'var(--muted-foreground)',
                      borderColor: `color-mix(in oklch, ${RISK_COLOR[plan.risk] || 'var(--muted-foreground)'} 26%, transparent)`,
                    }}
                  >
                    {plan.risk === 'low' ? 'Low' : plan.risk === 'medium' ? 'Medium' : 'High'} risk
                  </span>
                </div>
                {a.params && Object.keys(a.params).length > 0 && (
                  <div className="font-data mt-1 text-xs text-muted-foreground">
                    {actionParamsLabel(a)}
                  </div>
                )}
                {a.rationale && <p className="mt-1 text-xs text-muted-foreground">{a.rationale}</p>}
              </li>
            ))}
          </ul>
          <p className="mt-1.5 text-xs text-muted-foreground">
            Digdown can only run a short list of safe actions, and this is one of them.
          </p>
        </section>
      )}

      {/* outcome — what actually happened when the fix ran */}
      {execution && outcome && (
        <section className="mt-4">
          <div className="flex items-baseline justify-between">
            <Label>What happened</Label>
            <span className="font-data text-xs text-muted-foreground">{relTime(execution.created_at)}</span>
          </div>
          <p className="flex items-center gap-1.5 text-sm" style={{ color: outcome.tone }}>
            <OutcomeIcon className="size-3.5 shrink-0" />
            {outcome.label}
          </p>
          {ranByAutoHeal(record) && (
            <p className="mt-1 text-sm text-muted-foreground">Auto heal ran this fix without waiting for approval.</p>
          )}
          {execution.steps?.length > 0 && (
            <ul className="mt-2 space-y-1.5">
              {execution.steps.map((s, i) => (
                <li
                  key={i}
                  className="flex flex-wrap items-center gap-2 rounded-md border bg-background px-3 py-1.5 text-sm"
                >
                  {s.ok ? (
                    <Check className="size-3.5 shrink-0" style={{ color: 'var(--live)' }} />
                  ) : (
                    <X className="size-3.5 shrink-0" style={{ color: 'var(--sev-crash)' }} />
                  )}
                  <span className="label-eyebrow text-foreground">{actionLabel(s.type)}</span>
                  <span className="font-data text-foreground">{s.target}</span>
                  {s.rolled_back && (
                    <span className="ml-auto rounded-sm border px-1.5 py-px text-xs text-muted-foreground">
                      Undone
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
          {execution.detail && (
            <p className="mt-1.5 text-xs text-muted-foreground">{execution.detail}</p>
          )}
          {execution.verified && keepFixHint(plan) && (
            <p className="mt-2 rounded-md border bg-background px-3 py-2 text-sm text-foreground">{keepFixHint(plan)}</p>
          )}
        </section>
      )}

      {/* manual recommendation (no safe automated fix) */}
      {plan?.kind === 'manual' && (
        <section className="mt-4">
          <Label>What to do</Label>
          <div className="rounded-md border border-dashed bg-muted/20 p-3">
            <p className="text-sm leading-relaxed text-foreground">{plan.manual_fix}</p>
            <p className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground">
              <Wrench className="size-3.5 shrink-0" /> Digdown can't safely do this one, so it needs you.
            </p>
          </div>
        </section>
      )}

      {/* critic verdict — in its own voice */}
      {(plan?.critic2_reason || diagnosis?.critic_reason) && (
        <section className="mt-4">
          <Label>Second opinion</Label>
          <div
            className="border-l-2 pl-3"
            style={{ borderColor: 'var(--sev-unhealthy)' }}
          >
            <p className="text-[13px] leading-relaxed text-foreground">
              {plan?.critic2_reason || diagnosis?.critic_reason}
            </p>
            <p
              className="mt-1.5 flex items-center gap-1.5 text-xs"
              style={{ color: diagnosis?.critic_supported === false ? 'var(--sev-unhealthy)' : 'var(--live)' }}
            >
              {diagnosis?.critic_supported === false ? (
                <ShieldAlert className="size-3.5" />
              ) : (
                <ShieldCheck className="size-3.5" />
              )}
              {diagnosis?.critic_supported === false ? 'A second AI check disagreed with this' : 'A second AI check agreed with this'}
            </p>
          </div>
        </section>
      )}

      {!execution && autoHealSkipReason(record) && (
        <section className="mt-4">
          <Label>Why auto heal left this for you</Label>
          <p className="text-[13px] leading-relaxed text-muted-foreground">{autoHealSkipReason(record)}</p>
        </section>
      )}

      {/* rollback guarantee (stated in words — no fabricated numbers) */}
      {plan && plan.kind !== 'manual' && !execution && (
        <section className="mt-4">
          <Label>If the fix doesn&apos;t work</Label>
          <p className="text-[13px] leading-relaxed text-muted-foreground">
            Digdown records how things are now, applies the fix, then checks the container stays
            healthy. If it doesn&apos;t, Digdown undoes any settings it changed and tells you.
          </p>
        </section>
      )}

      {/* full reasoning trace on demand */}
      {items.length > 0 && (
        <section className="mt-4">
          <button
            type="button"
            onClick={() => setShowTrace((s) => !s)}
            className="label-eyebrow text-muted-foreground hover:text-foreground"
            aria-expanded={showTrace}
          >
            {showTrace ? 'Hide' : 'Show'} what the AI checked ({items.filter((i) => i.kind === 'call').length} steps)
          </button>
          {showTrace && (
            <ol className="mt-2 space-y-1.5 rounded-lg border bg-muted/30 p-3">
              {items.map((it, i) => (
                <li key={i} className="flex gap-2 text-[13px] leading-relaxed">
                  {it.kind === 'call' ? (
                    <>
                      <ArrowRight className="mt-0.5 size-3.5 shrink-0 text-primary" />
                      <span className="font-data min-w-0 break-words text-foreground">{it.text}</span>
                    </>
                  ) : (
                    <span className="font-data min-w-0 break-words pl-5 text-muted-foreground">{it.text}</span>
                  )}
                </li>
              ))}
            </ol>
          )}
        </section>
      )}

      {/* the gate — only while proposed */}
      {proposed && (
        <div className="mt-5 flex items-center gap-2.5">
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
        </div>
      )}
      {/* failed diagnosis — offer a manual retry */}
      {failed && (
        <div className="mt-5 flex items-center gap-2.5">
          <Button disabled={busy} onClick={() => onRetry(incident)}>
            {busy ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
            Retry diagnosis
          </Button>
        </div>
      )}
      {/* dismiss — escape hatch to close a stuck / ended incident to Done */}
      {dismissible && (
        <div className="mt-3 flex justify-end">
          <DismissButton onDismiss={() => onDismiss(incident)} disabled={busy} />
        </div>
      )}
    </div>
  )
}
