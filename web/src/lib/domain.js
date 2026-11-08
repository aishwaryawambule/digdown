// The Digdown domain model, mirrored from the backend (digdown/models.py).
// This is the single source of truth for how each signal type and incident
// state is named, ranked, and colored in the UI. Color encodes the taxonomy —
// it is data, not decoration.

// What Digdown can currently do, which decides how some states should read. Saying
// "waiting for diagnosis" when no model is connected would be a promise it can't keep.
let _runtime = { aiEnabled: true, mode: 'execute' }

/** Called by the dashboard whenever Digdown's status changes. */
export function setRuntime({ aiEnabled = true, mode = 'execute' } = {}) {
  _runtime = { aiEnabled, mode }
}

/** The action a plan will take, in words. */
export const ACTION_LABELS = {
  restart: 'Restart',
  recreate: 'Recreate',
  start: 'Start',
  set_env_var: 'Change a setting',
  set_resource_limit: 'Change memory limit',
  clean_docker: 'Clear unused Docker data',
}

export function actionLabel(type) {
  return ACTION_LABELS[type] || type
}

// Signals are grouped by the SOURCE that detected them. It began with only the
// container source; v1 (the Probe seam) adds app-level sources — HTTP, logs,
// metrics — so the taxonomy and the filter are labelled by category from the
// start.
export const SIGNAL_CATEGORIES = {
  container: { key: 'container', label: 'Docker', order: 0 },
  http: { key: 'http', label: 'HTTP', order: 1 },
  logs: { key: 'logs', label: 'Logs', order: 2 },
  metrics: { key: 'metrics', label: 'Metrics', order: 3 },
  alert: { key: 'alert', label: 'Alert', order: 4 },
}

/** Signal taxonomy: what kind of failure was detected, and which source saw it. */
export const SIGNALS = {
  // ── Container source (Docker / host) ──
  oom: {
    key: 'oom',
    label: 'Out of memory',
    short: 'Out of memory',
    token: 'sev-oom',
    rank: 5,
    category: 'container',
    blurb: 'It ran out of memory and the system stopped it',
  },
  memory_pressure: {
    key: 'memory_pressure',
    label: 'Memory nearly full',
    short: 'Memory high',
    token: 'sev-mempressure',
    rank: 4,
    category: 'container',
    blurb: 'Close to its memory limit, and likely to be stopped soon',
  },
  crash: {
    key: 'crash',
    label: 'Crashed',
    short: 'Crashed',
    token: 'sev-crash',
    rank: 4,
    category: 'container',
    blurb: 'It stopped with an error',
  },
  restart_loop: {
    key: 'restart_loop',
    label: 'Restart loop',
    short: 'Restart loop',
    token: 'sev-restart',
    rank: 3,
    category: 'container',
    blurb: 'It keeps restarting and can\'t stay up',
  },
  unhealthy: {
    key: 'unhealthy',
    label: 'Unhealthy',
    short: 'Unhealthy',
    token: 'sev-unhealthy',
    rank: 2,
    category: 'container',
    blurb: 'Its health check is failing',
  },
  disk_full: {
    key: 'disk_full',
    label: 'Disk nearly full',
    short: 'Disk full',
    token: 'sev-disk',
    rank: 1,
    category: 'container',
    blurb: 'This computer is running out of disk space',
  },
  stopped: {
    key: 'stopped',
    label: 'Stopped',
    short: 'Stopped',
    token: 'sev-stopped',
    rank: 4,
    category: 'container',
    blurb: 'It stopped cleanly but should be running',
  },
  created: {
    key: 'created',
    label: 'Never started',
    short: 'Never started',
    token: 'sev-created',
    rank: 3,
    category: 'container',
    blurb: 'It was created but failed to start',
  },
  paused: {
    key: 'paused',
    label: 'Paused',
    short: 'Paused',
    token: 'sev-paused',
    rank: 2,
    category: 'container',
    blurb: 'The container is paused',
  },
  // ── HTTP source (v1 — the Probe seam) ──
  http_error: {
    key: 'http_error',
    label: 'HTTP error',
    short: 'HTTP error',
    token: 'sev-http',
    rank: 5,
    category: 'http',
    blurb: 'The address returned an error, timed out, or refused the connection',
  },
  slow_response: {
    key: 'slow_response',
    label: 'Slow response',
    short: 'Slow',
    token: 'sev-latency',
    rank: 2,
    category: 'http',
    blurb: 'The address answered, but too slowly',
  },
  // ── Log source (v1) ──
  log_error_spike: {
    key: 'log_error_spike',
    label: 'Log error spike',
    short: 'Log errors',
    token: 'sev-log',
    rank: 3,
    category: 'logs',
    blurb: 'A sudden rise in errors in its logs',
  },
  // ── Metrics source (v1) ──
  metric_breach: {
    key: 'metric_breach',
    label: 'Metric over its limit',
    short: 'Metric',
    token: 'sev-metric',
    rank: 3,
    category: 'metrics',
    blurb: 'A watched metric crossed its threshold',
  },
  // ── Alert source (external monitor: grafana) ──
  alert_firing: {
    key: 'alert_firing',
    label: 'Alert',
    short: 'Alert',
    token: 'sev-metric',
    rank: 3,
    category: 'alert',
    blurb: 'Grafana raised an alert',
  },
}

/** The category (detecting source) a signal type belongs to. */
export function signalCategory(type) {
  return SIGNALS[type]?.category || 'container'
}

export function signalMeta(type) {
  return (
    SIGNALS[type] || {
      key: type,
      label: type,
      short: String(type || '').toUpperCase(),
      token: 'muted-foreground',
      rank: 0,
      category: 'container',
      blurb: 'Unknown signal',
    }
  )
}

// Incident lifecycle. Explain-only mode stops early, but the UI
// understands the full lifecycle so it is ready as later milestones land.
const TERMINAL = new Set([
  'resolved',
  'rejected',
  'rolled_back',
  'failed',
  'closed_no_fix',
])

