import { test } from 'node:test'
import assert from 'node:assert/strict'
import { posture, lifecyclePhase, liveStatus, groupByPhase, tickerSummary, resolvedWithin, isActive, canDismiss, SIGNALS, SIGNAL_CATEGORIES, actionParamsLabel, hostVital } from './domain.js'
import { isCompletedJob, serviceDegraded, serviceStatusLine, executionOutcome, dockerCase, httpCase } from './domain.js'
import { liveIncident } from './domain.js'
import { firedDetail, unitMuteState, mutePatternFor } from './domain.js'
import { sparklinePath } from './domain.js'
import { resolutionKind, stateBadge, stateMeta } from './domain.js'
import { setRuntime, emptyMessage, actionLabel } from './domain.js'

const svc = (o) => ({ name: 'web', status: 'running', health: 'none', exit_code: 0, restart_policy: '', image: '', ...o })

const inc = (state, type) => ({ state, signal: { type } })

test('posture is nominal when nothing is active', () => {
  assert.equal(posture([]), 'nominal')
  assert.equal(posture([inc('resolved', 'crash'), inc('muted', 'oom')]), 'nominal')
})

test('posture is elevated for active non-severe signals', () => {
  assert.equal(posture([inc('investigating', 'unhealthy')]), 'elevated')
  assert.equal(posture([inc('detected', 'disk_full'), inc('resolved', 'oom')]), 'elevated')
})

test('posture is critical for active oom/crash', () => {
  assert.equal(posture([inc('detected', 'crash')]), 'critical')
  assert.equal(posture([inc('investigating', 'oom')]), 'critical')
})

test('posture is critical when a fix awaits approval, regardless of signal', () => {
  assert.equal(posture([inc('proposed', 'unhealthy')]), 'critical')
})

const incS = (state, type = 'crash', extra = {}) => ({ state, signal: { type }, target: 't-' + state, created_at: '2026-06-18T00:00:00Z', ...extra })

test('lifecyclePhase maps each state to its stage index', () => {
  assert.equal(lifecyclePhase('investigating').index, 0)
  assert.equal(lifecyclePhase('proposed').index, 1)
  assert.equal(lifecyclePhase('executing').index, 2)
  assert.equal(lifecyclePhase('verifying').index, 2)
  assert.equal(lifecyclePhase('resolved').index, 3)
})

test('lifecyclePhase reports terminal outcome', () => {
  assert.equal(lifecyclePhase('resolved').outcome, 'good')
  assert.equal(lifecyclePhase('rolled_back').outcome, 'bad')
  assert.equal(lifecyclePhase('failed').outcome, 'bad')
  assert.equal(lifecyclePhase('closed_no_fix').outcome, 'neutral')
  assert.equal(lifecyclePhase('investigating').outcome, null)
})

test('liveStatus gives an honest, state-derived verb with no fabricated counts', () => {
  const v = liveStatus(incS('verifying'))
  assert.equal(v.tone, 'apply')
  assert.equal(v.active, true)
  assert.match(v.text, /checking/i)
  assert.doesNotMatch(v.text, /\d+\/\d+/) // no fabricated "3/6"
  assert.equal(liveStatus(incS('proposed')).tone, 'await')
  assert.equal(liveStatus(incS('resolved')).active, false)
})

test('firedDetail surfaces a grafana alert summary and distinct description', () => {
  const sig = { type: 'alert_firing', detail: { summary: 'Errors on shop-api', description: 'token could not be decrypted' } }
  assert.deepEqual(firedDetail(sig), {
    summary: 'Errors on shop-api',
    description: 'token could not be decrypted',
  })
})

test('firedDetail falls back to the one-line detail summary for non-alert signals', () => {
  assert.deepEqual(firedDetail({ type: 'crash', detail: { exit_code: 137 } }), { summary: 'exit 137', description: null })
})

test('firedDetail drops a description identical to the summary', () => {
  assert.deepEqual(firedDetail({ detail: { summary: 'same text', description: 'same text' } }), { summary: 'same text', description: null })
})

test('firedDetail returns null when there is nothing worth showing', () => {
  assert.equal(firedDetail({ detail: {} }), null)
  assert.equal(firedDetail(undefined), null)
})

test('unitMuteState: none when no mute matches the unit', () => {
  assert.equal(unitMuteState({ name: 'blog-web' }, []), 'none')
  assert.equal(unitMuteState({ name: 'blog-web' }, [{ pattern: 'apollo' }]), 'none')
})

test('unitMuteState: self when the unit has its own exact-name mute', () => {
  assert.equal(unitMuteState({ name: 'blog-web' }, [{ pattern: 'blog-web' }]), 'self')
})

test('unitMuteState: stack when only a broader prefix (its project) is muted', () => {
  assert.equal(unitMuteState({ name: 'blog-web' }, [{ pattern: 'blog' }]), 'stack')
})

test('unitMuteState: an exact self-mute takes precedence over a covering prefix', () => {
  assert.equal(unitMuteState({ name: 'blog-web' }, [{ pattern: 'blog' }, { pattern: 'blog-web' }]), 'self')
})

test('unitMuteState: none for a unit with no name', () => {
  assert.equal(unitMuteState({}, [{ pattern: 'blog' }]), 'none')
})

test('mutePatternFor: null when the unit is not muted', () => {
  assert.equal(mutePatternFor({ name: 'blog-web' }, []), null)
  assert.equal(mutePatternFor({ name: 'blog-web' }, [{ pattern: 'apollo' }]), null)
})

test('mutePatternFor: the exact-name pattern when individually muted', () => {
  assert.equal(mutePatternFor({ name: 'blog-web' }, [{ pattern: 'blog-web' }]), 'blog-web')
})

test('mutePatternFor: the covering stack pattern when muted via stack', () => {
  assert.equal(mutePatternFor({ name: 'blog-web' }, [{ pattern: 'blog' }]), 'blog')
})

test('mutePatternFor: prefers the exact-name mute over a covering prefix', () => {
  assert.equal(mutePatternFor({ name: 'blog-web' }, [{ pattern: 'blog' }, { pattern: 'blog-web' }]), 'blog-web')
})

