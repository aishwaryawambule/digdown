// Demo data for working on the dashboard without a running backend.
// Enabled with ?mock=1 (or ?mock). Shapes match GET /api/incidents exactly.
// Timestamps are spread across ~24h so the telemetry chart reads as real.

const ago = (mins) => new Date(Date.now() - mins * 60_000).toISOString()

function inc(id, type, target, state, detail, mins, diagnosis = null, plan = null,
            execution = null) {
  return {
    id,
    created_at: ago(mins),
    updated_at: ago(Math.max(0, mins - 2)),
    state,
    source: 'docker',
    target,
    signal: { source: type === 'disk_full' ? 'host' : 'docker', type, target, detail },
    dedup_key: `${type}:${target}:${type}`,
    diagnosis,
    plan,
    execution,
  }
}

const step = (type, target, ok = true, rolled_back = false) => ({
  type, target, ok, detail: '', rolled_back,
})
const exec = (result, steps, detail) => ({
  result, verified: result === 'resolved', rolled_back: result === 'rolled_back',
  steps, snapshot: {}, detail,
})

const autoPlan = (summary, actions, risk, reason) => ({
  kind: 'automated', summary, actions, risk, manual_fix: '',
  critic2_supported: true, critic2_reason: reason,
})
const manualPlan = (manual_fix, reason) => ({
  kind: 'manual', summary: 'Manual fix recommended', actions: [], risk: 'low',
  manual_fix, critic2_supported: false, critic2_reason: reason,
})

const dx = (root_cause, evidence, confidence, supported, reason, rounds = 1) => ({
  summary: root_cause,
  root_cause,
  evidence,
  confidence,
  rounds,
  critic_supported: supported,
  critic_reason: reason,
})

// A recorded ReAct transcript so the demo shows the Diagnostician's steps.
const apiTrace = [
  { role: 'assistant', tool_calls: [{ function: { name: 'get_container_state', arguments: '{"target":"api-gateway"}' } }] },
  { role: 'tool', name: 'get_container_state', content: 'api-gateway: status=exited exit_code=137 oom_killed=true restart_count=3' },
  { role: 'assistant', tool_calls: [{ function: { name: 'get_recent_logs', arguments: '{"target":"api-gateway"}' } }] },
  { role: 'tool', name: 'get_recent_logs', content: '<untrusted-logs> ... allocating buffers ... Killed </untrusted-logs>' },
  { role: 'assistant', tool_calls: [{ function: { name: 'inspect_container', arguments: '{"target":"api-gateway"}' } }] },
  { role: 'tool', name: 'inspect_container', content: 'image=api-gateway:1 restart_policy=always env_names=[PORT, UPSTREAM, LOG_LEVEL] mem_limit=256m' },
  { role: 'assistant', content: '{"root_cause":"memory limit too low for steady-state load",...}' },
]

const withTrace = (incident, trace) => ({ ...incident, trace })