// tone drives color: open = needs eyes, progress = being worked,
// good = resolved, bad = ended badly, muted = silenced.
export const STATES = {
  detected: { label: 'Detected', tone: 'open' },
  triaged: { label: 'Queued', tone: 'progress' },
  investigating: { label: 'Investigating', tone: 'progress' },
  diagnosed: { label: 'Cause found', tone: 'open' },
  diagnosis_failed: { label: 'Diagnosis failed', tone: 'bad' },
  proposed: { label: 'Fix ready', tone: 'open' },
  approved: { label: 'Approved', tone: 'progress' },
  executing: { label: 'Applying fix', tone: 'progress' },
  verifying: { label: 'Checking fix', tone: 'progress' },
  resolved: { label: 'Fixed', tone: 'good' },
  rejected: { label: 'Rejected', tone: 'muted' },
  rolled_back: { label: 'Fix undone', tone: 'bad' },
  failed: { label: 'Fix failed', tone: 'bad' },
  closed_no_fix: { label: 'Closed', tone: 'muted' },
  muted: { label: 'Muted', tone: 'muted' },
}

export function stateMeta(state) {
  return STATES[state] || { label: state, tone: 'progress' }
}

// How a resolved incident actually resolved — used to label it honestly.
// 'verified' = Digdown ran a fix and the verifier confirmed health.
// 'cleared'  = no verified fix (e.g. a Grafana alert simply stopped firing).
// NOTE: `execution` rides on the detail-fetched record and the mock, never on
// the list/stream incident the board renders, so against the live backend this
// returns 'cleared' for every resolved incident. In propose mode that is the
// honest answer (nothing was executed); the 'verified' branch lights up under
// /?mock and once a backend discriminator exposes execution on the payload.
export function resolutionKind(incident) {
  if (incident?.state !== 'resolved') return null
  return incident.execution?.verified ? 'verified' : 'cleared'
}

// Short badge label that respects how a resolved incident resolved: an
// auto-clear reads "Cleared" (muted), a verified fix keeps stateMeta's
// "Resolved" (good). Falls back to stateMeta for any non-resolved or nullish
// incident. NB: this returns stateMeta's tone vocabulary (good/muted, what
// StateBadge maps) — distinct from liveStatus's vocabulary (done/muted).
export function stateBadge(incident) {
  if (incident?.state === 'resolved' && resolutionKind(incident) === 'cleared') {
    return { label: 'Cleared', tone: 'muted' }
  }
  if (incident?.state === 'resolved' && incident.auto_healed) {
    return { label: 'Auto healed', tone: 'good' }
  }
  return stateMeta(incident?.state)
}

export function isTerminal(state) {
  return TERMINAL.has(state)
}

// States a person can manually dismiss (close to closed_no_fix). Mirrors the
// backend _DISMISSIBLE_STATES — the stuck / pre-decision non-terminal states.
const DISMISSIBLE = new Set(['detected', 'triaged', 'investigating', 'diagnosis_failed', 'diagnosed'])

/** Can a person manually dismiss an incident in this state? An approval that
 *  will never run (Digdown is only suggesting fixes) can be closed too. */
export function canDismiss(state) {
  return DISMISSIBLE.has(state) || (state === 'approved' && _runtime.mode !== 'execute')
}

/** An incident is "active" while it still wants attention. */
export function isActive(incident) {
  return !TERMINAL.has(incident.state) && incident.state !== 'muted'
}

// --- Mutes (runtime silence by target prefix) -----------------------------
// Mirrors the backend Mute.matches: a pattern silences a target by exact match
// or prefix, so muting `blog` covers the whole `blog-*` stack.
export function matchesMute(target, pattern) {
  return Boolean(target) && (target === pattern || target.startsWith(pattern))
}

/** Is this compose project silenced by any active mute? */
export function isProjectMuted(project, mutes = []) {
  return mutes.some((m) => matchesMute(project, m.pattern))
}

/** How a single unit is silenced:
 *  - 'self'  — its own exact-name mute (individually muted; can be unmuted here)
 *  - 'stack' — covered only by a broader prefix mute, e.g. its compose project
 *              (unmute at the stack level, not per-unit)
 *  - 'none'  — not silenced. */
export function unitMuteState(unit, mutes = []) {
  const name = unit?.name
  if (!name) return 'none'
  if (mutes.some((m) => m.pattern === name)) return 'self'
  if (mutes.some((m) => matchesMute(name, m.pattern))) return 'stack'
  return 'none'
}

/** The exact mute pattern silencing this unit — its own exact-name mute if it
 *  has one, otherwise the broader prefix (stack) mute covering it. This is the
 *  pattern to DELETE to unmute it; for a stack mute that lifts the whole stack.
 *  Returns null when the unit isn't muted. */
export function mutePatternFor(unit, mutes = []) {
  const name = unit?.name
  if (!name) return null
  const exact = mutes.find((m) => m.pattern === name)
  if (exact) return exact.pattern
  const covering = mutes.find((m) => matchesMute(name, m.pattern))
  return covering ? covering.pattern : null
}

// Render a plan action's params for humans: set_resource_limit shows the new
// limit in MB; everything else keeps the raw key=value join.
export function actionParamsLabel(action) {
  const p = action?.params || {}
  if (action?.type === 'set_resource_limit' && p.mem_limit_bytes != null) {
    return `New limit: ${Math.round(p.mem_limit_bytes / (1024 * 1024))} MB`
  }
  const entries = Object.entries(p)
  return entries.length ? entries.map(([k, v]) => `${k}=${v}`).join('  ') : ''
}

/** Compact relative time, e.g. "now", "4m", "3h", "2d". */
export function relTime(iso, now = Date.now()) {
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return 'Unknown'
  const s = Math.max(0, Math.round((now - t) / 1000))
  if (s < 45) return 'now'
  const m = Math.round(s / 60)
  if (m < 60) return `${m}m`
  const h = Math.round(m / 60)
  if (h < 24) return `${h}h`
  const d = Math.round(h / 24)
  return `${d}d`
}