test('groupByPhase partitions awaiting / working / done', () => {
  const g = groupByPhase([incS('proposed'), incS('investigating'), incS('executing'), incS('resolved'), incS('muted')])
  assert.equal(g.awaiting.length, 1)
  assert.equal(g.working.length, 2)   // investigating + executing
  assert.equal(g.done.length, 2)      // resolved + muted
})

test('tickerSummary lists in-flight work + awaiting count', () => {
  const s = tickerSummary([incS('investigating', 'crash', { target: 'api' }), incS('proposed'), incS('detected')])
  assert.equal(s.awaiting, 1)
  assert.ok(s.items.some((i) => i.target === 'api' && /diagnos/i.test(i.verb)))
  // 'detected' is not active (no spinner) → not in the live items
  assert.equal(s.items.length, 1)
})

test('resolvedWithin counts only resolved incidents inside the window', () => {
  const now = Date.parse('2026-06-18T12:00:00Z')
  const mk = (state, hoursAgo) => ({ state, updated_at: new Date(now - hoursAgo * 3_600_000).toISOString() })
  const list = [mk('resolved', 1), mk('resolved', 30), mk('investigating', 1), mk('resolved', 5)]
  assert.equal(resolvedWithin(list, 24, now), 2)
})

test('isActive filter keeps active lanes and drops the whole done lane', () => {
  const incidents = [
    incS('proposed'), incS('investigating'), incS('executing'),
    incS('resolved'), incS('rejected'), incS('rolled_back'),
    incS('failed'), incS('closed_no_fix'), incS('muted'),
  ]
  const kept = incidents.filter(isActive).map((i) => i.state)
  assert.deepEqual(kept, ['proposed', 'investigating', 'executing'])
})

test('canDismiss covers stuck / pre-decision states only', () => {
  for (const s of ['detected', 'triaged', 'investigating', 'diagnosis_failed']) {
    assert.equal(canDismiss(s), true, `${s} should be dismissible`)
  }
  for (const s of ['proposed', 'approved', 'executing', 'verifying',
                   'resolved', 'rejected', 'rolled_back', 'failed',
                   'closed_no_fix', 'muted']) {
    assert.equal(canDismiss(s), false, `${s} should NOT be dismissible`)
  }
})

test('isCompletedJob: a one-shot init that exited 0 is done', () => {
  assert.equal(isCompletedJob(svc({ name: 'shop-db-init-1', status: 'exited', exit_code: 0 })), true)
})

test('isCompletedJob: a cleanly-stopped service is NOT a completed job', () => {
  assert.equal(isCompletedJob(svc({ name: 'shop-prod', status: 'exited', exit_code: 0 })), false)
})

test('isCompletedJob: a failed one-shot (non-zero exit) is not "completed"', () => {
  assert.equal(isCompletedJob(svc({ name: 'db-init', status: 'exited', exit_code: 1 })), false)
})

test('serviceDegraded: a completed job is calm', () => {
  assert.equal(serviceDegraded(svc({ name: 'db-init', status: 'exited', exit_code: 0 })), false)
})

test('serviceDegraded: a stopped service needs attention', () => {
  assert.equal(serviceDegraded(svc({ name: 'shop-prod', status: 'exited', exit_code: 0 })), true)
})

test('serviceDegraded: running healthy is calm; paused/unhealthy need attention', () => {
  assert.equal(serviceDegraded(svc({ status: 'running', health: 'healthy' })), false)
  assert.equal(serviceDegraded(svc({ status: 'running', health: 'unhealthy' })), true)
  assert.equal(serviceDegraded(svc({ status: 'paused' })), true)
})

test('serviceStatusLine: completed vs stopped vs running', () => {
  assert.equal(serviceStatusLine(svc({ name: 'db-init', status: 'exited', exit_code: 0 })), 'completed')
  assert.equal(serviceStatusLine(svc({ name: 'shop-prod', status: 'exited', exit_code: 0 })), 'stopped')
  assert.equal(serviceStatusLine(svc({ status: 'running', health: 'healthy' })), 'running, healthy')
})

test('executionOutcome: resolved + verified', () => {
  const o = executionOutcome({ result: 'resolved', verified: true })
  assert.equal(o.label, 'Fixed and confirmed healthy')
  assert.equal(o.tone, 'var(--live)')
  assert.equal(o.icon, 'ok')
})

test('executionOutcome: resolved but not verified', () => {
  const o = executionOutcome({ result: 'resolved', verified: false })
  assert.equal(o.label, 'Fixed')
  assert.equal(o.tone, 'var(--live)')
  assert.equal(o.icon, 'ok')
})

test('executionOutcome: rolled_back', () => {
  const o = executionOutcome({ result: 'rolled_back' })
  assert.equal(o.label, "The fix didn't work, so it was undone")
  assert.equal(o.tone, 'var(--sev-unhealthy)')
  assert.equal(o.icon, 'revert')
})

test('executionOutcome: failed', () => {
  const o = executionOutcome({ result: 'failed' })
  assert.equal(o.label, 'The fix failed and was undone')
  assert.equal(o.tone, 'var(--sev-crash)')
  assert.equal(o.icon, 'fail')
})

test('executionOutcome: unknown/missing result falls back to neutral', () => {
  assert.equal(executionOutcome({ result: 'weird' }).label, 'Applied')
  assert.equal(executionOutcome({}).label, 'Applied')
  assert.equal(executionOutcome(null).label, 'Applied')
  assert.equal(executionOutcome(null).tone, 'var(--muted-foreground)')
})

test('dockerCase classifies the conditions', () => {
  assert.equal(dockerCase({ status: 'running', health: 'healthy' }), 'running')
  assert.equal(dockerCase({ status: 'running', health: 'unhealthy' }), 'unhealthy')
  assert.equal(dockerCase({ status: 'exited', exit_code: 0, name: 'web' }), 'stopped')
  assert.equal(dockerCase({ status: 'exited', exit_code: 137, name: 'web' }), 'crashed')
  assert.equal(dockerCase({ status: 'restarting' }), 'restarting')
  assert.equal(dockerCase({ status: 'paused' }), 'paused')
  assert.equal(dockerCase({ status: 'created' }), 'created')
  assert.equal(dockerCase({ status: 'exited', exit_code: 0, name: 'db-migrate' }), 'completed')
})

