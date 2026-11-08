import { useMemo } from 'react'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { InsightsKpi } from '@/components/insights-kpi'
import { WINDOWS, windowRange, windowStats, formatDuration, kpiDelta, bucketSeries, topOffenders, SIGNALS } from '@/lib/domain'
import { usePref } from '@/lib/prefs'

const PCT = (r) => (r == null ? 'None yet' : `${Math.round(r * 100)}%`)

const TYPES = Object.keys(SIGNALS)
const OUTCOME_META = [
  { key: 'resolved', label: 'Fixed', tone: 'var(--live)' },
  { key: 'rolled_back', label: 'Fix undone', tone: 'var(--sev-unhealthy)' },
  { key: 'failed', label: 'Fix failed', tone: 'var(--sev-crash)' },
  { key: 'closed_no_fix', label: 'No automatic fix', tone: 'var(--muted-foreground)' },
  { key: 'rejected', label: 'Rejected', tone: 'var(--muted-foreground)' },
]
const BY_SEVERITY = Object.values(SIGNALS).sort((a, b) => b.rank - a.rank)

export function Insights({ incidents, onOpenHistory }) {
  const [windowKey, setWindowKey] = usePref('trend-range', ['24h', '7d', '30d'], '7d')
  const range = useMemo(() => windowRange(windowKey), [windowKey])
  const cur = useMemo(() => windowStats(incidents, { start: range.start, end: range.end }), [incidents, range])
  const prev = useMemo(
    () => windowStats(incidents, { start: range.prevStart, end: range.prevEnd }),
    [incidents, range],
  )

  const series = useMemo(
    () => bucketSeries(incidents, { end: range.end, bucket: range.bucket, buckets: range.buckets }),
    [incidents, range],
  )
  const offenders = useMemo(
    () => topOffenders(incidents, { start: range.start, end: range.end }),
    [incidents, range],
  )
  const peak = useMemo(
    () => Math.max(1, ...series.map((r) => TYPES.reduce((s, t) => s + r[t], 0))),
    [series],
  )
  // Total drawn by the bars — sourced from the (bucket-aligned) series, not
  // windowStats' exact rolling window, so the header always matches the bars.
  const seriesTotal = useMemo(
    () => series.reduce((s, r) => s + TYPES.reduce((a, t) => a + r[t], 0), 0),
    [series],
  )
  const outcomeTotal = useMemo(
    () => OUTCOME_META.reduce((s, o) => s + cur.byOutcome[o.key], 0),
    [cur],
  )
  const maxType = Math.max(1, ...Object.values(cur.byType))

  return (
    <section aria-label="Trends" className="flex flex-col gap-6">
      <ToggleGroup
        type="single"
        value={windowKey}
        onValueChange={(v) => v && setWindowKey(v)}
        variant="outline"
        size="sm"
        className="self-start"
      >
        {Object.keys(WINDOWS).map((k) => (
          <ToggleGroupItem key={k} value={k} className="label-eyebrow px-3">
            {k}
          </ToggleGroupItem>
        ))}
      </ToggleGroup>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <InsightsKpi
          label="Detected"
          value={cur.detected}
          delta={kpiDelta(cur.detected, prev.detected, 'down')}
        />
        <InsightsKpi
          label="Average time to fix"
          value={formatDuration(cur.mttrMs)}
          delta={kpiDelta(cur.mttrMs, prev.mttrMs, 'down')}
          formatMagnitude={formatDuration}
          hint="From when Digdown noticed the problem to when it was fixed. Lower is better. Often called MTTR."
        />
        <InsightsKpi
          label="Fixes that worked"
          value={PCT(cur.successRate)}
          delta={kpiDelta(cur.successRate, prev.successRate, 'up')}
          formatMagnitude={(n) => `${Math.round(n * 100)}%`}
        />
        <InsightsKpi
          label="Fixed"
          value={cur.resolved}
          delta={kpiDelta(cur.resolved, prev.resolved, 'up')}
        />
      </div>

      {/* Volume over time — hand-rolled stacked bars with a count above each bar */}
      <div className="glass rounded-xl p-3.5">
        <div className="flex items-baseline justify-between">
          <h3 className="label-eyebrow text-muted-foreground">Incidents over time</h3>
          <span className="font-data text-xs text-muted-foreground/70">
            {seriesTotal} in total, at most {peak} per {range.bucket}
          </span>
        </div>
        <p className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
          <span className="flex items-center gap-1.5"><span className="size-2 rounded-[2px] bg-sev-crash" aria-hidden /> Stopped working</span>
          <span className="flex items-center gap-1.5"><span className="size-2 rounded-[2px] bg-sev-unhealthy" aria-hidden /> Warning signs</span>
        </p>
        <div
          className="mt-4 flex h-40 items-stretch gap-1"
          role="img"
          aria-label={`Incident volume per ${range.bucket}; ${seriesTotal} total`}
        >
          {series.map((row) => {
            const total = TYPES.reduce((s, t) => s + row[t], 0)
            return (
              <div
                key={row.t}
                className="flex flex-1 flex-col justify-end gap-0.5"
                title={`${row.label}: ${total} incident${total === 1 ? '' : 's'}`}
              >
                {total > 0 && (
                  <span className="text-center font-data text-[0.625rem] leading-none tabular-nums text-foreground/80">
                    {total}
                  </span>
                )}
                <div
                  className="flex flex-col-reverse overflow-hidden rounded-[2px]"
                  style={{
                    height: total ? `${(total / peak) * 82}%` : '2px',
                    backgroundColor: total ? 'transparent' : 'var(--border)',
                  }}
                >
                  {BY_SEVERITY.filter((s) => row[s.key] > 0).map((s) => (
                    <span
                      key={s.key}
                      style={{ height: `${(row[s.key] / total) * 100}%`, backgroundColor: `var(--${s.token})` }}
                    />
                  ))}
                </div>
              </div>
            )
          })}
        </div>
        <div className="mt-2 flex justify-between text-xs text-muted-foreground">
          <span>{series[0]?.label}</span>
          {series.length > 2 && <span>{series[Math.floor(series.length / 2)]?.label}</span>}
          <span>now</span>
        </div>
      </div>

      {/* Outcome breakdown — one stacked horizontal bar */}
      <div className="glass rounded-xl p-3.5">
        <div className="flex items-baseline justify-between">
          <h3 className="label-eyebrow text-muted-foreground">How they ended</h3>
          {outcomeTotal > 0 && (
            <span className="font-data text-xs text-muted-foreground/70">{outcomeTotal} total</span>
          )}
        </div>
        {outcomeTotal === 0 ? (
          <p className="mt-3 text-sm text-muted-foreground">Nothing finished in this period.</p>
        ) : (
          <>
            <div className="mt-3 flex h-3 w-full overflow-hidden rounded-full">
              {OUTCOME_META.filter((o) => cur.byOutcome[o.key] > 0).map((o) => (
                <span
                  key={o.key}
                  style={{ width: `${(cur.byOutcome[o.key] / outcomeTotal) * 100}%`, backgroundColor: o.tone }}
                  title={`${o.label}: ${cur.byOutcome[o.key]}`}
                />
              ))}
            </div>
            <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1.5">
              {OUTCOME_META.filter((o) => cur.byOutcome[o.key] > 0).map((o) => (
                <span key={o.key} className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <span className="size-2 rounded-[2px]" style={{ backgroundColor: o.tone }} />
                  {o.label}
                  <span className="font-data tabular-nums text-foreground">{cur.byOutcome[o.key]}</span>
                  <span className="font-data tabular-nums text-muted-foreground/60">
                    {Math.round((cur.byOutcome[o.key] / outcomeTotal) * 100)}%
                  </span>
                </span>
              ))}
            </div>
          </>
        )}
      </div>

      {/* Detection by type — windowed bar list (mirrors Telemetry) */}
      <div className="glass rounded-xl p-3.5">
        <div className="flex items-baseline justify-between">
          <h3 className="label-eyebrow text-muted-foreground">What kind of failure</h3>
          {cur.detected > 0 && (
            <span className="font-data text-xs text-muted-foreground/70">{cur.detected} total</span>
          )}
        </div>
        {cur.detected === 0 ? (
          <p className="mt-3 text-sm text-muted-foreground">Nothing failed in this period.</p>
        ) : (
          <ul className="mt-3 space-y-1.5">
            {BY_SEVERITY.filter((s) => (cur.byType[s.key] || 0) > 0).map((s) => {
              const n = cur.byType[s.key]
              return (
                <li key={s.key} className="flex items-center gap-3">
                  <span className="flex w-28 shrink-0 items-center gap-2 text-xs text-muted-foreground">
                    <span className="size-2 shrink-0 rounded-[2px]" style={{ backgroundColor: `var(--${s.token})` }} />
                    <span className="truncate">{s.label}</span>
                  </span>
                  <span className="relative h-1.5 flex-1 overflow-hidden rounded-[3px] bg-muted/25">
                    <span
                      className="absolute inset-y-0 left-0 rounded-[3px]"
                      style={{ width: `${(n / maxType) * 100}%`, backgroundColor: `var(--${s.token})` }}
                    />
                  </span>
                  <span className="font-data w-6 shrink-0 text-right text-sm tabular-nums text-foreground">{n}</span>
                </li>
              )
            })}
          </ul>
        )}
      </div>
      {/* Top offenders this window — opens the per-target history panel */}
      {offenders.length > 0 && (
        <div className="glass rounded-xl p-3.5">
          <h3 className="label-eyebrow text-muted-foreground">Failed most often</h3>
          <ul className="mt-3 flex flex-col gap-1">
            {offenders.map((o) => (
              <li key={o.target}>
                <button
                  type="button"
                  onClick={() => onOpenHistory?.(o.target)}
                  aria-label={`${o.target} history`}
                  className="flex w-full items-center gap-3 rounded-md px-1.5 py-1.5 text-left transition-colors hover:bg-accent/40 focus-visible:ring-2 focus-visible:ring-ring/60 focus-visible:outline-none"
                >
                  <span className="font-data min-w-0 flex-1 truncate text-sm text-foreground">{o.target}</span>
                  <span className="font-data text-xs tabular-nums text-muted-foreground">
                    {o.count}×
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