/** Full timestamp for tooltips. */
export function absTime(iso) {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return String(iso)
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

/** One-line summary of a signal's detail payload, e.g. "exit 137", "92% full". */
export function detailSummary(signal) {
  const d = signal?.detail || {}
  if (d.exit_code != null) return `exit ${d.exit_code}`
  if (d.percent != null) return `${Math.round(d.percent)}% full`
  if (d.restart_count != null) return `${d.restart_count} restarts`
  if (d.status != null) return `HTTP ${d.status}`
  if (d.latency_ms != null) return `${(d.latency_ms / 1000).toFixed(1)}s`
  if (d.count != null && d.pattern) return `${d.count}× ${d.pattern}`
  if (d.metric != null) return String(d.metric)
  return null
}

/** Flatten the live ReAct message stream into call / observation lines.
 *  Pure: shared by the case-file trace and the situation hero's live readout. */
export function flattenSteps(steps) {
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
      items.push({ kind: 'obs', text: String(m.content || '') })
    }
  }
  return items
}

/** The headline "what fired" text for the hero while there's no diagnosis yet:
 *  a grafana alert's own words (summary + description) or, for container / http
 *  / log / metric signals, the one-line detail summary. Returns null when the
 *  signal carries nothing worth showing, so the caller can fall back. */
export function firedDetail(signal) {
  const d = signal?.detail || {}
  const summary = d.summary || detailSummary(signal) || d.description || null
  if (!summary) return null
  const description = d.description && d.description !== summary ? d.description : null
  return { summary, description }
}

/** Human label for a detail key. */
export function detailLabel(key) {
  return (
    {
      exit_code: 'Exit code',
      percent: 'Disk used',
      restart_count: 'Restarts',
      status: 'Status',
      url: 'Endpoint',
      latency_ms: 'Latency',
      threshold_ms: 'Budget',
      pattern: 'Pattern',
      count: 'Matches',
      metric: 'Metric',
      // grafana alert (ALERT_FIRING) detail keys
      alertname: 'Alert name',
      summary: 'Summary',
      description: 'Description',
      labels: 'Labels',
      value: 'Value',
      fingerprint: 'Fingerprint',
      startsAt: 'Started',
    }[key] || key
  )
}

function scalar(v) {
  if (v == null) return ''
  return typeof v === 'object' ? JSON.stringify(v) : String(v)
}

/**
 * Render one detail value for the Evidence panel. A grafana alert's `labels`
 * arrives as a nested object — `String(obj)` would print "[object Object]", so
 * objects become a compact `k=v, k=v` line and arrays a comma list. Empty or
 * missing values collapse to '' so the caller can drop the row entirely;
 * falsy-but-real scalars (0, false) are preserved.
 */
export function formatDetailValue(key, value) {
  if (value == null || value === '') return ''
  if (key === 'percent' && typeof value === 'number') return `${Math.round(value)}%`
  if (Array.isArray(value)) return value.map(scalar).join(', ')
  if (typeof value === 'object') {
    return Object.entries(value)
      .map(([k, v]) => `${k}=${scalar(v)}`)
      .join(', ')
  }
  return String(value)
}

/**
 * Bucket detections into `hours` hourly bins ending now, counted per signal
 * type. Feeds the telemetry chart. Returns one row per hour, oldest first.
 */
export function bucketByHour(incidents, hours = 24, now = Date.now()) {
  const types = Object.keys(SIGNALS)
  const end = Math.floor(now / 3_600_000) * 3_600_000
  const rows = []
  for (let i = hours - 1; i >= 0; i--) {
    const start = end - i * 3_600_000
    const row = { t: start, label: hourLabel(start) }
    for (const ty of types) row[ty] = 0
    rows.push(row)
  }
  const first = end - (hours - 1) * 3_600_000
  for (const inc of incidents) {
    const t = new Date(inc.created_at).getTime()
    if (Number.isNaN(t) || t < first) continue
    const idx = Math.min(hours - 1, Math.floor((t - first) / 3_600_000))
    const ty = inc.signal?.type
    if (rows[idx] && ty in rows[idx]) rows[idx][ty] += 1
  }
  return rows
}

function hourLabel(ms) {
  const d = new Date(ms)
  const h = d.getHours()
  const am = h < 12
  const h12 = h % 12 || 12
  return `${h12}${am ? 'a' : 'p'}`
}

const _MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
function dayLabel(ms) {
  const d = new Date(ms)
  return `${_MONTHS[d.getMonth()]} ${d.getDate()}`
}

/** Distinct targets among the given incidents. */
export function distinctTargets(incidents) {
  return new Set(incidents.map((i) => i.target)).size
}

/** Count incidents created within the last `hours`. */
export function countWithin(incidents, hours, now = Date.now()) {
  const cutoff = now - hours * 3_600_000
  return incidents.filter((i) => new Date(i.created_at).getTime() >= cutoff).length
}

/** Count incidents resolved within the last `hours`, by updated_at. */
export function resolvedWithin(incidents, hours = 24, now = Date.now()) {
  const cutoff = now - hours * 3_600_000
  return incidents.filter(
    (i) => i.state === 'resolved' && new Date(i.updated_at).getTime() >= cutoff,
  ).length
}

// --- Recurrence / memory ---------------------------------------------------
// Per-target failure history, computed from the already-loaded incident list.
// Pure; `now` injectable for tests (module convention).
export const RECURRENCE = { repeat24h: 2, chronic24h: 3, chronic7d: 5 }

const DAY_MS = 86_400_000

// Coarse fix outcome derived from a terminal state alone (no per-incident fetch).
export function outcomeFromState(state) {
  if (state === 'resolved') return 'held'
  if (state === 'rolled_back' || state === 'failed') return 'reverted'
  if (state === 'closed_no_fix' || state === 'rejected' || state === 'muted') return 'none'
  return 'open' // any non-terminal state — still in flight
}

// Outcome → hue, reusing the signal palette (held = phosphor, reverted = amber).
export const OUTCOME_TONE = {
  held: 'var(--live)',
  reverted: 'var(--sev-unhealthy)',
  none: 'var(--muted-foreground)',
  open: 'var(--sev-disk)',
}

/**
 * Recurrence track record for one target. `opts.signalType` narrows counts to a
 * single failure type (so a chip can read "4th crash", not "4th incident");
 * omit for an all-signals view.
 */
