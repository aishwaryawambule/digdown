import { useEffect, useState } from 'react'

// Incident states past which nothing more is written — once reached, the record
// is final and we stop polling. Mirrors the backend's TERMINAL_STATES.
const TERMINAL = new Set([
  'resolved', 'rejected', 'rolled_back', 'failed', 'closed_no_fix', 'muted',
])

// Resolve one incident's full record. Live mode fetches /api/incidents/:id when
// `active` and KEEPS POLLING while the incident is still being worked, so an open
// view fills in the diagnosis / plan / trace live instead of showing the snapshot
// from the moment it was opened; polling stops once the incident is terminal.
// Demo mode reads the fields already embedded on the incident.
export function useIncidentRecord(incident, active, mock) {
  const [fetched, setFetched] = useState(null)
  const id = incident?.id
  const state = incident?.state

  useEffect(() => {
    if (!active || !id || mock) return
    let cancelled = false
    let timer = null
    const stop = () => {
      if (timer) {
        clearInterval(timer)
        timer = null
      }
    }
    const load = () =>
      fetch(`/api/incidents/${id}`)
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => {
          if (cancelled || !d) return
          setFetched({ id, ...d })
          if (TERMINAL.has(d.state)) stop() // record is final — stop refreshing
        })
        .catch(() => {})
    load()
    timer = setInterval(load, 2500) // keep the open view live until terminal
    return () => {
      cancelled = true
      stop()
    }
    // `state` too, so the cause appears as soon as it's found, not at the next poll.
  }, [active, id, state, mock])

  const live = fetched && incident && fetched.id === incident.id ? fetched : null
  const pick = (k) => (mock ? incident?.[k] ?? null : live?.[k] ?? incident?.[k] ?? null)
  const trace = mock
    ? incident?.trace ?? null
    : live?.audit?.find((a) => a.event === 'reasoning')?.payload?.steps ?? incident?.trace ?? null

  return {
    diagnosis: pick('diagnosis'),
    plan: pick('plan'),
    execution: pick('execution'),
    audit: pick('audit'),
    approvals: pick('approvals'),
    trace,
  }
}
