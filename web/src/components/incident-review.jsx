import { useMemo, useState } from 'react'
import { ChevronLeft, ChevronRight, ArrowLeft, Radio, Eye, Clock, Fingerprint, History } from 'lucide-react'
import { Separator } from '@/components/ui/separator'
import { SignalIcon } from '@/components/signal-icon'
import { StateBadge } from '@/components/state-badge'
import { CaseFile } from '@/components/case-file'
import { useIncidentRecord } from '@/hooks/useIncidentRecord'
import { AgentActivity } from '@/components/agent-activity'
import { RecurrenceChip } from '@/components/recurrence-chip'
import { signalMeta, detailLabel, formatDetailValue, absTime, relTime, trackRecord, OUTCOME_TONE } from '@/lib/domain'
import { Postmortem } from '@/components/postmortem'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'

function Row({ label, children }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5">
      <span className="shrink-0 text-sm text-muted-foreground">{label}</span>
      <span className="font-data min-w-0 break-words text-right text-sm text-foreground">{children}</span>
    </div>
  )
}

// A dedup key is a long, delimiter-joined identifier. Break it only at its
// ':' / '/' / '-' boundaries (via <wbr>, which is copy-safe) so it wraps as
// whole segments instead of snapping mid-token like "htt|p_error".
function DedupKey({ value }) {
  const parts = String(value).split(/(?<=[:/-])/)
  return parts.map((p, i) => (
    <span key={i}>{p}{i < parts.length - 1 && <wbr />}</span>
  ))
}

function Section({ icon: Icon, title, children }) {
  return (
    <section className="space-y-3">
      <h3 className="label-eyebrow flex items-center gap-2 text-muted-foreground">
        <Icon className="size-3.5" />
        {title}
      </h3>
      {children}
    </section>
  )
}