export function trackRecord(incidents, target, opts = {}) {
  const { signalType = null, signature = null, now = Date.now() } = opts
  const mine = incidents
    .filter((i) =>
      signature && i.error_signature
        ? i.error_signature === signature
        : i.target === target && (!signalType || i.signal?.type === signalType))
    .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())

  const within = (ms) =>
    mine.filter((i) => now - new Date(i.created_at).getTime() <= ms).length
  const occurrences24h = within(DAY_MS)
  const occurrences7d = within(7 * DAY_MS)
  const occurrencesTotal = mine.length

  let status = 'first-seen'
  if (occurrences24h >= RECURRENCE.chronic24h || occurrences7d >= RECURRENCE.chronic7d) {
    status = 'chronic'
  } else if (occurrences24h >= RECURRENCE.repeat24h) {
    status = 'repeat'
  }

  // Prior occurrences = everything but the most recent (the "current" one).
  const prior = mine.slice(1)
  const outcomeHistory = prior.map((i) => ({
    id: i.id,
    signalType: i.signal?.type,
    createdAt: i.created_at,
    outcome: outcomeFromState(i.state),
  }))

  return {
    target,
    occurrences24h,
    occurrences7d,
    occurrencesTotal,
    status,
    lastSeenIso: prior[0]?.created_at ?? null,
    outcomeHistory,
    priorFixesReverted: outcomeHistory.filter((o) => o.outcome === 'reverted').length,
  }
}

const _STATUS_RANK = { 'first-seen': 0, repeat: 1, chronic: 2 }

/** Targets that keep failing — non-first-seen, chronic first, capped. */
export function repeatOffenders(incidents, opts = {}) {
  const { now = Date.now(), limit = 5 } = opts
  const targets = [...new Set(incidents.map((i) => i.target))]
  return targets
    .map((t) => trackRecord(incidents, t, { now }))
    .filter((r) => r.status !== 'first-seen')
    .sort((a, b) => {
      if (_STATUS_RANK[b.status] !== _STATUS_RANK[a.status]) {
        return _STATUS_RANK[b.status] - _STATUS_RANK[a.status]
      }
      if (b.occurrences24h !== a.occurrences24h) return b.occurrences24h - a.occurrences24h
      return new Date(b.lastSeenIso || 0).getTime() - new Date(a.lastSeenIso || 0).getTime()
    })
    .slice(0, limit)
}

/** One muted line for the hero when prior fixes were reverted; null otherwise. */
export function cameBackLine(record) {
  const n = record?.priorFixesReverted ?? 0
  if (n < 1) return null
  return n === 1
    ? 'An earlier fix was undone, and this came back.'
    : `Earlier fixes were undone ${n} times, and this keeps coming back.`
}

/** Presentation for the inline recurrence chip. Hidden for first-seen. */
export function recurrenceChip(record) {
  if (!record || record.status === 'first-seen') return { show: false, text: '', tone: '' }
  if (record.status === 'chronic') {
    return { show: true, text: `Keeps failing: ${record.occurrences7d} times in 7 days`, tone: 'var(--sev-oom)' }
  }
  return { show: true, text: `${record.occurrences24h} times today`, tone: 'var(--sev-unhealthy)' }
}

// --- Trends / insights (windowed analytics) --------------------------------
const HOUR_MS = 3_600_000

export const WINDOWS = {
  '24h': { ms: 24 * HOUR_MS, bucket: 'hour', buckets: 24 },
  '7d': { ms: 7 * DAY_MS, bucket: 'day', buckets: 7 },
  '30d': { ms: 30 * DAY_MS, bucket: 'day', buckets: 30 },
}

/** A window key → its [start,end), the previous equal-length window, and bucketing. */
export function windowRange(windowKey, now = Date.now()) {
  const w = WINDOWS[windowKey] || WINDOWS['7d']
  const start = now - w.ms
  return { start, end: now, prevStart: start - w.ms, prevEnd: start, bucket: w.bucket, buckets: w.buckets, ms: w.ms }
}

const _OUTCOME_STATES = ['resolved', 'rolled_back', 'failed', 'closed_no_fix', 'rejected']

/** Windowed metrics from the loaded incident list. Pure; half-open [start,end). */
export function windowStats(incidents, { start, end }) {
  const inWin = (iso) => {
    const t = new Date(iso).getTime()
    return !Number.isNaN(t) && t >= start && t < end
  }
  let detected = 0
  const byType = {}
  const byOutcome = { resolved: 0, rolled_back: 0, failed: 0, closed_no_fix: 0, rejected: 0 }
  let mttrSum = 0
  let mttrN = 0
  for (const i of incidents) {
    if (inWin(i.created_at)) {
      detected += 1
      const ty = i.signal?.type
      if (ty) byType[ty] = (byType[ty] || 0) + 1
    }
    if (_OUTCOME_STATES.includes(i.state) && inWin(i.updated_at)) {
      byOutcome[i.state] += 1
      if (i.state === 'resolved') {
        const c = new Date(i.created_at).getTime()
        const u = new Date(i.updated_at).getTime()
        if (!Number.isNaN(c) && !Number.isNaN(u) && u >= c) {
          mttrSum += u - c
          mttrN += 1
        }
      }
    }
  }
  const attempts = byOutcome.resolved + byOutcome.rolled_back + byOutcome.failed
  return {
    detected,
    resolved: byOutcome.resolved,
    mttrMs: mttrN ? mttrSum / mttrN : null,
    successRate: attempts ? byOutcome.resolved / attempts : null,
    noFix: byOutcome.closed_no_fix + byOutcome.rejected,
    byOutcome,
    byType,
  }
}

/**
 * Volume buckets ending at `end`, every signal type zero-initialised per row so a
 * calm window renders as calm. Independent of bucketByHour to avoid perturbing the
 * existing Telemetry card (small intentional overlap).
 */
export function bucketSeries(incidents, { end, bucket, buckets }) {
  const size = bucket === 'hour' ? HOUR_MS : DAY_MS
  const endAligned = Math.floor(end / size) * size
  const first = endAligned - (buckets - 1) * size
  const types = Object.keys(SIGNALS)
  const rows = []
  for (let i = 0; i < buckets; i++) {
    const t = first + i * size
    const row = { t, label: bucket === 'hour' ? hourLabel(t) : dayLabel(t) }
    for (const ty of types) row[ty] = 0
    rows.push(row)
  }
  for (const inc of incidents) {
    const t = new Date(inc.created_at).getTime()
    if (Number.isNaN(t) || t < first) continue
    const idx = Math.min(buckets - 1, Math.floor((t - first) / size))
    const ty = inc.signal?.type
    if (idx >= 0 && rows[idx] && ty in rows[idx]) rows[idx][ty] += 1
  }
  return rows
}