export const MOCK_INCIDENTS = [
  inc(16, 'memory_pressure', 'api-gateway', 'proposed',
    { mem_bytes: 244_000_000, mem_limit_bytes: 256_000_000, percent: 95 }, 7,
    dx('api-gateway is wedged at 95% of its 256 MB memory cap, climbing toward an OOM.',
       'memory 244/256 MB (95%), up from 180 MB an hour ago; no leak signature in logs.',
       0.86, true, 'Reversible, in-scope, zero-downtime — raising the cap is the right call.'),
    autoPlan('Raise api-gateway memory limit to 512 MB',
      [{ type: 'set_resource_limit', target: 'api-gateway',
         params: { mem_limit_bytes: 512_000_000 },
         rationale: 'At 95% of 256 MB — a live bump to 512 MB clears the pressure with no restart.' }],
      'medium',
      'Snapshot the old limit, raise it live, verify the pressure clears, auto-rollback if not.')),
  inc(15, 'memory_pressure', 'api-gateway', 'detected',
    { mem_bytes: 240_000_000, mem_limit_bytes: 256_000_000, percent: 94 }, 4, null),
  withTrace(inc(14, 'oom', 'api-gateway', 'investigating', { exit_code: 137 }, 3,
    dx('Container memory limit (256MB) is below steady-state usage; the kernel OOM-killed it under load.',
       ['exit_code=137 with OOMKilled=true', 'mem_limit=256m in inspect',
        'logs show "Killed" with no application error'],
       0.86, true, 'Exit 137 + OOMKilled + the low mem_limit directly support the conclusion.')),
    apiTrace),
  inc(13, 'restart_loop', 'payments-worker', 'proposed', { restart_count: 11 }, 9,
    dx('Worker crashes on boot because STRIPE_API_KEY is unset, and the restart policy retries it endlessly.',
       ['restart_count climbing (11)', 'logs: "KeyError: STRIPE_API_KEY" each boot',
        'env_names lack STRIPE_API_KEY in inspect'],
       0.79, true, 'Repeated identical boot-time KeyError plus the missing env var are conclusive.'),
    autoPlan('Set the missing STRIPE_API_KEY and recreate the worker.',
      [{ type: 'set_env_var', target: 'payments-worker',
         params: { key: 'STRIPE_API_KEY', value: '‹from secret store›' },
         rationale: 'Supplies the variable the worker reads on boot.' }],
      'high', 'In scope and reversible (the prior env file is snapshotted); addresses the cause directly.')),
  inc(12, 'unhealthy', 'postgres', 'investigating', {}, 17, null),  // still diagnosing
  inc(11, 'crash', 'image-resizer', 'detected', { exit_code: 1 }, 26, null),
  inc(10, 'disk_full', 'host', 'detected', { percent: 92.4 }, 44, null),
  inc(9, 'crash', 'api-gateway', 'detected', { exit_code: 139 }, 72, null),
  inc(8, 'unhealthy', 'redis', 'detected', {}, 96, null),
  inc(7, 'restart_loop', 'scheduler', 'resolved', { restart_count: 6 }, 188,
    dx('Scheduler wedged on a stale lock after a deploy; a restart clears it.',
       ['restart_count climbing then flat', 'logs: "could not acquire lock"'],
       0.74, true, 'Lock-contention pattern; a clean restart is the right call.'),
    autoPlan('Restart the scheduler to clear the stale lock.',
      [{ type: 'restart', target: 'scheduler', rationale: 'Releases the held lock.' }],
      'low', 'Restart is reversible (no-op rollback) and in scope.'),
    exec('resolved', [step('restart', 'scheduler')],
      'scheduler stable across 6 checks')),
  inc(6, 'crash', 'notifications', 'rolled_back', { exit_code: 2 }, 252,
    dx('Crash looked like a config drift, so a recreate was attempted.',
       ['exit_code=2 on boot', 'config mismatch suspected'],
       0.55, true, 'Plausible enough to try a reversible recreate.'),
    autoPlan('Recreate the notifications service from its current image.',
      [{ type: 'recreate', target: 'notifications', rationale: 'Reset to a clean state.' }],
      'medium', 'Recreate is reversible via the snapshotted image digest.'),
    exec('rolled_back', [step('recreate', 'notifications', true, true)],
      'verification failed: notifications still exiting (exit_code=2) — reverted to snapshot')),
  inc(5, 'oom', 'search-indexer', 'closed_no_fix', { exit_code: 137 }, 410,
    dx('Likely a memory leak during index compaction, but the evidence is circumstantial.',
       ['intermittent OOM under indexing', 'no clear leak in available logs'],
       0.41, false, 'Evidence is too thin to confirm a root cause; recommend enabling heap profiling.', 2),
    manualPlan('Raise the container mem_limit in compose (e.g. 512m) and enable heap profiling to confirm the leak before a code fix.',
      'No safe automated action addresses a suspected leak; this needs a human.')),
  inc(4, 'unhealthy', 'rabbitmq', 'resolved', {}, 540, null),
  inc(3, 'crash', 'image-resizer', 'resolved', { exit_code: 1 }, 712, null),
  inc(2, 'restart_loop', 'payments-worker', 'muted', { restart_count: 8 }, 905, null),
  inc(1, 'disk_full', 'host', 'resolved', { percent: 88.1 }, 1180, null),
]

export const MOCK_HEALTH = { status: 'ok', mode: 'observe' }

export const MOCK_STATUS = {
  demo: false, first_run: false, mode: 'execute', watching: true,
  docker: { ok: true }, ai: { enabled: true, configured: true, model: 'sample' }, auth_required: false,
}

export const MOCK_HOST = {
  disk_percent: 90.6, load1: 1.2, mem_percent: 64,
  disk_series: [83, 85, 86, 88, 89, 90, 90, 91, 90, 91],
  mem_series: [60, 62, 61, 63, 64, 63, 65, 64, 63, 64],
}

// The roster Digdown is watching — most healthy, a few degraded (matching the
// active demo incidents).
export const MOCK_SERVICES = [
  { name: 'api-gateway', service: 'api-gateway', status: 'exited', health: 'none', exit_code: 137,
    cpu_series: [22, 28, 24, 30, 26, 35, 31, 40, 38, 44],
    mem_series: [72, 75, 79, 83, 86, 88, 90, 92, 94, 95] },
  { name: 'payments-worker', service: 'payments-worker', status: 'restarting', health: 'none' },
  { name: 'postgres', service: 'postgres', status: 'running', health: 'unhealthy' },
  { name: 'redis', service: 'redis', status: 'running', health: 'healthy' },
  { name: 'web', service: 'web', status: 'running', health: 'healthy',
    cpu_series: [12, 9, 14, 11, 8, 13, 10, 12, 9, 11],
    mem_series: [40, 42, 41, 43, 40, 44, 42, 41, 43, 42] },
  { name: 'scheduler', service: 'scheduler', status: 'running', health: 'healthy' },
  { name: 'notifications', service: 'notifications', status: 'running', health: 'healthy' },
]