test('httpCase from the service http field', () => {
  assert.equal(httpCase({ http: { status: 'ok' } }), 'ok')
  assert.equal(httpCase({ http: { status: 'http_error' } }), 'error')
  assert.equal(httpCase({ http: { status: 'slow_response' } }), 'slow')
  assert.equal(httpCase({}), null)
})

// --- Mutes (runtime silence by target prefix) ---
import { matchesMute, isProjectMuted } from './domain.js'

test('matchesMute is exact or prefix', () => {
  assert.equal(matchesMute('blog', 'blog'), true)               // exact
  assert.equal(matchesMute('blog-api_app-1', 'blog'), true)  // whole stack via prefix
  assert.equal(matchesMute('grafana', 'blog'), false)
  assert.equal(matchesMute('', 'blog'), false)
})

test('isProjectMuted matches any active mute pattern', () => {
  const mutes = [{ pattern: 'blog' }]
  assert.equal(isProjectMuted('blog', mutes), true)
  assert.equal(isProjectMuted('grafana', mutes), false)
  assert.equal(isProjectMuted('blog', []), false)
})

test('alert_firing is a known signal in the taxonomy', () => {
  assert.ok(SIGNALS.alert_firing)
  assert.equal(typeof SIGNALS.alert_firing.label, 'string')
  assert.equal(SIGNALS.alert_firing.category, 'alert')
  assert.ok(SIGNAL_CATEGORIES.alert)
})

// --- Evidence value formatting (grafana alert `labels` is a nested object) ---
import { formatDetailValue } from './domain.js'

test('formatDetailValue renders an object as k=v pairs, never [object Object]', () => {
  const out = formatDetailValue('labels', { severity: 'critical', app: 'shop-api' })
  assert.doesNotMatch(out, /\[object Object\]/)
  assert.match(out, /severity=critical/)
  assert.match(out, /app=shop-api/)
})

test('formatDetailValue keeps the percent rendering', () => {
  assert.equal(formatDetailValue('percent', 91.6), '92%')
})

test('formatDetailValue collapses empty/missing values to "" so the row can be dropped', () => {
  assert.equal(formatDetailValue('value', ''), '')
  assert.equal(formatDetailValue('startsAt', null), '')
  assert.equal(formatDetailValue('startsAt', undefined), '')
  assert.equal(formatDetailValue('labels', {}), '')
})

test('formatDetailValue preserves falsy-but-real scalars and plain strings', () => {
  assert.equal(formatDetailValue('exit_code', 0), '0')
  assert.equal(formatDetailValue('fingerprint', 'demo-fingerprint-1'), 'demo-fingerprint-1')
})

test('formatDetailValue renders arrays without [object Object]', () => {
  assert.equal(formatDetailValue('tags', ['a', 'b']), 'a, b')
})

test('liveIncident re-resolves the open snapshot against the live stream', () => {
  const snapshot = { id: 7, state: 'investigating', updated_at: 'a' }
  const live = [
    { id: 3, state: 'detected' },
    { id: 7, state: 'closed_no_fix', updated_at: 'b' }, // same id, newer state
  ]
  // The open review must follow the stream, not the stale snapshot.
  assert.equal(liveIncident(snapshot, live).state, 'closed_no_fix')
  assert.equal(liveIncident(snapshot, live).updated_at, 'b')
})

test('liveIncident falls back to the snapshot when the incident left the list', () => {
  const snapshot = { id: 9, state: 'resolved' }
  assert.equal(liveIncident(snapshot, [{ id: 1 }]), snapshot)
  assert.equal(liveIncident(null, [{ id: 1 }]), null)
})

test('liveStatus distinguishes queued, diagnosing, and failed', () => {
  assert.equal(liveStatus({ state: 'detected' }).text, 'Waiting for diagnosis')
  assert.equal(liveStatus({ state: 'detected' }).active, false)
  assert.equal(liveStatus({ state: 'investigating' }).text, 'Diagnosing…')
  assert.equal(liveStatus({ state: 'investigating' }).active, true)
  const failed = liveStatus({ state: 'diagnosis_failed', failure_reason: 'Rate-limited by the model API' })
  assert.equal(failed.text, 'Diagnosis failed: Rate-limited by the model API')
  assert.equal(failed.active, false)
  assert.equal(liveStatus({ state: 'diagnosis_failed', failure_reason: '' }).text, 'Diagnosis failed')
})

test('groupByPhase routes diagnosis_failed into the awaiting lane', () => {
  const mk = (state, type) => ({ state, signal: { type }, target: 't-' + state, created_at: '2026-06-22T00:00:00Z' })
  const lanes = groupByPhase([
    mk('diagnosis_failed', 'alert_firing'),
    mk('investigating', 'crash'),
    mk('detected', 'oom'),
  ])
  assert.equal(lanes.awaiting.length, 1)
  assert.equal(lanes.awaiting[0].state, 'diagnosis_failed')
  assert.equal(lanes.working.length, 2)
})

// --- Recurrence domain core ---
import { RECURRENCE, outcomeFromState, OUTCOME_TONE, trackRecord } from './domain.js'

const T0 = Date.parse('2026-06-22T12:00:00Z')
const HOUR = 3_600_000
const DAY = 86_400_000
const ago = (ms) => new Date(T0 - ms).toISOString()
// recurrence incident factory
const ri = (target, type, state, agoMs, id = 0) =>
  ({ id, target, state, signal: { type }, created_at: ago(agoMs) })