/** Targets ranked by incident count within the window; outcome mix per target. */
export function topOffenders(incidents, { start, end }, limit = 5) {
  const inWin = (iso) => {
    const t = new Date(iso).getTime()
    return !Number.isNaN(t) && t >= start && t < end
  }
  const byTarget = new Map()
  for (const i of incidents) {
    if (!inWin(i.created_at)) continue
    const cur = byTarget.get(i.target) || { target: i.target, count: 0, byOutcome: {}, _latest: 0 }
    cur.count += 1
    const o = outcomeFromState(i.state)
    cur.byOutcome[o] = (cur.byOutcome[o] || 0) + 1
    const t = new Date(i.created_at).getTime()
    if (t > cur._latest) cur._latest = t
    byTarget.set(i.target, cur)
  }
  return [...byTarget.values()]
    .sort((a, b) => b.count - a.count || b._latest - a._latest)
    .slice(0, limit)
    .map((o) => ({ target: o.target, count: o.count, byOutcome: o.byOutcome }))
}

/** Trend delta vs the previous window. goodDirection: 'up' or 'down' (which way is better). */
export function kpiDelta(curr, prev, goodDirection) {
  if (curr == null || prev == null) return { show: false }
  const diff = curr - prev
  const dir = diff === 0 ? 'flat' : diff > 0 ? 'up' : 'down'
  const good = dir === 'flat' ? null : goodDirection === 'up' ? diff > 0 : diff < 0
  const tone = dir === 'flat' ? 'var(--muted-foreground)' : good ? 'var(--live)' : 'var(--sev-unhealthy)'
  return { show: true, dir, diff, tone }
}

const _fmtNum = (n) => (Number.isInteger(n) ? String(n) : n.toFixed(1))

/** Compact duration: "<1m", "4m", "1.2h", "2d". null → "—". */
export function formatDuration(ms) {
  if (ms == null) return 'None yet'
  const m = ms / 60_000
  if (m < 1) return '<1m'
  if (m < 60) return `${Math.round(m)}m`
  const h = m / 60
  if (h < 24) return `${h < 10 ? _fmtNum(h) : Math.round(h)}h`
  const d = h / 24
  return `${d < 10 ? _fmtNum(d) : Math.round(d)}d`
}

/** Newest first; on ties, hardest-down signal first. */
export function bySeverityThenTime(a, b) {
  const ta = new Date(a.created_at).getTime()
  const tb = new Date(b.created_at).getTime()
  if (tb !== ta) return tb - ta
  return signalMeta(b.signal?.type).rank - signalMeta(a.signal?.type).rank
}

/**
 * Operational posture for the command-strip chip — honest, derived purely
 * from active incidents. nominal = nothing active; critical = a fix awaits
 * approval or a hardest-down signal (oom/crash) is active; elevated otherwise.
 */
export function posture(incidents) {
  const active = incidents.filter(isActive)
  if (active.length === 0) return 'nominal'
  const critical = active.some(
    (i) => i.state === 'proposed' || i.signal?.type === 'oom' || i.signal?.type === 'crash',
  )
  return critical ? 'critical' : 'elevated'
}

// ---- Lifecycle surfacing ----

// The four pipeline stages an incident passes through.
export const LIFECYCLE = ['Diagnose', 'Approve', 'Apply', 'Done']

const STAGE_OF = {
  detected: 0, triaged: 0, investigating: 0, diagnosis_failed: 0, diagnosed: 0,
  proposed: 1,
  approved: 2, executing: 2, verifying: 2,
  resolved: 3, rolled_back: 3, failed: 3, closed_no_fix: 3, rejected: 3, muted: 3,
}

/** Which stage an incident's state sits in (0..3 → LIFECYCLE), and — once
 *  Done — how it ended: 'good' | 'bad' | 'neutral', else null. */
export function lifecyclePhase(state) {
  const index = STAGE_OF[state] ?? 0
  let outcome = null
  if (index === 3) {
    if (state === 'resolved') outcome = 'good'
    else if (state === 'rolled_back' || state === 'failed') outcome = 'bad'
    else outcome = 'neutral'
  }
  return { index, outcome }
}

// state → live status shown on the queue row. tone drives color, active drives
// the spinner. Text is state-derived only — NEVER fabricate sub-counts.
const LIVE_STATUS = {
  detected: { text: 'Waiting for diagnosis', tone: 'diagnose', active: false },
  triaged: { text: 'Waiting for diagnosis', tone: 'diagnose', active: false },
  investigating: { text: 'Diagnosing…', tone: 'diagnose', active: true },
  diagnosed: { text: 'Cause found. Closes by itself once it recovers', tone: 'await', active: false },
  proposed: { text: 'Waiting for your approval', tone: 'await', active: false },
  approved: { text: 'Approved, applying the fix…', tone: 'apply', active: true },
  executing: { text: 'Applying the fix…', tone: 'apply', active: true },
  verifying: { text: 'Checking that the fix worked…', tone: 'apply', active: true },
  resolved: { text: 'Fixed and confirmed healthy', tone: 'done', active: false },
  rolled_back: { text: 'The fix didn\'t work, so it was undone', tone: 'bad', active: false },
  failed: { text: 'The fix couldn\'t be applied', tone: 'bad', active: false },
  closed_no_fix: { text: 'Closed with no automatic fix', tone: 'muted', active: false },
  rejected: { text: 'You rejected the fix', tone: 'muted', active: false },
  muted: { text: 'Muted', tone: 'muted', active: false },
}

