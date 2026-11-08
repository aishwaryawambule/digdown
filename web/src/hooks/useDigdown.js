import { useEffect, useRef, useState, useCallback } from 'react'
import { MOCK_INCIDENTS, MOCK_HEALTH, MOCK_SERVICES, MOCK_HOST, MOCK_STATUS } from '@/lib/mock'
import { isActive, setRuntime } from '@/lib/domain'

const MOCK = (() => {
  const p = new URLSearchParams(window.location.search)
  return p.has('mock')
})()

// Connection lifecycle for the live event stream.
// 'connecting' -> 'live' (open) -> 'down' (error/closed).
export function useDigdown() {
  // In demo mode the dataset is known up front, so seed it via the initializers
  // rather than synchronously in an effect (which triggers cascading renders).
  const [incidents, setIncidents] = useState(() => (MOCK ? MOCK_INCIDENTS : []))
  const [health, setHealth] = useState(() => (MOCK ? MOCK_HEALTH : null))
  const [conn, setConn] = useState(MOCK ? 'live' : 'connecting')
  const [loading, setLoading] = useState(!MOCK)
  const [lastEventAt, setLastEventAt] = useState(null)
  const [services, setServices] = useState(() => (MOCK ? MOCK_SERVICES : []))
  const [host, setHost] = useState(() => (MOCK ? MOCK_HOST : null))
  const [mutes, setMutes] = useState([])               // active runtime mute rules
  const [excludedProjects, setExcludedProjects] = useState([])  // config exclude_projects
  const [liveSteps, setLiveSteps] = useState(() => ({}))
  const [config, setConfig] = useState(null)
  const [status, setStatus] = useState(() => (MOCK ? MOCK_STATUS : null))
  const onArriveRef = useRef(null)

  // Let the UI react to brand-new incidents (toast + flash) without coupling
  // that to render. Consumers register a callback.
  const onIncident = useCallback((fn) => {
    onArriveRef.current = fn
  }, [])

  // Merge a server-updated incident into the list (used after approve/reject).
  const patchIncident = useCallback((updated) => {
    setIncidents((prev) =>
      prev.map((i) => (i.id === updated.id ? { ...i, ...updated } : i)),
    )
  }, [])

  // Mute a target prefix (e.g. a compose project): Digdown stops raising
  // incidents for it and any open ones flip to "muted". Optimistic — the stream
  // reconciles. Returns the parsed response (or null in mock/dev).
  const muteTarget = useCallback(async (pattern, note = '') => {
    setMutes((prev) =>
      prev.some((m) => m.pattern === pattern) ? prev : [...prev, { pattern, note }],
    )
    if (MOCK) {
      setIncidents((prev) => prev.map((i) =>
        i.target?.startsWith(pattern) && isActive(i) ? { ...i, state: 'muted' } : i))
      return null
    }
    const r = await fetch('/api/mutes', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pattern, note }),
    })
    if (!r.ok) throw new Error(`mute failed (${r.status})`)
    return r.json()
  }, [])

  const patchConfig = useCallback(async (key, value) => {
    const r = await fetch('/api/config', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key, value }),
    })
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `patch failed (${r.status})`)
    const view = await r.json()
    setConfig(view)
    return view
  }, [])

  const resetConfig = useCallback(async (key) => {
    const r = await fetch(`/api/config/${key}`, { method: 'DELETE' })
    if (!r.ok) throw new Error(`reset failed (${r.status})`)
    setConfig(await r.json())
  }, [])

  const unmuteTarget = useCallback(async (pattern) => {
    setMutes((prev) => prev.filter((m) => m.pattern !== pattern))
    if (MOCK) return null
    const r = await fetch(`/api/mutes/${encodeURIComponent(pattern)}`, { method: 'DELETE' })
    if (!r.ok) throw new Error(`unmute failed (${r.status})`)
    return r.json()
  }, [])

  // Wipe the Done lane: delete every terminal/muted incident server-side.
  // Optimistic — keep only active incidents; the stream reconciles the rest.
  const clearHistory = useCallback(async () => {
    setIncidents((prev) => prev.filter(isActive))
    if (MOCK) return null
    const r = await fetch('/api/history', { method: 'DELETE' })
    if (!r.ok) throw new Error(`clear history failed (${r.status})`)
    return r.json()
  }, [])

  useEffect(() => {
    if (MOCK) return // demo data already seeded
    let cancelled = false

    fetch('/api/health')
      .then((r) => (r.ok ? r.json() : null))
      .then((h) => !cancelled && setHealth(h))
      .catch(() => {})

    // The watched-services roster: load once, then refresh periodically to
    // catch containers that recover (they leave no incident event).
    const loadServices = () =>
      fetch('/api/services')
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => {
          if (!cancelled && d?.available) {
            setServices(d.services)
            setHost(d.host || null)
          }
        })
        .catch(() => {})
    loadServices()
    const servicesTimer = setInterval(loadServices, 20_000)
    // Several updates can arrive together, so wait for them to settle.
    let servicesSoon = null
    const loadServicesSoon = () => {
      clearTimeout(servicesSoon)
      servicesSoon = setTimeout(loadServices, 500)
    }

    // What Digdown can do right now: is it really watching, is AI connected.
    const loadStatus = () =>
      fetch('/api/status')
        .then((r) => {
          // The session ended, so go back through sign-in.
          if (r.status === 401) window.location.assign('/')
          return r.ok ? r.json() : null
        })
        .then((d) => {
          if (cancelled || !d) return
          setRuntime({ aiEnabled: d.ai?.enabled, mode: d.mode })
          setStatus(d)
        })
        .catch(() => {})
    loadStatus()
    const statusTimer = setInterval(loadStatus, 15_000)

    const loadConfig = () =>
      fetch('/api/config')
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => !cancelled && d && setConfig(d))
        .catch(() => {})
    loadConfig()

    // Active mutes + config excludes — so the dashboard can show what's silenced.
    const loadMutes = () =>
      fetch('/api/mutes')
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => {
          if (cancelled || !d) return
          setMutes(Array.isArray(d.mutes) ? d.mutes : [])
          setExcludedProjects(Array.isArray(d.excluded_projects) ? d.excluded_projects : [])
        })
        .catch(() => {})
    loadMutes()

    // Full incident list — fetched on mount AND on every (re)connect of the
    // event stream. SSE has no replay, so a dropped stream (e.g. a backend
    // restart) would otherwise leave the queue showing stale state; refetching
    // on reconnect re-syncs anything missed while disconnected.
    const loadIncidents = () =>
      fetch('/api/incidents')
        .then((r) => {
          if (!r.ok) throw new Error(`incidents ${r.status}`)
          return r.json()
        })
        .then((list) => {
          if (cancelled) return
          setIncidents(Array.isArray(list) ? list : [])
          setLoading(false)
        })
        .catch(() => {
          if (cancelled) return
          setLoading(false)
        })
    loadIncidents()

    const es = new EventSource('/api/stream')
    let wasDown = false
    es.onopen = () => {
      if (cancelled) return
      setConn('live')
      if (wasDown) {        // reconnected after a drop (restart) — catch up on what we missed
        wasDown = false
        loadIncidents()
        loadMutes()
      }
    }
    es.onerror = () => {
      if (cancelled) return
      setConn('down')
      wasDown = true
    }
    es.onmessage = (e) => {
      if (cancelled) return
      setConn('live')
      setLastEventAt(Date.now())
      let msg
      try {
        msg = JSON.parse(e.data)
      } catch {
        return
      }
      if ((msg.type === 'incident_created' || msg.type === 'incident_updated') && msg.incident) {
        loadServicesSoon()
      }
      if (msg.type === 'incident_created' && msg.incident) {
        setIncidents((prev) => {
          if (prev.some((i) => i.id === msg.incident.id)) return prev
          return [msg.incident, ...prev]
        })
        onArriveRef.current?.(msg.incident)
      } else if (msg.type === 'incident_updated' && msg.incident) {
        // State moves through the lifecycle (triaged → investigating → …);
        // merge so the badge updates live without a refetch.
        setIncidents((prev) =>
          prev.map((i) => (i.id === msg.incident.id ? { ...i, ...msg.incident } : i)),
        )
        if (msg.incident.state === 'investigating') {
          setLiveSteps((prev) => ({ ...prev, [msg.incident.id]: [] }))
        }
      } else if (msg.type === 'reasoning_step' && msg.incident_id != null) {
        setLiveSteps((prev) => {
          const cur = prev[msg.incident_id] || []
          return { ...prev, [msg.incident_id]: [...cur, msg.step] }
        })
      } else if (msg.type === 'mute_added' && msg.mute) {
        setMutes((prev) =>
          prev.some((m) => m.pattern === msg.mute.pattern) ? prev : [...prev, msg.mute],
        )
      } else if (msg.type === 'mute_removed' && msg.pattern) {
        setMutes((prev) => prev.filter((m) => m.pattern !== msg.pattern))
      } else if (msg.type === 'history_cleared' && Array.isArray(msg.incident_ids)) {
        const gone = new Set(msg.incident_ids)
        setIncidents((prev) => prev.filter((i) => !gone.has(i.id)))
      } else if (msg.type === 'config_changed') {
        loadConfig() // re-pull the annotated view so source badges stay accurate
        loadStatus()
      }
    }

    return () => {
      cancelled = true
      clearInterval(servicesTimer)
      clearInterval(statusTimer)
      clearTimeout(servicesSoon)
      es.close()
    }
  }, [])

  return { incidents, health, conn, loading, lastEventAt, services, host, mutes,
           excludedProjects, onIncident, patchIncident, muteTarget, unmuteTarget,
           clearHistory, liveSteps, mock: MOCK, config, patchConfig, resetConfig, status }
}