test('outcomeFromState maps each state to a coarse outcome', () => {
  assert.equal(outcomeFromState('resolved'), 'held')
  assert.equal(outcomeFromState('rolled_back'), 'reverted')
  assert.equal(outcomeFromState('failed'), 'reverted')
  assert.equal(outcomeFromState('closed_no_fix'), 'none')
  assert.equal(outcomeFromState('rejected'), 'none')
  assert.equal(outcomeFromState('muted'), 'none')
  assert.equal(outcomeFromState('investigating'), 'open')
  assert.equal(outcomeFromState('proposed'), 'open')
  assert.ok(OUTCOME_TONE.held && OUTCOME_TONE.reverted && OUTCOME_TONE.none && OUTCOME_TONE.open)
})

test('trackRecord reports first-seen for a single occurrence', () => {
  const incs = [ri('db', 'crash', 'investigating', 0, 1)]
  const r = trackRecord(incs, 'db', { now: T0 })
  assert.equal(r.status, 'first-seen')
  assert.equal(r.occurrences24h, 1)
  assert.equal(r.occurrencesTotal, 1)
  assert.equal(r.lastSeenIso, null)
  assert.deepEqual(r.outcomeHistory, [])
  assert.equal(r.priorFixesReverted, 0)
})

test('trackRecord reports repeat at the 24h threshold', () => {
  const incs = [
    ri('db', 'crash', 'investigating', 0, 3),
    ri('db', 'crash', 'resolved', 2 * HOUR, 2),
  ]
  const r = trackRecord(incs, 'db', { now: T0 })
  assert.equal(r.status, 'repeat')
  assert.equal(r.occurrences24h, RECURRENCE.repeat24h)
})

test('trackRecord reports chronic at 3 in 24h', () => {
  const incs = [
    ri('db', 'crash', 'investigating', 0, 4),
    ri('db', 'crash', 'rolled_back', 3 * HOUR, 3),
    ri('db', 'crash', 'resolved', 6 * HOUR, 2),
  ]
  const r = trackRecord(incs, 'db', { now: T0 })
  assert.equal(r.status, 'chronic')
  assert.equal(r.occurrences24h, 3)
})

test('trackRecord reports chronic via the slow-burn 7d window even with few in 24h', () => {
  const incs = [
    ri('db', 'crash', 'investigating', 0, 5),       // today (24h count = 1)
    ri('db', 'crash', 'resolved', 2 * DAY, 4),
    ri('db', 'crash', 'resolved', 3 * DAY, 3),
    ri('db', 'crash', 'resolved', 4 * DAY, 2),
    ri('db', 'crash', 'resolved', 5 * DAY, 1),
  ]
  const r = trackRecord(incs, 'db', { now: T0 })
  assert.equal(r.occurrences24h, 1)
  assert.equal(r.occurrences7d, RECURRENCE.chronic7d)
  assert.equal(r.status, 'chronic')
})

test('trackRecord excludes occurrences outside the 24h window from the 24h count', () => {
  const incs = [
    ri('db', 'crash', 'investigating', 0, 2),
    ri('db', 'crash', 'resolved', 25 * HOUR, 1), // older than 24h
  ]
  const r = trackRecord(incs, 'db', { now: T0 })
  assert.equal(r.occurrences24h, 1)
  assert.equal(r.occurrences7d, 2)
  assert.equal(r.occurrencesTotal, 2)
})

test('trackRecord narrows counts to one signal type when given', () => {
  const incs = [
    ri('db', 'crash', 'investigating', 0, 3),
    ri('db', 'crash', 'resolved', 1 * HOUR, 2),
    ri('db', 'oom', 'resolved', 2 * HOUR, 1),
  ]
  const crashes = trackRecord(incs, 'db', { signalType: 'crash', now: T0 })
  assert.equal(crashes.occurrences24h, 2)
  const all = trackRecord(incs, 'db', { now: T0 })
  assert.equal(all.occurrences24h, 3)
})

test('trackRecord builds outcomeHistory from prior occurrences, newest first', () => {
  const incs = [
    ri('db', 'crash', 'investigating', 0, 3),        // current — excluded from history
    ri('db', 'crash', 'rolled_back', 2 * HOUR, 2),
    ri('db', 'crash', 'resolved', 5 * HOUR, 1),
  ]
  const r = trackRecord(incs, 'db', { now: T0 })
  assert.equal(r.lastSeenIso, ago(2 * HOUR))
  assert.deepEqual(r.outcomeHistory.map((o) => o.outcome), ['reverted', 'held'])
  assert.equal(r.priorFixesReverted, 1)
})

// recurrence incident factory carrying an error_signature
const riSig = (target, sig, agoMs = 0, id = 0) =>
  ({ id, target, state: 'resolved', signal: { type: 'alert_firing' },
     error_signature: sig, created_at: ago(agoMs) })

test('trackRecord groups by error_signature when a signature is given', () => {
  const incs = [riSig('shop-api', 'sig-a', 0, 1),
                riSig('shop-api', 'sig-a', HOUR, 2),
                riSig('shop-api', 'sig-b', 2 * HOUR, 3)]
  const r = trackRecord(incs, 'shop-api', { signature: 'sig-a', now: T0 })
  assert.equal(r.occurrencesTotal, 2)   // only the sig-a pair, not sig-b
})

test('trackRecord ignores other problems on the same target', () => {
  const incs = [riSig('shop-api', 'sig-a', 0, 1),
                riSig('shop-api', 'sig-b', HOUR, 2)]
  const r = trackRecord(incs, 'shop-api', { signature: 'sig-b', now: T0 })
  assert.equal(r.occurrencesTotal, 1)
})

test('trackRecord falls back to target+type for legacy incidents (no signature)', () => {
  const incs = [ri('db', 'crash', 'resolved', 0, 1),
                ri('db', 'crash', 'resolved', HOUR, 2)]
  const r = trackRecord(incs, 'db', { signalType: 'crash', now: T0 })
  assert.equal(r.occurrencesTotal, 2)
})

import { recurrenceChip } from './domain.js'