export function liveStatus(incident) {
  if (incident.state === 'detected' && incident.parent_incident_id != null) {
    return { text: 'Waiting: another failure may have caused this', tone: 'diagnose', active: false }
  }
  if (!_runtime.aiEnabled && (incident.state === 'detected' || incident.state === 'triaged')) {
    return { text: 'AI diagnosis is off, so the cause is unknown', tone: 'muted', active: false }
  }
  if (incident.state === 'approved' && _runtime.mode !== 'execute') {
    return { text: 'Approved. Digdown is only suggesting fixes, so nothing changed', tone: 'muted', active: false }
  }
  if (incident.state === 'failed' && incident.failure_reason) {
    return { text: incident.failure_reason, tone: 'bad', active: false }
  }
  if (incident.state === 'diagnosis_failed') {
    const reason = incident.failure_reason
    return {
      text: reason ? `Diagnosis failed: ${reason}` : 'Diagnosis failed',
      tone: 'bad',
      active: false,
    }
  }
  if (incident.state === 'resolved') {
    // Reserve the word "Resolved" for a genuine, verified fix; an auto-clear
    // reads as a neutral stand-down. tone 'done' (not 'good') is the live-status
    // vocabulary key STATUS_COLOR / LifecycleTracker map to phosphor — 'good'
    // has no entry there and would regress to muted gray.
    if (resolutionKind(incident) !== 'verified') {
      return { text: 'Cleared without a fix', tone: 'muted', active: false }
    }
    return {
      text: incident.auto_healed ? 'Fixed automatically and confirmed healthy' : 'Fixed and confirmed healthy',
      tone: 'done',
      active: false,
    }
  }
  return (
    LIVE_STATUS[incident.state] || {
      text: stateMeta(incident.state).label,
      tone: 'muted',
      active: false,
    }
  )
}

/** Partition incidents into the three queue swimlanes. Each lane sorted by the
 *  existing bySeverityThenTime. */
export function groupByPhase(incidents) {
  const awaiting = [], working = [], done = []
  for (const i of incidents) {
    if (i.state === 'proposed' || i.state === 'diagnosis_failed' || i.state === 'diagnosed') awaiting.push(i)
    else if (isActive(i)) working.push(i)
    else done.push(i)
  }
  awaiting.sort(bySeverityThenTime)
  working.sort(bySeverityThenTime)
  done.sort(bySeverityThenTime)
  return { awaiting, working, done }
}

const TICKER_VERB = { investigating: 'Diagnosing', approved: 'Applying a fix to', executing: 'Applying a fix to', verifying: 'Checking' }

/** Compact data for the live "NOW" ticker: in-flight (spinner) work + the
 *  awaiting count. */
export function tickerSummary(incidents) {
  const { awaiting, working } = groupByPhase(incidents)
  const items = working
    .filter((i) => liveStatus(i).active)
    .map((i) => ({ target: i.target, verb: TICKER_VERB[i.state] || 'working' }))
  return { items, awaiting: awaiting.length }
}

// --- Watched containers (the fleet) ---------------------------------------
// One-shot / init jobs are MEANT to run once and exit — mirrors the backend's
// _is_oneshot rule (digdown/watcher.py) so the UI agrees with what the
// daemon flags.
const ONESHOT_NAME = /init|migrat|setup|seed|bootstrap|provision/i

export function isOneShot(s) {
  return ONESHOT_NAME.test(s.name || '') || s.restart_policy === 'on-failure'
}

/** A one-shot job that finished successfully — "done", not a problem. */
export function isCompletedJob(s) {
  return s.status === 'exited' && (s.exit_code ?? 0) === 0 && isOneShot(s)
}

// A watched container "needs attention" on the same conditions the watcher turns
// into incidents — crash / oom / unhealthy / stopped / paused / created. A
// finished one-shot job is the one calm exception (it's DONE, not down): a clean
// exit by a long-running SERVICE, by contrast, is a `stopped` incident.
export function serviceDegraded(s) {
  if (isCompletedJob(s)) return false
  return s.health === 'unhealthy' || s.oom_killed || s.status !== 'running'
}

/** Status hue: amber if it needs attention, phosphor if live, muted otherwise
 *  (a completed job reads calm). */
export function serviceColor(s) {
  if (serviceDegraded(s)) return 'var(--sev-unhealthy)'
  if (s.status === 'running') return 'var(--live)'
  return 'var(--muted-foreground)'
}

/** Compact status line, e.g. "running, healthy", "completed", "stopped",
 *  "exited, code 137". */
export function serviceStatusLine(s) {
  if (s.status === 'exited') {
    const code = s.exit_code ?? 0
    if (code !== 0) return `exited, code ${code}`
    return isOneShot(s) ? 'completed' : 'stopped'
  }
  if (s.status === 'running' && s.health && s.health !== 'none') {
    return `running, ${s.health}`
  }
  return s.status
}

// --- Execution outcome ------------------------------------------------------
// Presentation for a completed Execution. `icon` is a key the Case File maps to
// a lucide component, keeping this a pure (node --test-able) data module.
export function executionOutcome(execution) {
  switch (execution?.result) {
    case 'resolved':
      return {
        label: execution.verified ? 'Fixed and confirmed healthy' : 'Fixed',
        tone: 'var(--live)',
        icon: 'ok',
      }
    case 'rolled_back':
      return {
        label: 'The fix didn\'t work, so it was undone',
        tone: 'var(--sev-unhealthy)',
        icon: 'revert',
      }
    case 'failed':
      return { label: 'The fix failed and was undone', tone: 'var(--sev-crash)', icon: 'fail' }
    default:
      return { label: 'Applied', tone: 'var(--muted-foreground)', icon: 'ok' }
  }
}

// Format a host vital for display; null/undefined → an em dash.
export function hostVital(v, suffix = '') {
  return v == null ? 'Unknown' : `${v}${suffix}`
}

// Build an SVG polyline path from a numeric series for a sparkline. Filters
// out null/undefined; needs >=2 plottable points (else ''). y is inverted so
// higher values sit higher. max: a number (fixed scale) or 'auto' (series max).
export function sparklinePath(values, width, height, max) {
  const pts = (values || []).filter((v) => v != null && Number.isFinite(v))
  if (pts.length < 2) return ''
  const hi = max === 'auto' ? Math.max(...pts) : max
  const range = hi || 1 // baseline 0; guards divide-by-zero on an all-zero series
  const stepX = width / (pts.length - 1)
  return pts
    .map((v, i) => {
      const x = i * stepX
      const y = height - (v / range) * height
      return `${i === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`
    })
    .join(' ')
}