// The focused review: the case file plus the raw signal/timeline/identifier
// facts. Replaces the slide-out Sheet — it owns the column while open.
export function IncidentReview({ incident, incidents = [], mock, onDecision, onRetry, onDismiss, onOpenHistory, busy, position, total, onPrev, onNext, onClose, liveSteps }) {
  const record = useIncidentRecord(incident, true, mock)
  const track = useMemo(
    () => trackRecord(incidents, incident.target, {
      signalType: incident.signal?.type,
      signature: incident.error_signature,
    }),
    [incidents, incident.target, incident.signal?.type, incident.error_signature],
  )
  const [reviewTab, setReviewTab] = useState('casefile')
  const meta = signalMeta(incident.signal?.type)
  const detailObj = incident.signal?.detail || {}
  // Format each value and drop the empties — a grafana alert's `labels` is a
  // nested object (would print "[object Object]") and `value`/`startsAt` are
  // often blank, which otherwise leaves empty rows in the panel.
  const detailRows = Object.entries(detailObj)
    .map(([k, v]) => [k, formatDetailValue(k, v)])
    .filter(([, v]) => v !== '')

  return (
    <div className="w-full">
      <div
        className="sticky z-10 mb-3 flex items-center justify-between border-b bg-background py-2"
        style={{ top: 'calc(var(--topbar, 0px) + var(--chrome-h, 0px))' }}
      >
        <button
          type="button"
          onClick={onClose}
          className="glass glass-raise flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs text-foreground"
        >
          <ArrowLeft className="size-4" /> Back to incidents <span className="font-data ml-1 text-muted-foreground max-sm:hidden">esc</span>
        </button>
        {total > 1 && (
          <div className="glass flex items-center gap-3 rounded-lg px-3 py-1.5">
            <button type="button" onClick={onPrev} disabled={position <= 1} aria-label="Previous incident"
              className="text-foreground/70 transition-colors hover:text-foreground disabled:opacity-30">
              <ChevronLeft className="size-4" />
            </button>
            <span className="font-data text-xs tabular-nums text-foreground">{position} of {total}</span>
            <button type="button" onClick={onNext} disabled={position >= total} aria-label="Next incident"
              className="text-foreground/70 transition-colors hover:text-foreground disabled:opacity-30">
              <ChevronRight className="size-4" />
            </button>
          </div>
        )}
      </div>

      <div className="mb-6 flex items-start gap-3.5">
        <SignalIcon type={incident.signal?.type} size="lg" />
        <div className="min-w-0 flex-1 pt-0.5">
          <h1 className="font-data truncate text-lg leading-tight text-foreground">{incident.target}</h1>
          <p className="mt-1 text-sm" style={{ color: `var(--${meta.token})` }}>{meta.label}</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <RecurrenceChip record={track} onClick={() => onOpenHistory?.(incident.target)} />
          <StateBadge incident={incident} />
        </div>
      </div>

      <Tabs value={reviewTab} onValueChange={setReviewTab} className="mb-6">
        <TabsList variant="line">
          <TabsTrigger value="casefile" className="label-eyebrow">Details</TabsTrigger>
          <TabsTrigger value="postmortem" className="label-eyebrow">Report</TabsTrigger>
        </TabsList>
      </Tabs>

      {reviewTab === 'postmortem' ? (
        <Postmortem incident={incident} record={record} track={track} onOpenHistory={onOpenHistory} />
      ) : (
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem] lg:items-start">
          <div className="min-w-0 space-y-6">
            {incident.state === 'investigating' && (
              <AgentActivity steps={liveSteps?.[incident.id]} />
            )}
            <CaseFile incident={incident} record={record} onDecision={onDecision} onRetry={onRetry} onDismiss={onDismiss} busy={busy} variant="review" />
          </div>

          <aside className="space-y-6">
          <Section icon={Radio} title="What was detected">
            <div className="glass rounded-lg px-4 py-1">
              <Row label="Problem">{meta.label}</Row>
              <Separator />
              <Row label="Noticed by">{incident.signal?.source}</Row>
              <Separator />
              <Row label="Affected">{incident.target}</Row>
            </div>
          </Section>

          {detailRows.length > 0 && (
            <Section icon={Eye} title="Details">
              <div className="glass rounded-lg px-4 py-1">
                {detailRows.map(([k, v], i) => (
                  <div key={k}>
                    {i > 0 && <Separator />}
                    <Row label={detailLabel(k)}>{v}</Row>
                  </div>
                ))}
              </div>
            </Section>
          )}

          <Section icon={Clock} title="When">
            <div className="glass rounded-lg px-4 py-1">
              <Row label="Detected">
                {absTime(incident.created_at)}{' '}
                <span className="text-muted-foreground">({relTime(incident.created_at)})</span>
              </Row>
              <Separator />
              <Row label="Last changed">{absTime(incident.updated_at)}</Row>
            </div>
          </Section>

          <Section icon={History} title="History">
            <div className="glass rounded-lg p-4">
              {track.occurrencesTotal <= 1 ? (
                <p className="text-sm text-muted-foreground">This is the first time Digdown has seen this fail.</p>
              ) : (
                <>
                  <p className="text-sm text-foreground">
                    Failed <span className="font-data">{track.occurrencesTotal}</span> times.
                    {track.lastSeenIso && (
                      <span className="text-muted-foreground"> Last time was {relTime(track.lastSeenIso)} ago.</span>
                    )}
                  </p>
                  {track.outcomeHistory.length > 0 && (
                    <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
                      {track.outcomeHistory.slice(0, 16).map((o) => (
                        <span
                          key={o.id}
                          className="size-2 rounded-full"
                          style={{ backgroundColor: OUTCOME_TONE[o.outcome] }}
                          title={`${signalMeta(o.signalType).short}: ${o.outcome}`}
                        />
                      ))}
                    </div>
                  )}
                </>
              )}
              <button
                type="button"
                onClick={() => onOpenHistory?.(incident.target)}
                className="label-eyebrow mt-3 text-muted-foreground hover:text-foreground"
              >
                See full history
              </button>
            </div>
          </Section>

          <Section icon={Fingerprint} title="For reference">
            <div className="glass rounded-lg px-4 py-1">
              <Row label="Incident">#{incident.id}</Row>
              <Separator />
              <div className="py-1.5">
                <span className="text-sm text-muted-foreground">Internal key</span>
                <p className="font-data mt-1 break-words text-sm text-foreground">
                  <DedupKey value={incident.dedup_key} />
                </p>
              </div>
            </div>
          </Section>
          </aside>
        </div>
      )}
    </div>
  )
}