test('recurrenceChip hides for first-seen and shows count/tone otherwise', () => {
  assert.equal(recurrenceChip({ status: 'first-seen' }).show, false)
  assert.equal(recurrenceChip(null).show, false)

  const repeat = recurrenceChip({ status: 'repeat', occurrences24h: 2, occurrences7d: 2 })
  assert.equal(repeat.show, true)
  assert.match(repeat.text, /2/)
  assert.match(repeat.text, /today/)
  assert.equal(repeat.tone, 'var(--sev-unhealthy)')

  const chronic = recurrenceChip({ status: 'chronic', occurrences24h: 1, occurrences7d: 6 })
  assert.equal(chronic.show, true)
  assert.match(chronic.text, /Keeps failing/)
  assert.match(chronic.text, /6/)
  assert.equal(chronic.tone, 'var(--sev-oom)')
})

import { cameBackLine } from './domain.js'

test('cameBackLine summarises reverted prior fixes, else null', () => {
  assert.equal(cameBackLine({ priorFixesReverted: 0 }), null)
  assert.equal(cameBackLine(null), null)
  assert.match(cameBackLine({ priorFixesReverted: 1 }), /came back/i)
  assert.match(cameBackLine({ priorFixesReverted: 3 }), /3/)
})

import { repeatOffenders } from './domain.js'

test('repeatOffenders excludes first-seen, ranks chronic first, caps to limit', () => {
  const incs = [
    // chronic: 3 in 24h
    ri('db', 'crash', 'investigating', 0, 9),
    ri('db', 'crash', 'rolled_back', 2 * HOUR, 8),
    ri('db', 'crash', 'resolved', 4 * HOUR, 7),
    // repeat: 2 in 24h
    ri('api', 'unhealthy', 'investigating', 0, 6),
    ri('api', 'unhealthy', 'resolved', 3 * HOUR, 5),
    // first-seen: single
    ri('cache', 'oom', 'investigating', 0, 4),
  ]
  const offenders = repeatOffenders(incs, { now: T0 })
  assert.deepEqual(offenders.map((o) => o.target), ['db', 'api'])
  assert.equal(offenders[0].status, 'chronic')

  const capped = repeatOffenders(incs, { now: T0, limit: 1 })
  assert.equal(capped.length, 1)
  assert.equal(capped[0].target, 'db')

  assert.deepEqual(repeatOffenders([], { now: T0 }), [])
})

import { WINDOWS, windowRange, windowStats, formatDuration } from './domain.js'

const W0 = Date.parse('2026-06-22T12:00:00Z')
const H = 3_600_000
const D = 86_400_000
const wago = (ms) => new Date(W0 - ms).toISOString()
// incident with explicit created/updated
const wi = (state, type, createdAgo, updatedAgo = createdAgo, id = 0, target = 'db') =>
  ({ id, target, state, signal: { type }, created_at: wago(createdAgo), updated_at: wago(updatedAgo) })

test('windowRange computes window + previous window + bucketing', () => {
  const r = windowRange('7d', W0)
  assert.equal(r.end, W0)
  assert.equal(r.start, W0 - 7 * D)
  assert.equal(r.prevEnd, r.start)
  assert.equal(r.prevStart, W0 - 14 * D)
  assert.equal(r.bucket, 'day')
  assert.equal(r.buckets, 7)
  assert.equal(windowRange('24h', W0).bucket, 'hour')
  assert.equal(windowRange('bogus', W0).buckets, WINDOWS['7d'].buckets) // fallback
})

test('windowStats counts detected by created_at within the half-open window', () => {
  const incs = [wi('investigating', 'crash', 1 * H), wi('detected', 'oom', 25 * H)]
  const { start, end } = windowRange('24h', W0)
  const s = windowStats(incs, { start, end })
  assert.equal(s.detected, 1) // the 25h-old one is excluded
  assert.equal(s.byType.crash, 1)
})

test('windowStats computes resolved, mttr, successRate, noFix, byOutcome', () => {
  const incs = [
    wi('resolved', 'crash', 10 * H, 8 * H),     // resolved in window; mttr = 2h
    wi('resolved', 'oom', 6 * H, 5 * H),         // resolved in window; mttr = 1h
    wi('rolled_back', 'crash', 7 * H, 4 * H),    // a failed fix
    wi('closed_no_fix', 'unhealthy', 9 * H, 3 * H), // no fix
  ]
  const { start, end } = windowRange('24h', W0)
  const s = windowStats(incs, { start, end })
  assert.equal(s.resolved, 2)
  assert.equal(s.mttrMs, 1.5 * H)               // mean(2h, 1h)
  assert.equal(s.successRate, 2 / 3)            // 2 resolved / (2 + 1 rolled_back)
  assert.equal(s.noFix, 1)
  assert.deepEqual(s.byOutcome, { resolved: 2, rolled_back: 1, failed: 0, closed_no_fix: 1, rejected: 0 })
})

test('windowStats returns null mttr/successRate when nothing qualifies', () => {
  const { start, end } = windowRange('24h', W0)
  const s = windowStats([wi('investigating', 'crash', 2 * H)], { start, end })
  assert.equal(s.mttrMs, null)
  assert.equal(s.successRate, null)
})

test('formatDuration renders compact units, and words for null', () => {
  assert.equal(formatDuration(null), 'None yet')
  assert.equal(formatDuration(4 * 60_000), '4m')
  assert.equal(formatDuration(1.2 * H), '1.2h')
  assert.equal(formatDuration(2 * D), '2d')
  assert.equal(formatDuration(30_000), '<1m')
})

import { bucketSeries, topOffenders, kpiDelta } from './domain.js'

test('bucketSeries buckets by day with every signal type zero-initialised', () => {
  const incs = [wi('detected', 'crash', 1 * D, 1 * D, 1), wi('detected', 'crash', 1 * D, 1 * D, 2), wi('detected', 'oom', 3 * D, 3 * D, 3)]
  const { end, bucket, buckets } = windowRange('7d', W0)
  const rows = bucketSeries(incs, { end, bucket, buckets })
  assert.equal(rows.length, 7)
  assert.equal(typeof rows[0].crash, 'number') // zero-initialised
  const total = rows.reduce((s, r) => s + r.crash + r.oom, 0)
  assert.equal(total, 3)
})

test('bucketSeries excludes incidents older than the window', () => {
  const incs = [wi('detected', 'crash', 40 * D)]
  const { end, bucket, buckets } = windowRange('30d', W0)
  const rows = bucketSeries(incs, { end, bucket, buckets })
  assert.equal(rows.reduce((s, r) => s + r.crash, 0), 0)
})