// --- Fleet case classification (drives the source/case filters) ---
export function dockerCase(s) {
  if (isCompletedJob(s)) return 'completed'
  if (s.status === 'running') return s.health === 'unhealthy' ? 'unhealthy' : 'running'
  if (s.status === 'restarting') return 'restarting'
  if (s.status === 'paused') return 'paused'
  if (s.status === 'created') return 'created'
  if (s.status === 'exited' || s.status === 'dead')
    return (s.exit_code ?? 0) !== 0 || s.oom_killed ? 'crashed' : 'stopped'
  return s.status || 'unknown'
}

const _HTTP_CASE = { ok: 'ok', http_error: 'error', slow_response: 'slow' }
export function httpCase(s) {
  return s?.http?.status ? _HTTP_CASE[s.http.status] ?? null : null
}

// An opened incident is held as a snapshot taken when the review was opened.
// Re-resolve it against the live incident list (kept current by the event
// stream) so an open review reflects state changes — investigating → resolved /
// closed — without a manual refresh. Falls back to the snapshot if the incident
// has aged out of the list.
export function liveIncident(selected, incidents) {
  if (!selected) return null
  return incidents.find((i) => i.id === selected.id) ?? selected
}

export const DOCKER_CASES = [
  { key: 'all', label: 'All', token: 'muted-foreground' },
  { key: 'running', label: 'Running', token: 'live' },
  { key: 'unhealthy', label: 'Unhealthy', token: 'sev-unhealthy' },
  { key: 'crashed', label: 'Crashed', token: 'sev-crash' },
  { key: 'stopped', label: 'Stopped', token: 'sev-stopped' },
  { key: 'restarting', label: 'Restarting', token: 'sev-restart' },
  { key: 'paused', label: 'Paused', token: 'sev-paused' },
  { key: 'created', label: 'Created', token: 'sev-created' },
  { key: 'completed', label: 'Completed', token: 'muted-foreground' },
]

export const HTTP_CASES = [
  { key: 'all', label: 'All', token: 'muted-foreground' },
  { key: 'ok', label: 'OK', token: 'live' },
  { key: 'error', label: 'Error', token: 'sev-crash' },
  { key: 'slow', label: 'Slow', token: 'sev-latency' },
]

// --- Postmortem: lifecycle timeline -----------------------------------------
const AUDIT_LABEL = {
  auto_approved: 'Auto heal started the fix',
  auto_heal_skipped: 'Auto heal left this for you',
  detected: 'Detected',
  diagnosed: 'Diagnosed',
  diagnosed_from_cache: 'Diagnosed (cached)',
  approved: 'Approved',
  rejected: 'Rejected',
  dismissed: 'Dismissed',
  muted: 'Muted',
  execute_failed: 'Execution failed',
  resolved: 'Resolved',
  retry_diagnosis: 'Re-queued',
}
const AUDIT_SKIP = new Set(['reasoning']) // the trace, not a lifecycle milestone
const TERMINAL_STEP = {
  resolved: 'Resolved', rolled_back: 'Rolled back', failed: 'Failed',
  closed_no_fix: 'Closed — no fix', rejected: 'Rejected', muted: 'Muted',
}
const _humanizeEvent = (ev) =>
  String(ev || '').charAt(0).toUpperCase() + String(ev || '').slice(1).replace(/_/g, ' ')

/**
 * The incident's lifecycle as a chronological list — audit events (milestones)
 * merged with record timestamps that the audit log lacks (notably "Fix proposed"
 * from plan.created_at). Pure; `now` injectable for the in-progress step + tests.
 */
export function incidentTimeline(incident, record = {}, now = Date.now()) {
  const ms = (iso) => {
    const t = new Date(iso).getTime()
    return Number.isNaN(t) ? null : t
  }
  const raw = []
  const seen = new Set()
  const add = (iso, label, actor) => {
    const t = ms(iso)
    if (t == null) return
    raw.push({ t, label, actor: actor || null })
    seen.add(label)
  }

  add(incident.created_at, 'Detected', incident.signal?.source || 'watcher')

  for (const a of record.audit || []) {
    if (AUDIT_SKIP.has(a.event) || a.event === 'detected') continue
    add(a.ts, AUDIT_LABEL[a.event] || _humanizeEvent(a.event), a.actor)
  }

  // Fill gaps the audit log doesn't carry, from the record's own timestamps.
  if (record.diagnosis?.created_at && !seen.has('Diagnosed')) add(record.diagnosis.created_at, 'Diagnosed', 'diagnostician')
  if (record.plan?.created_at && !seen.has('Fix proposed')) add(record.plan.created_at, 'Fix proposed', 'planner')
  if (record.execution?.created_at && !seen.has('Executing')) add(record.execution.created_at, 'Executing', 'executor')

  let inProgressStep = null
  if (isTerminal(incident.state)) {
    const label = TERMINAL_STEP[incident.state] || stateMeta(incident.state).label
    if (!seen.has(label)) add(incident.updated_at, label, 'digdown')
  } else {
    inProgressStep = { t: now, label: `In progress: ${liveStatus(incident).text}`, actor: null, inProgress: true }
  }

  raw.sort((a, b) => a.t - b.t)
  if (inProgressStep) raw.push(inProgressStep) // always last
  let prev = null
  const steps = raw.map((s) => {
    const sinceMs = prev == null ? null : s.t - prev
    prev = s.t
    return { key: `${s.t}-${s.label}`, label: s.label, actor: s.actor, ts: new Date(s.t).toISOString(), sinceMs, detail: null, inProgress: s.inProgress || false }
  })

  const createdT = ms(incident.created_at)
  const endedT = ms(incident.updated_at)
  const totalMs = isTerminal(incident.state) && createdT != null && endedT != null ? endedT - createdT : null
  return { steps, totalMs }
}