test('topOffenders ranks targets by in-window count, capped, with outcome mix', () => {
  const incs = [
    wi('resolved', 'crash', 1 * H, 1 * H, 1, 'db'),
    wi('rolled_back', 'crash', 2 * H, 2 * H, 2, 'db'),
    wi('resolved', 'oom', 3 * H, 3 * H, 3, 'db'),
    wi('investigating', 'unhealthy', 1 * H, 1 * H, 4, 'api'),
    wi('detected', 'crash', 40 * H, 40 * H, 5, 'cache'), // outside 24h
  ]
  const { start, end } = windowRange('24h', W0)
  const top = topOffenders(incs, { start, end })
  assert.equal(top[0].target, 'db')
  assert.equal(top[0].count, 3)
  assert.equal(top[0].byOutcome.held, 2) // 2 resolved → held
  assert.ok(!top.some((o) => o.target === 'cache')) // out of window
  assert.equal(topOffenders(incs, { start, end }, 1).length, 1) // cap
})

test('kpiDelta tones by direction-of-good and guards nulls', () => {
  assert.equal(kpiDelta(null, 5, 'down').show, false)
  // fewer detected than before, lower-is-better → good (live)
  const better = kpiDelta(3, 8, 'down')
  assert.equal(better.dir, 'down')
  assert.equal(better.tone, 'var(--live)')
  // more detected → bad (unhealthy)
  assert.equal(kpiDelta(8, 3, 'down').tone, 'var(--sev-unhealthy)')
  // higher success rate, higher-is-better → good
  assert.equal(kpiDelta(0.9, 0.5, 'up').tone, 'var(--live)')
  assert.equal(kpiDelta(5, 5, 'down').dir, 'flat')
})

// --- Postmortems: incidentTimeline ---
import { incidentTimeline } from './domain.js'

const PM_NOW = Date.parse('2026-06-22T12:00:00Z')
const at = (mins) => new Date(PM_NOW - mins * 60_000).toISOString()
// incident + record fixture
const pmInc = (state, extra = {}) => ({
  id: 1, target: 'db', state, signal: { type: 'crash', source: 'docker' },
  created_at: at(60), updated_at: at(50), ...extra,
})

test('incidentTimeline starts with Detected and orders chronologically', () => {
  const { steps } = incidentTimeline(
    pmInc('resolved'),
    { audit: [{ actor: 'diagnostician', event: 'diagnosed', ts: at(55) }, { actor: 'digdown', event: 'resolved', ts: at(50) }] },
    PM_NOW,
  )
  assert.equal(steps[0].label, 'Detected')
  assert.deepEqual(steps.map((s) => s.label), ['Detected', 'Diagnosed', 'Resolved'])
  assert.equal(steps[0].sinceMs, null)
  assert.equal(steps[1].sinceMs, 5 * 60_000) // 60m→55m = 5m
})

test('incidentTimeline skips the reasoning event and humanizes unknown events', () => {
  const { steps } = incidentTimeline(
    pmInc('resolved'),
    { audit: [{ event: 'reasoning', ts: at(58) }, { event: 'triaged', ts: at(57) }, { event: 'resolved', ts: at(50) }] },
    PM_NOW,
  )
  const labels = steps.map((s) => s.label)
  assert.ok(!labels.includes('Reasoning'))
  assert.ok(labels.includes('Triaged')) // humanized from "triaged"
})

test('incidentTimeline fills the Fix proposed step from plan.created_at (no proposed audit event)', () => {
  const { steps } = incidentTimeline(
    pmInc('resolved'),
    { audit: [{ event: 'resolved', ts: at(50) }], plan: { created_at: at(54) } },
    PM_NOW,
  )
  assert.ok(steps.some((s) => s.label === 'Fix proposed'))
})

test('incidentTimeline synthesizes a terminal step from state when audit lacks it, and computes totalMs', () => {
  const { steps, totalMs } = incidentTimeline(pmInc('rolled_back'), { audit: [] }, PM_NOW)
  assert.equal(steps[steps.length - 1].label, 'Rolled back')
  assert.equal(totalMs, 10 * 60_000) // created 60m ago, updated 50m ago
})

test('incidentTimeline adds an in-progress step for a live incident; totalMs null', () => {
  const { steps, totalMs } = incidentTimeline(pmInc('investigating'), { audit: [] }, PM_NOW)
  const last = steps[steps.length - 1]
  assert.equal(last.inProgress, true)
  assert.match(last.label, /in progress/i)
  assert.equal(totalMs, null)
})

import { postmortemMarkdown, signalMeta, signalCategory } from './domain.js'

test('signalMeta knows the memory_pressure signal', () => {
  const m = signalMeta('memory_pressure')
  assert.equal(m.label, 'Memory nearly full')
  assert.equal(m.short, 'Memory high')
  assert.equal(m.token, 'sev-mempressure')
  assert.equal(signalCategory('memory_pressure'), 'container')
})