/** A self-contained Markdown postmortem. Pure; deterministic given its inputs. */
export function postmortemMarkdown(incident, record = {}, timeline = { steps: [], totalMs: null }, track = null) {
  const meta = signalMeta(incident.signal?.type)
  const { diagnosis, plan, execution } = record
  const term = isTerminal(incident.state)
  const L = []
  L.push(`# Postmortem — ${incident.target}`, '')
  L.push(`**${meta.label}**, ${stateMeta(incident.state).label}, detected ${absTime(incident.created_at)}`, '')
  L.push(diagnosis?.root_cause || 'Diagnosis pending.', '')

  const recurrence = track && track.occurrencesTotal > 1
    ? `${track.status}, seen ${track.occurrencesTotal}×`
    : 'first seen'
  L.push('| | |', '|---|---|')
  L.push(`| Target | ${incident.target} |`)
  L.push(`| Signal | ${incident.signal?.type ?? '—'} |`)
  L.push(`| Detected | ${absTime(incident.created_at)} |`)
  L.push(`| Ended | ${term ? absTime(incident.updated_at) : '—'} |`)
  L.push(`| Time to resolve | ${timeline.totalMs != null ? formatDuration(timeline.totalMs) : '—'} |`)
  L.push(`| Recurrence | ${recurrence} |`, '')

  L.push('## Timeline')
  for (const s of timeline.steps) {
    const since = s.sinceMs != null ? ` · +${formatDuration(s.sinceMs)}` : ''
    const who = s.actor ? ` (${s.actor})` : ''
    L.push(`- ${absTime(s.ts)} · ${s.label}${who}${since}`)
  }
  L.push('')

  if (diagnosis?.root_cause) L.push('## Root cause', diagnosis.root_cause, '')
  if (diagnosis?.evidence?.length) {
    L.push('## Evidence')
    for (const e of diagnosis.evidence) L.push(`- ${e}`)
    L.push('')
  }
  if (plan) {
    L.push('## Fix')
    if (plan.kind === 'manual') {
      L.push(plan.manual_fix || 'Manual fix recommended.', '')
    } else {
      if (plan.summary) L.push(plan.summary)
      for (const a of plan.actions || []) L.push(`- \`${a.type}\` ${a.target}`)
      L.push('')
    }
  }
  if (execution) {
    const o = executionOutcome(execution)
    L.push('## Outcome', `${o.label}${execution.detail ? ` — ${execution.detail}` : ''}`, '')
  } else if (term) {
    L.push('## Outcome', stateMeta(incident.state).label, '')
  } else {
    L.push('## Outcome', 'In progress.', '')
  }
  const critic = plan?.critic2_reason || diagnosis?.critic_reason
  if (critic) L.push("## Critic's verdict", `> ${critic}`, '')

  return `${L.join('\n').replace(/\n{3,}/g, '\n\n').trim()}\n`
}

/** What an empty list actually means. "Nothing is failing" is only true if
 *  Digdown can see containers at all. */
export function emptyMessage(status, services = []) {
  if (status && !status.docker?.ok) {
    return {
      title: 'Nothing is being watched',
      body: "Digdown can't reach Docker, so it can't tell whether anything is failing.",
    }
  }
  if (status && services.length === 0) {
    return {
      title: 'No containers found yet',
      body: 'Start a container and it will appear here. If you expected some, check which stacks you chose in Settings.',
    }
  }
  const n = services.length
  return {
    title: 'Nothing needs you',
    body: n
      ? `All ${n} container${n === 1 ? ' is' : 's are'} running without problems. If one fails, it will appear here.`
      : 'If something fails, it will appear here.',
  }
}

// ---- auto heal ----

/** What auto heal fixed recently, newest first. A heal that was undone isn't here:
 *  it shows up as an incident that needs you. */
export function recentHeals(incidents, hours = 24, now = Date.now()) {
  const cutoff = now - hours * 3600 * 1000
  return incidents
    .filter((i) => i.auto_healed && i.state === 'resolved' && new Date(i.updated_at).getTime() >= cutoff)
    .sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at))
}

/** Why auto heal handed this incident to a person, if it did. */
export function autoHealSkipReason(record) {
  return record?.audit?.find((a) => a.event === 'auto_heal_skipped')?.payload?.reason || ''
}

/** Did auto heal, rather than a person, start this fix? */
export function ranByAutoHeal(record) {
  return Boolean(record?.approvals?.some((a) => a.identity === 'auto-heal' && a.decision === 'approve'))
}

/** A new limit lives on the container, so recreating it (an update) loses it. */
export function keepFixHint(plan) {
  const raise = plan?.actions?.find((a) => a.type === 'set_resource_limit')
  if (!raise) return ''
  // Compose's `m` means 1024 x 1024. Round up so the kept limit isn't lower.
  const megabytes = Math.ceil((raise.params?.mem_limit_bytes || 0) / (1024 * 1024))
  return `This limit is lost when the container is recreated, for example by an update. To keep it, add mem_limit: ${megabytes}m to this service in your Compose file.`
}

// Stands for containers started without Compose. Matches NO_STACK in config.py.
export const NO_STACK = '(no stack)'

export function stackLabel(name) {
  return name === NO_STACK ? 'Containers not in a Compose app' : name
}

/** Stacks that auto heal can be turned on for. A stack that's switched on stays
 *  listed, so it can still be switched off. */
export function watchedStacks(services = [], enabled = []) {
  const counts = new Map(enabled.map((name) => [name, 0]))
  for (const s of services) {
    const name = s.project || NO_STACK
    counts.set(name, (counts.get(name) || 0) + 1)
  }
  return [...counts].map(([name, containers]) => ({ name, containers }))
    .sort((a, b) => a.name.localeCompare(b.name))
}

const HEAL_OUTCOMES = {
  fixed: { label: 'Fixed', tone: 'var(--success-text)' },
  undone: { label: "Undone, it didn't help", tone: 'var(--danger-text)' },
  failed: { label: "Couldn't apply it", tone: 'var(--danger-text)' },
  working: { label: 'Working on it', tone: 'var(--info-text)' },
  left_for_you: { label: 'Left for you', tone: 'var(--warning-text)' },
}

export function healOutcome(key) {
  return HEAL_OUTCOMES[key] || { label: key, tone: 'var(--muted-foreground)' }
}

/** Whether auto heal can run right now, for the bar along the bottom. */
export function autoHealRunning(status) {
  const heal = status?.auto_heal
  return status?.mode === 'execute' && Boolean(heal?.all || heal?.stacks?.length)
}