test('postmortemMarkdown renders key facts, timeline, and present sections; omits absent ones', () => {
  const inc = pmInc('resolved')
  const record = {
    diagnosis: { root_cause: 'Memory limit too low', evidence: ['exit 137', 'OOMKilled'], critic_reason: 'sound' },
    plan: { kind: 'automated', summary: 'Restart the worker', actions: [{ type: 'restart', target: 'db' }], created_at: at(54) },
    execution: { result: 'resolved', verified: true, detail: 'healthy across 6 checks' },
  }
  const timeline = incidentTimeline(inc, record, PM_NOW)
  const md = postmortemMarkdown(inc, record, timeline, { status: 'chronic', occurrencesTotal: 4 })

  assert.match(md, /^# Postmortem — db/m)
  assert.match(md, /Memory limit too low/)
  assert.match(md, /\| Time to resolve \|/)
  assert.match(md, /chronic, seen 4×/)
  assert.match(md, /## Timeline/)
  assert.match(md, /## Root cause/)
  assert.match(md, /## Evidence/)
  assert.match(md, /## Fix/)
  assert.match(md, /## Outcome/)
  assert.match(md, /## Critic's verdict/)
})

test('postmortemMarkdown shows In progress and — for a live, undiagnosed incident', () => {
  const md = postmortemMarkdown(pmInc('investigating'), {}, incidentTimeline(pmInc('investigating'), {}, PM_NOW), null)
  assert.match(md, /Diagnosis pending\./)
  assert.match(md, /\| Ended \| — \|/)
  assert.match(md, /In progress\./)
  assert.ok(!/## Evidence/.test(md)) // no evidence section when none
  assert.match(md, /first seen/) // null track
})

test('actionParamsLabel formats set_resource_limit as MB, k=v otherwise', () => {
  assert.equal(
    actionParamsLabel({ type: 'set_resource_limit', params: { mem_limit_bytes: 536_870_912 } }),
    'New limit: 512 MB',
  )
  assert.equal(
    actionParamsLabel({ type: 'set_env_var', params: { key: 'X', value: '1' } }),
    'key=X  value=1',
  )
  assert.equal(actionParamsLabel({ type: 'restart', params: {} }), '')
})

test('sparklinePath builds a path, filters nulls, needs 2+ points, no NaN', () => {
  assert.equal(sparklinePath([], 48, 14, 100), '')
  assert.equal(sparklinePath([50], 48, 14, 100), '')          // <2 points
  assert.equal(sparklinePath([null, null], 48, 14, 100), '')  // all filtered
  const d = sparklinePath([0, 50, 100], 48, 14, 100)
  assert.ok(d.startsWith('M '))
  assert.equal((d.match(/[ML]/g) || []).length, 3)            // 3 plotted points
  assert.ok(!d.includes('NaN'))
  const d2 = sparklinePath([10, null, 30], 48, 14, 'auto')    // null dropped → 2 points
  assert.equal((d2.match(/[ML]/g) || []).length, 2)
  assert.ok(!d2.includes('NaN'))
  assert.ok(!sparklinePath([5, 5, 5], 48, 14, 'auto').includes('NaN'))  // flat, no /0
})

test('hostVital formats values and says when one is unknown', () => {
  assert.equal(hostVital(90.5, '%'), '90.5%')
  assert.equal(hostVital(1.2), '1.2')
  assert.equal(hostVital(null, '%'), 'Unknown')
  assert.equal(hostVital(undefined), 'Unknown')
})

test('resolutionKind classifies how a resolved incident resolved', () => {
  assert.equal(resolutionKind({ state: 'resolved', execution: { verified: true } }), 'verified')
  assert.equal(resolutionKind({ state: 'resolved' }), 'cleared')                       // no execution (Grafana auto-clear)
  assert.equal(resolutionKind({ state: 'resolved', execution: { verified: false } }), 'cleared')
  assert.equal(resolutionKind({ state: 'investigating' }), null)                       // not resolved
  assert.equal(resolutionKind(undefined), null)
})

test('liveStatus labels a verified fix vs an auto-clear honestly', () => {
  const verified = liveStatus({ state: 'resolved', execution: { verified: true } })
  assert.equal(verified.text, 'Fixed and confirmed healthy')
  assert.equal(verified.tone, 'done')   // live-status vocabulary key STATUS_COLOR maps to phosphor; NOT 'good'
  assert.equal(verified.active, false)

  const cleared = liveStatus({ state: 'resolved' })
  assert.equal(cleared.text, 'Cleared without a fix')
  assert.equal(cleared.tone, 'muted')
  assert.equal(cleared.active, false)

  assert.equal(liveStatus({ state: 'investigating' }).text, 'Diagnosing…') // non-resolved unchanged
})

test('stateBadge keeps the short badge consistent with the honest status', () => {
  assert.deepEqual(stateBadge({ state: 'resolved', execution: { verified: true } }), stateMeta('resolved'))
  assert.deepEqual(stateBadge({ state: 'resolved' }), { label: 'Cleared', tone: 'muted' })
  assert.deepEqual(stateBadge({ state: 'resolved', execution: { verified: false } }), { label: 'Cleared', tone: 'muted' })
  assert.deepEqual(stateBadge({ state: 'investigating' }), stateMeta('investigating'))
})

// ---- status that stays honest about what Digdown can actually do ----
test('with AI off, an incident does not claim to be waiting for diagnosis', () => {
  setRuntime({ aiEnabled: false, mode: 'observe' })
  try {
    const s = liveStatus({ state: 'detected' })
    assert.match(s.text, /AI diagnosis is off/)
    assert.equal(s.active, false)
  } finally {
    setRuntime()
  }
  assert.equal(liveStatus({ state: 'detected' }).text, 'Waiting for diagnosis')
})

test('approving in suggest-only mode does not pretend a fix is being applied', () => {
  setRuntime({ aiEnabled: true, mode: 'propose' })
  try {
    const s = liveStatus({ state: 'approved' })
    assert.equal(s.active, false)
    assert.match(s.text, /nothing changed/)
    assert.equal(canDismiss('approved'), true)
  } finally {
    setRuntime()
  }
  assert.equal(liveStatus({ state: 'approved' }).active, true)
  assert.equal(canDismiss('approved'), false)
})

test('an empty list only says nothing is wrong when Digdown can tell', () => {
  assert.match(emptyMessage({ docker: { ok: false } }, []).title, /Nothing is being watched/)
  assert.match(emptyMessage({ docker: { ok: true } }, []).title, /No containers/)
  const fine = emptyMessage({ docker: { ok: true } }, [{ name: 'a' }, { name: 'b' }])
  assert.equal(fine.title, 'Nothing needs you')
  assert.match(fine.body, /All 2 containers are/)
})

test('plan actions read as words', () => {
  assert.equal(actionLabel('set_resource_limit'), 'Change memory limit')
  assert.equal(actionLabel('something_new'), 'something_new')
})

test('explain-only incidents read as finished thinking, and need a person', () => {
  const s = liveStatus({ state: 'diagnosed' })
  assert.equal(s.active, false)
  assert.match(s.text, /Cause found/)
  assert.equal(canDismiss('diagnosed'), true)
  assert.equal(isActive({ state: 'diagnosed' }), true)
  assert.equal(groupByPhase([{ state: 'diagnosed', created_at: 0 }]).awaiting.length, 1)
})

test('a fix interrupted by a restart says so', () => {
  const s = liveStatus({ state: 'failed', failure_reason: 'Digdown restarted while applying this fix' })
  assert.match(s.text, /restarted/)
  assert.equal(s.tone, 'bad')
  assert.equal(liveStatus({ state: 'failed' }).text, "The fix couldn't be applied")
})

// ---- auto heal ----
import { recentHeals, autoHealSkipReason, keepFixHint, watchedStacks, ranByAutoHeal, NO_STACK, stackLabel, healOutcome, autoHealRunning } from './domain.js'

const healed = (o = {}) => ({
  id: 1, target: 'web', state: 'resolved', auto_healed: true, heal_summary: 'Restarted web',
  execution: { verified: true }, updated_at: new Date(1000 * 3600 * 30).toISOString(), ...o,
})
const at30h = 1000 * 3600 * 30

test('recent heals lists what auto heal fixed, newest first', () => {
  const older = healed({ id: 1, updated_at: new Date(at30h - 2000).toISOString() })
  const newer = healed({ id: 2, updated_at: new Date(at30h - 1000).toISOString() })
  assert.deepEqual(recentHeals([older, newer], 24, at30h).map((i) => i.id), [2, 1])
})

test('recent heals leaves out fixes a person approved', () => {
  assert.deepEqual(recentHeals([healed({ auto_healed: undefined })], 24, at30h), [])
})

test('recent heals leaves out a heal that did not end fixed', () => {
  assert.deepEqual(recentHeals([healed({ state: 'rolled_back' })], 24, at30h), [])
})

test('recent heals leaves out anything older than the window', () => {
  const old = healed({ updated_at: new Date(at30h - 25 * 3600 * 1000).toISOString() })
  assert.deepEqual(recentHeals([old], 24, at30h), [])
})

test('an auto healed incident says so in its status and badge', () => {
  assert.equal(liveStatus(healed()).text, 'Fixed automatically and confirmed healthy')
  assert.equal(stateBadge(healed()).label, 'Auto healed')
})

test('a fix you approved keeps its normal wording', () => {
  const byHand = healed({ auto_healed: undefined })
  assert.equal(liveStatus(byHand).text, 'Fixed and confirmed healthy')
  assert.equal(stateBadge(byHand).label, 'Fixed')
})

test('a skip reason comes from the audit log', () => {
  const record = { audit: [{ event: 'proposed', payload: {} },
    { event: 'auto_heal_skipped', payload: { reason: 'It came back.' } }] }
  assert.equal(autoHealSkipReason(record), 'It came back.')
  assert.equal(autoHealSkipReason({ audit: [] }), '')
  assert.equal(autoHealSkipReason({}), '')
})

test('a raised memory limit warns that an update loses it', () => {
  const plan = { actions: [{ type: 'set_resource_limit', params: { mem_limit_bytes: 536870912 } }] }
  assert.match(keepFixHint(plan), /mem_limit: 512m/)
  assert.equal(keepFixHint({ actions: [{ type: 'restart', params: {} }] }), '')
  assert.equal(keepFixHint(null), '')
})

test('stacks come from watched containers, with plain ones grouped together', () => {
  const stacks = watchedStacks([
    { name: 'a-1', project: 'photos' }, { name: 'a-2', project: 'photos' },
    { name: 'b-1', project: 'notes' }, { name: 'c', project: '' }, { name: 'd' },
  ])
  assert.deepEqual(stacks, [
    { name: NO_STACK, containers: 2 }, { name: 'notes', containers: 1 }, { name: 'photos', containers: 2 },
  ])
})

test('plain containers get a readable name', () => {
  assert.equal(stackLabel(NO_STACK), 'Containers not in a Compose app')
  assert.equal(stackLabel('photos'), 'photos')
})

test('every auto heal outcome reads plainly', () => {
  for (const key of ['fixed', 'undone', 'failed', 'working', 'left_for_you']) {
    assert.ok(healOutcome(key).label.length > 0)
    assert.ok(healOutcome(key).tone)
  }
  assert.equal(healOutcome('left_for_you').label, 'Left for you')
})

test('a stack that is switched on stays listed while its containers are gone', () => {
  assert.deepEqual(watchedStacks([], ['photos']), [{ name: 'photos', containers: 0 }])
})

test('a linked failure says it is waiting rather than looking stuck', () => {
  setRuntime({ aiEnabled: true, mode: 'execute' })
  const waiting = { state: 'detected', parent_incident_id: 8, signal: { type: 'crash' } }
  assert.match(liveStatus(waiting).text, /another failure/)
  assert.equal(liveStatus(waiting).active, false)
})

test('it can tell when auto heal ran a fix', () => {
  assert.equal(ranByAutoHeal({ approvals: [{ identity: 'auto-heal', decision: 'approve' }] }), true)
  assert.equal(ranByAutoHeal({ approvals: [{ identity: 'digdown', decision: 'approve' }] }), false)
  assert.equal(ranByAutoHeal({}), false)
})

test('a memory limit reads the same as the plan and the Compose tip', () => {
  assert.match(actionParamsLabel({ type: 'set_resource_limit', params: { mem_limit_bytes: 67108864 } }), /64 MB/)
})

test('the bar says auto heal is on for every app or chosen ones, only when fixes are allowed', () => {
  assert.equal(autoHealRunning({ mode: 'execute', auto_heal: { stacks: [], all: true } }), true)
  assert.equal(autoHealRunning({ mode: 'execute', auto_heal: { stacks: ['shop'], all: false } }), true)
  assert.equal(autoHealRunning({ mode: 'execute', auto_heal: { stacks: [], all: false } }), false)
  assert.equal(autoHealRunning({ mode: 'propose', auto_heal: { stacks: [], all: true } }), false)
  assert.equal(autoHealRunning(null), false)
})
