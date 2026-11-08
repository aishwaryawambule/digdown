import { useEffect, useMemo, useRef, useState, useCallback } from 'react'
import { toast } from 'sonner'
import { X } from 'lucide-react'
import { useDigdown } from '@/hooks/useDigdown'
import { Sidebar } from '@/components/sidebar'
import { StatusBanners } from '@/components/status-banner'
import { Setup } from '@/components/setup'
import { LiveTicker } from '@/components/live-ticker'
import { SignalChips } from '@/components/signal-chips'
import { SituationWall } from '@/components/situation-wall'
import { Fleet } from '@/components/fleet'
import { Insights } from '@/components/insights'
import { Settings } from '@/components/settings'
import { Filters } from '@/components/filters'
import { IncidentCard } from '@/components/incident-card'
import { IncidentTable } from '@/components/incident-table'
import { IncidentReview } from '@/components/incident-review'
import { AllClear, NoMatch } from '@/components/empty-state'
import { ClearHistoryButton } from '@/components/clear-history-button'
import { ScrollToTop } from '@/components/scroll-to-top'
import { ShortcutsLegend } from '@/components/keyboard'
import { StatusBar } from '@/components/status-bar'
import { TargetHistory } from '@/components/target-history'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { signalMeta, signalCategory, SIGNAL_CATEGORIES, isActive, bySeverityThenTime, groupByPhase, lifecyclePhase, liveIncident, LIFECYCLE } from '@/lib/domain'
import { cn } from '@/lib/utils'
import { AutoHealPage } from '@/components/auto-heal'
import { parseRoute, routeHash } from '@/lib/routes'
import { usePref } from '@/lib/prefs'

const isProposed = (i) => i.state === 'proposed'

// Lifecycle swimlanes — the queue groups by phase so status is legible without
// opening anything; the action lane is always first so an approval never hides.
const LANES = [
  { key: 'awaiting', label: 'Needs you', color: 'var(--sev-unhealthy)', pulse: true },
  { key: 'working', label: 'In progress', color: 'var(--primary)', pulse: false },
  { key: 'done', label: 'Finished', color: 'var(--live)', pulse: false },
]

// Resolved/closed incidents pile up forever — cap the Done lane so the feed
// never grows into an endless scroll. The action lanes (Awaiting / Working)
// always show in full; the rest expands on demand.
const DONE_CAP = 6

export default function App() {
  const { incidents, conn, loading, services, host, mutes, excludedProjects,
    onIncident, patchIncident, muteTarget, unmuteTarget, clearHistory, mock, liveSteps,
    config, patchConfig, resetConfig, status } = useDigdown()
  const [changingSetup, setChangingSetup] = useState(false)

  const [scope, setScope] = useState('all') // start on All so counts/feed show real totals
  const [sourceFilter, setSourceFilter] = useState('all') // detecting source: all | container | http | …
  const [typeFilter, setTypeFilter] = useState(() => new Set())
  const [query, setQuery] = useState('')
  const [view, setView] = usePref('incident-view', ['feed', 'table'], 'table')
  const [section, setSection] = useState(() => parseRoute(window.location.hash).section)
  const [settingsTab, setSettingsTab] = useState(() => parseRoute(window.location.hash).tab)
  // An incident in the address when the page opened, waiting for the list to load.
  const pendingIncident = useRef(parseRoute(window.location.hash).incidentId)
  const writtenHash = useRef(window.location.hash)
  const [stageFilter, setStageFilter] = useState(null) // pipeline lens: 0..3 → LIFECYCLE, or null
  const [fleetFocus, setFleetFocus] = useState('all') // Fleet's status lens when entered from a tile
  const [fleetUnit, setFleetUnit] = useState(null) // unit open in the Fleet detail
  const incidentsRef = useRef(null) // scroll anchor for tile/stage navigation
  const chromeRef = useRef(null)    // sticky band above the page, measured into --chrome-h
  const [selected, setSelected] = useState(null)
  const [sheetOpen, setSheetOpen] = useState(false)
  const incidentsLatest = useRef(incidents)
  useEffect(() => { incidentsLatest.current = incidents }, [incidents])
  const [historyTarget, setHistoryTarget] = useState(null)
  const [fresh, setFresh] = useState(() => new Set())
  const [focusIdx, setFocusIdx] = useState(-1)
  // Mirror the cursor in a ref so rapid j/k presses read the latest value
  // synchronously instead of a stale closure.
  const focusRef = useRef(-1)
  const setFocus = useCallback((idx) => {
    focusRef.current = idx
    setFocusIdx(idx)
  }, [])
  const [showHelp, setShowHelp] = useState(false)
  const [showAllDone, setShowAllDone] = useState(false) // expand the capped Done lane
  const [deciding, setDeciding] = useState(false)
  const [, setTick] = useState(0) // refreshes relative timestamps

  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 30_000)
    return () => clearInterval(id)
  }, [])

  // Measure the sticky top chrome so sub-headers (e.g. the review's "Back to
  // queue" bar) can stick flush beneath it — the LiveTicker band changes height,
  // so a fixed offset would otherwise leave a gap.
  useEffect(() => {
    const el = chromeRef.current
    if (!el) return
    const apply = () =>
      document.documentElement.style.setProperty('--chrome-h', `${el.offsetHeight}px`)
    apply()
    const ro = new ResizeObserver(apply)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // Announce new incidents as they stream in: a toast + a brief card flash.
  useEffect(() => {
    onIncident((incident) => {
      const meta = signalMeta(incident.signal?.type)
      toast(`${incident.target}: ${meta.short}`, { description: meta.blurb })
      setFresh((prev) => new Set(prev).add(incident.id))
      setTimeout(() => {
        setFresh((prev) => {
          const next = new Set(prev)
          next.delete(incident.id)
          return next
        })
      }, 1800)
    })
  }, [onIncident])

  const toggleType = useCallback((t) => {
    setTypeFilter((prev) => {
      const next = new Set(prev)
      next.has(t) ? next.delete(t) : next.add(t)
      return next
    })
  }, [])

  const clearFilters = useCallback(() => {
    setScope('all')
    setSourceFilter('all')
    setTypeFilter(new Set())
    setQuery('')
    setStageFilter(null)
  }, [])

  // Progressive cascade: picking a specific source reveals the scope tabs and
  // its type chips. Switching away drops type toggles that don't belong to the
  // new source. Returning to "All sources" collapses the cascade — the scope
  // tabs and chips hide there, so scope resets to active and the type filter
  // clears, rather than leaving stale-but-invisible filters applied.
  const changeSource = useCallback((src) => {
    setSourceFilter(src)
    setStageFilter(null) // picking a source supersedes a pipeline-stage lens
    if (src === 'all') {
      setScope('all')
      setTypeFilter(new Set())
    } else {
      setTypeFilter((prev) => {
        const next = new Set([...prev].filter((t) => signalCategory(t) === src))
        return next.size === prev.size ? prev : next
      })
    }
  }, [])

  // Switching the top section by hand drops any tile-driven lens, so a stale
  // pipeline/fleet filter never lingers under a plain tab click.
  const onSection = useCallback((s) => {
    setSection(s)
    setSheetOpen(false) // clicking Incidents from inside one goes back to the list
    setStageFilter(null)
    setFleetFocus('all')
  }, [])

  // The Situation tiles and Pipeline stages are shortcuts into the feed: each
  // jumps to its "respective" view — Fleet for watched/degraded units, the
  // incident list (scoped, or narrowed to one pipeline stage) for the rest —
  // and scrolls it into sight. A source/type lens would fight the slice, so we
  // clear the cascade first.
  const navigateTo = useCallback((intent) => {
    if (intent.to === 'fleet') {
      setFleetUnit(null)
      setFleetFocus(intent.degraded ? 'degraded' : 'all')
      setSection('fleet')
      return
    }
    if (intent.to === 'insights') {
      setSection('insights')
      return
    }
    setSection('incidents')
    setSheetOpen(false)
    setQuery('')
    if (intent.type != null) {
      // A detection-breakdown row → that one signal type, across every state.
      // Selecting its source reveals the type chip, so the slice stays
      // controllable (and clearable) through the normal Filters cascade.
      setStageFilter(null)
      setScope('all')
      setSourceFilter(signalCategory(intent.type))
      setTypeFilter(new Set([intent.type]))
    } else if (intent.stage != null) {
      setSourceFilter('all')
      setTypeFilter(new Set())
      setStageFilter(intent.stage)
      setScope('active') // stages 0–2 are all live incidents
    } else {
      setSourceFilter('all')
      setTypeFilter(new Set())
      setStageFilter(null)
      setScope(intent.scope || 'all')
    }
    requestAnimationFrame(() =>
      incidentsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }),
    )
  }, [])

  // Scope + search, but NOT the type filter. This is the set the signal chips
  // count against, so each chip shows how many incidents the current scope
  // holds of that type — not a scope-blind "active" tally.
  const scoped = useMemo(() => {
    const q = query.trim().toLowerCase()
    return incidents.filter((i) => {
      if (scope === 'active' && !isActive(i)) return false
      if (scope === 'resolved' && i.state !== 'resolved') return false
      if (scope === 'muted' && i.state !== 'muted') return false
      if (q) {
        const hay = `${i.target} ${i.signal?.type}`.toLowerCase()
        if (!hay.includes(q)) return false
      }
      return true
    })
  }, [incidents, scope, query])

  // Drives the source dropdown. The LIST is every source that has any incident
  // at all (so the dropdown is always available to pick from — an empty current
  // scope can't make it vanish and trap you), while the COUNTS reflect the
  // current scope so they match the feed in the common case. Leads with the
  // synthetic "All sources". Empty only when there are no incidents whatsoever.
  const sources = useMemo(() => {
    if (incidents.length === 0) return []
    const scopeCounts = {}
    for (const i of scoped) {
      const cat = signalCategory(i.signal?.type)
      scopeCounts[cat] = (scopeCounts[cat] || 0) + 1
    }
    const everPresent = new Set(incidents.map((i) => signalCategory(i.signal?.type)))
    const present = Object.values(SIGNAL_CATEGORIES)
      .sort((a, b) => a.order - b.order)
      .filter((c) => everPresent.has(c.key))
      .map((c) => ({ key: c.key, label: c.label, count: scopeCounts[c.key] || 0 }))
    return [{ key: 'all', label: 'All sources', count: scoped.length }, ...present]
  }, [incidents, scoped])

  // If the chosen source has nothing in the current scope, fall back to "all"
  // rather than showing an empty feed under a phantom selection.
  const effectiveSource = sources.some((s) => s.key === sourceFilter) ? sourceFilter : 'all'

  const sourceScoped = useMemo(() => {
    if (effectiveSource === 'all') return scoped
    return scoped.filter((i) => signalCategory(i.signal?.type) === effectiveSource)
  }, [scoped, effectiveSource])

  const visible = useMemo(() => {
    return sourceScoped
      .filter((i) => !typeFilter.size || typeFilter.has(i.signal?.type))
      // A pipeline-stage lens (set by clicking the funnel) narrows to incidents
      // sitting in that one stage.
      .filter((i) => stageFilter == null || lifecyclePhase(i.state).index === stageFilter)
      // Anything awaiting your approval floats to the top — it's your action
      // item — then hardest-down and newest.
      .sort((a, b) => {
        if (isProposed(a) !== isProposed(b)) return isProposed(a) ? -1 : 1
        return bySeverityThenTime(a, b)
      })
  }, [sourceScoped, typeFilter, stageFilter])

  // Partition into lifecycle lanes; the flattened lane order is the canonical
  // order for keyboard nav + focus, so j/k matches the top-to-bottom layout.
  const lanes = useMemo(() => groupByPhase(visible), [visible])
  // Use the UNFILTERED terminal count so the "Clear N?" label matches what
  // clearHistory() will actually delete, even when a source/type/scope filter
  // is active and the Done lane shows a smaller subset.
  const doneTotal = useMemo(() => incidents.filter((i) => !isActive(i)).length, [incidents])
  const ordered = useMemo(
    () => [...lanes.awaiting, ...lanes.working, ...lanes.done],
    [lanes],
  )

  // Clamp at read time so a shrinking list never points the cursor out of range.
  const focusedId =
    focusIdx >= 0 && focusIdx < ordered.length ? ordered[focusIdx]?.id : null

  // `selected` is a snapshot from when the incident was opened; re-resolve it
  // against the live list so the open review tracks lifecycle changes
  // (investigating → resolved / closed) from the stream without a refresh.
  const selectedLive = useMemo(() => liveIncident(selected, incidents), [selected, incidents])

  // Open the incident named in the address once the list has loaded. One that no
  // longer exists just leaves you on the list.
  useEffect(() => {
    if (pendingIncident.current == null || loading) return
    const found = incidents.find((i) => i.id === pendingIncident.current)
    pendingIncident.current = null
    if (found) {
      setSelected(found)
      setSheetOpen(true)
    }
  }, [incidents, loading])

  // Keep the address in step with what's on screen. Stepping between incidents
  // replaces the entry, so Back returns to the list rather than through each one.
  useEffect(() => {
    if (pendingIncident.current != null) return
    const incidentId = section === 'incidents' && sheetOpen ? selected?.id ?? null : null
    const next = routeHash({ section, incidentId, tab: settingsTab })
    const current = window.location.hash
    if (current === next) return
    writtenHash.current = next
    if (!current || (incidentId != null && parseRoute(current).incidentId != null)) {
      window.history.replaceState(null, '', next)
    } else {
      window.location.hash = next
    }
  }, [section, sheetOpen, selected, settingsTab])

  // Back, Forward, or an address typed in by hand.
  useEffect(() => {
    const onHashChange = () => {
      if (window.location.hash === writtenHash.current) return
      writtenHash.current = window.location.hash
      const route = parseRoute(window.location.hash)
      setSection(route.section)
      setSettingsTab(route.tab)
      setStageFilter(null)
      setFleetFocus('all')
      if (route.incidentId == null) {
        setSheetOpen(false)
        return
      }
      const found = incidentsLatest.current.find((i) => i.id === route.incidentId)
      setSelected(found || null)
      setSheetOpen(Boolean(found))
    }
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  // The browser tab says where you are and whether anything is open.
  useEffect(() => {
    const titles = { incidents: 'Incidents', heal: 'Auto heal', fleet: 'Containers', insights: 'Trends', settings: 'Settings' }
    const open = incidents.filter(isActive).length
    document.title = `${open ? `(${open}) ` : ''}${titles[section]} - digdown`
  }, [section, incidents])

  // Scroll the focused row into view as you move.
  useEffect(() => {
    if (focusIdx < 0 || !ordered[focusIdx]) return
    document
      .getElementById(`inc-${ordered[focusIdx].id}`)
      ?.scrollIntoView({ block: 'nearest' })
  }, [focusIdx, ordered, view])


  const openIncident = useCallback(
    (inc) => {
      setFocus(ordered.findIndex((x) => x.id === inc.id))
      setSelected(inc)
      setSheetOpen(true)
    },
    [ordered, setFocus],
  )

  // Open a target's full history panel; opening one of its incidents from there
  // closes the panel and dives into that case file.
  const openHistory = useCallback((target) => setHistoryTarget(target), [])
  const openIncidentFromHistory = useCallback(
    (inc) => {
      setHistoryTarget(null)
      openIncident(inc)
    },
    [openIncident],
  )

  // Opening an incident from the Fleet detail jumps back to the queue and
  // surfaces its case file, so a container's failures are one click away.
  const openFromFleet = useCallback(
    (inc) => {
      setSection('incidents')
      setSelected(inc)
      setSheetOpen(true)
      setFocus(ordered.findIndex((x) => x.id === inc.id))
    },
    [ordered, setFocus],
  )

  const navSheet = useCallback(
    (delta) => {
      const start = focusRef.current < 0 ? 0 : focusRef.current
      const next = Math.min(ordered.length - 1, Math.max(0, start + delta))
      setFocus(next)
      setSelected(ordered[next])
    },
    [ordered, setFocus],
  )

  const decide = useCallback(
    async (incident, decision) => {
      const nextState = decision === 'approve' ? 'approved' : 'rejected'
      const meta = signalMeta(incident.signal?.type)
      setDeciding(true)
      try {
        if (!mock) {
          const r = await fetch(`/api/incidents/${incident.id}/${decision}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: '{}',
          })
          if (!r.ok) throw new Error(`${decision} failed (${r.status})`)
          patchIncident(await r.json())
        } else {
          patchIncident({ id: incident.id, state: nextState })
        }
        toast(`${decision === 'approve' ? 'Approved' : 'Rejected'}: ${meta.short}, ${incident.target}`)
        setSheetOpen(false)
      } catch (e) {
        toast.error(String(e.message || e))
      } finally {
        setDeciding(false)
      }
    },
    [mock, patchIncident],
  )

  const retryDiagnosis = useCallback(
    async (incident) => {
      setDeciding(true)
      try {
        if (!mock) {
          const r = await fetch(`/api/incidents/${incident.id}/retry`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: '{}',
          })
          if (!r.ok) throw new Error(`retry failed (${r.status})`)
          patchIncident(await r.json())
        } else {
          patchIncident({ id: incident.id, state: 'detected', failure_reason: '' })
        }
        toast(`Retrying diagnosis: ${incident.target}`)
        setSheetOpen(false)
      } catch (e) {
        toast.error(String(e.message || e))
      } finally {
        setDeciding(false)
      }
    },
    [mock, patchIncident],
  )

  const dismissIncident = useCallback(
    async (incident) => {
      setDeciding(true)
      try {
        if (!mock) {
          const r = await fetch(`/api/incidents/${incident.id}/dismiss`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: '{}',
          })
          if (!r.ok) throw new Error(`dismiss failed (${r.status})`)
          patchIncident(await r.json())
        } else {
          patchIncident({ id: incident.id, state: 'closed_no_fix' })
        }
        toast(`Dismissed: ${incident.target}`)
        setSheetOpen(false)
      } catch (e) {
        toast.error(String(e.message || e))
      } finally {
        setDeciding(false)
      }
    },
    [mock, patchIncident],
  )

  // Keyboard-first triage. Ignores typing in inputs (except Esc to blur).
  useEffect(() => {
    function onKey(e) {
      const el = e.target
      const typing =
        el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable)
      if (e.key === 'Escape') {
        if (showHelp) setShowHelp(false)
        else if (sheetOpen) setSheetOpen(false)
        else if (typing) el.blur()
        return
      }
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return
      if (e.key === '?') return setShowHelp((h) => !h)
      const page = { 1: 'incidents', 2: 'heal', 3: 'fleet', 4: 'insights', 5: 'settings' }[e.key]
      if (page) return onSection(page)
      if (e.key === '/') {
        const box = document.getElementById(section === 'fleet' ? 'fleet-search' : 'incident-search')
        if (box) {
          e.preventDefault()
          box.focus()
        }
        return
      }
      // Incident triage keys (j/k, open, approve…) only apply in the Incidents view.
      if (section !== 'incidents') return
      if (!ordered.length) return
      const move = (delta) => {
        e.preventDefault()
        const cur = focusRef.current
        const start = cur < 0 ? (delta > 0 ? -1 : 0) : cur
        const next = Math.min(ordered.length - 1, Math.max(0, start + delta))
        setFocus(next)
        if (sheetOpen) setSelected(ordered[next])
      }
      if (e.key === 'j' || e.key === 'ArrowDown') return move(1)
      if (e.key === 'k' || e.key === 'ArrowUp') return move(-1)
      if (sheetOpen && (e.key === 'ArrowRight' || e.key === 'l')) return move(1)
      if (sheetOpen && (e.key === 'ArrowLeft' || e.key === 'h')) return move(-1)
      if (e.key === 'Enter' || e.key === 'o') {
        const i = focusRef.current
        if (i >= 0 && i < ordered.length) {
          setFocus(i)
          setSelected(ordered[i])
          setSheetOpen(true)
        }
        return
      }
      // a/r act only on a proposed fix you're actually viewing. Read the live
      // record so a fix that just resolved can't be approved from a stale view.
      if ((e.key === 'a' || e.key === 'r') && sheetOpen &&
          selectedLive?.state === 'proposed' && !deciding) {
        decide(selectedLive, e.key === 'a' ? 'approve' : 'reject')
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [ordered, sheetOpen, selectedLive, showHelp, deciding, decide, setFocus, section, onSection])

  const filtersDirty =
    scope !== 'all' || effectiveSource !== 'all' || typeFilter.size > 0 || query.trim() !== '' || stageFilter != null
  const hasAnyActive = incidents.some(isActive)

  // A "lens" is a tile/stage navigation that the Filters row can't surface a
  // control for (scope tabs hide while the source is "All"). Show a dismissible
  // chip by the heading so the slice is always nameable — and escapable.
  const lensLabel =
    stageFilter != null
      ? LIFECYCLE[stageFilter]
      : scope === 'resolved'
        ? 'Fixed'
        : scope === 'muted'
          ? 'Muted'
          : null
  const showLens = lensLabel != null && effectiveSource === 'all'
  const clearLens = useCallback(() => {
    setScope('all')
    setStageFilter(null)
  }, [])

  let placeholder = null
  if (loading) placeholder = <FeedSkeleton />
  else if (ordered.length === 0)
    placeholder =
      filtersDirty || hasAnyActive ? (
        <NoMatch onClear={clearFilters} />
      ) : (
        <AllClear status={status} services={services} />
      )

  const hintItems = [
    { keys: ['j', 'k'], label: 'move' },
    { keys: ['↵'], label: 'open' },
    ...(sheetOpen
      ? [{ keys: ['←', '→'], label: 'prev / next' }, { keys: ['esc'], label: 'back' }]
      : [{ keys: ['/'], label: 'search' }]),
    ...(sheetOpen && selectedLive?.state === 'proposed'
      ? [{ keys: ['a'], label: 'approve' }, { keys: ['r'], label: 'reject' }]
      : []),
  ]

  const reviewing = section === 'incidents' && sheetOpen && selectedLive

  if (changingSetup) {
    return <Setup onCancel={() => setChangingSetup(false)} />
  }

  return (
    <div className="flex min-h-svh flex-col">
    <div className="flex flex-1 flex-col lg:flex-row">
      <Sidebar
        section={section}
        onSection={onSection}
        incidents={incidents}
        services={services}
        status={status}
        conn={conn}
        mock={mock}
      />


      <main className="flex min-w-0 flex-1 flex-col">
        <StatusBanners status={status} conn={conn} mock={mock} onOpenSetup={() => setChangingSetup(true)} />
        <div ref={chromeRef} className="sticky top-[var(--topbar)] z-20">
          <LiveTicker incidents={incidents} />
        </div>

        <div className="page-width flex flex-1 flex-col px-4 py-5 lg:px-6">
          {!reviewing && (
            <header className="mb-3 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
                <h1 className="text-lg leading-tight text-foreground">
                  {section === 'heal' ? 'Auto heal' : section === 'fleet' ? 'Containers' : section === 'insights' ? 'Trends' : section === 'settings' ? 'Settings' : 'Incidents'}
                </h1>
                <p className="text-sm text-muted-foreground">
                  {section === 'settings' && 'Most changes apply straight away. A few restart Digdown, and your containers aren\'t touched.'}
                  {mock && (
                    <span className="ml-2 rounded border border-dashed px-1.5 py-0.5 text-xs text-muted-foreground">
                      Sample data
                    </span>
                  )}
                </p>
              </div>
            </header>
          )}

          {section === 'heal' ? (
            <AutoHealPage
              config={config}
              mock={mock}
              services={services}
              incidents={incidents}
              onPatch={patchConfig}
              onReset={resetConfig}
              onOpenIncident={openFromFleet}
            />
          ) : section === 'fleet' ? (
            <Fleet
              services={services}
              host={host}
              incidents={incidents}
              mutes={mutes}
              excludedProjects={excludedProjects}
              onMute={muteTarget}
              onUnmute={unmuteTarget}
              onOpenIncident={openFromFleet}
              selected={fleetUnit}
              onSelect={setFleetUnit}
              initialStatus={fleetFocus}
              autoHealStacks={status?.mode === 'execute' ? status?.auto_heal?.stacks : []}
            />
          ) : section === 'insights' ? (
            <Insights incidents={incidents} onOpenHistory={openHistory} />
          ) : section === 'settings' ? (
            <Settings config={config} onPatch={patchConfig} onReset={resetConfig} mock={mock} status={status} services={services} tab={settingsTab} onTab={setSettingsTab} onChangeSetup={() => setChangingSetup(true)} />
          ) : reviewing ? (
            <IncidentReview
              incident={selectedLive}
              incidents={incidents}
              mock={mock}
              liveSteps={liveSteps}
              onDecision={decide}
              onRetry={retryDiagnosis}
              onDismiss={dismissIncident}
              onOpenHistory={openHistory}
              busy={deciding}
              position={focusIdx + 1}
              total={ordered.length}
              onPrev={() => navSheet(-1)}
              onNext={() => navSheet(1)}
              onClose={() => setSheetOpen(false)}
            />
          ) : (
            <>
              <SituationWall
                incidents={incidents}
                services={services}
                status={status}
                mock={mock}
                onOpenIncident={openIncident}
                onDecision={decide}
                onRetry={retryDiagnosis}
                onDismiss={dismissIncident}
                onNavigate={navigateTo}
                onOpenHistory={openHistory}
                busy={deciding}
              />

              <div ref={incidentsRef} className="scroll-mt-24" aria-hidden />
              <Tabs value={view} onValueChange={setView} className="mt-4 gap-3">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <h2 className="panel-title flex items-center gap-2">
                    Incidents
                    <span className="font-data text-sm font-normal text-muted-foreground">
                      {ordered.length}
                    </span>
                    {showLens && (
                      <button
                        type="button"
                        onClick={clearLens}
                        aria-label={`Clear ${lensLabel} filter`}
                        className="label-eyebrow inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-normal text-muted-foreground transition-colors hover:text-foreground"
                      >
                        {lensLabel}
                        <X className="size-3" />
                      </button>
                    )}
                  </h2>
                  <div className="flex flex-wrap items-center gap-2 sm:ml-auto">
                <Filters
                  query={query}
                  onQuery={setQuery}
                  scope={scope}
                  onScope={setScope}
                  sources={sources}
                  source={effectiveSource}
                  onSource={changeSource}
                />
                  <TabsList>
                    <TabsTrigger value="table" className="label-eyebrow">
                      Table
                    </TabsTrigger>
                    <TabsTrigger value="feed" className="label-eyebrow">
                      Cards
                    </TabsTrigger>
                  </TabsList>
                  </div>
                </div>
                {/* Progressive third level: type chips appear only once a
                    specific source is chosen. */}
                {effectiveSource !== 'all' && (
                  <SignalChips
                    incidents={sourceScoped}
                    typeFilter={typeFilter}
                    onToggleType={toggleType}
                  />
                )}

                <TabsContent value="feed">
                  {placeholder ??
                    (() => {
                      // Lane headers only earn their place when work spans more
                      // than one phase; a single group just shows its rows.
                      const populated = LANES.filter((l) => lanes[l.key].length > 0)
                      const showHeaders = populated.length > 1
                      // Expand the Done cap on demand — or automatically when
                      // keyboard focus reaches a row hidden beneath it, so j/k
                      // never lands on something off-screen. Derived (not state)
                      // to keep the effect-free.
                      const doneExpanded =
                        showAllDone ||
                        (focusedId != null &&
                          lanes.done.slice(DONE_CAP).some((i) => i.id === focusedId))
                      return (
                        <div className="flex flex-col gap-4">
                          {populated.map((lane) => {
                            // The Done lane is capped so resolved history can't
                            // flood the feed; everything else renders in full.
                            const all = lanes[lane.key]
                            const capped =
                              lane.key === 'done' && !doneExpanded && all.length > DONE_CAP
                            const rows = capped ? all.slice(0, DONE_CAP) : all
                            const hidden = all.length - rows.length
                            return (
                              <div key={lane.key} className="flex flex-col gap-2.5">
                                {showHeaders && (
                                  <div className="flex items-center gap-2.5">
                                    <span
                                      className={cn('size-2 rounded-full', lane.pulse && '')}
                                      style={{ backgroundColor: lane.color }}
                                    />
                                    <span className="label-eyebrow" style={{ color: lane.color }}>
                                      {lane.label}
                                    </span>
                                    <span className="font-data text-xs tabular-nums text-muted-foreground">
                                      {all.length}
                                    </span>
                                    <span className="h-px flex-1 bg-border" />
                                    {lane.key === 'done' && all.length > 0 && (
                                      <ClearHistoryButton count={doneTotal} onClear={clearHistory} />
                                    )}
                                  </div>
                                )}
                                {!showHeaders && lane.key === 'done' && all.length > 0 && (
                                  <div className="flex justify-end">
                                    <ClearHistoryButton count={doneTotal} onClear={clearHistory} />
                                  </div>
                                )}
                                {rows.map((i) => (
                                  <IncidentCard
                                    key={i.id}
                                    incident={i}
                                    incidents={incidents}
                                    onOpen={openIncident}
                                    fresh={fresh.has(i.id)}
                                    focused={i.id === focusedId}
                                  />
                                ))}
                                {lane.key === 'done' && all.length > DONE_CAP && (
                                  <button
                                    type="button"
                                    onClick={() => setShowAllDone((s) => !s)}
                                    className="label-eyebrow mt-0.5 flex items-center justify-center gap-1.5 rounded-lg border border-dashed py-2 text-muted-foreground/70 transition-colors hover:border-border hover:text-foreground"
                                  >
                                    {doneExpanded ? 'Show fewer' : `Show ${hidden} more`}
                                  </button>
                                )}
                              </div>
                            )
                          })}
                        </div>
                      )
                    })()}
                </TabsContent>

                <TabsContent value="table">
                  {placeholder ?? (
                    <IncidentTable
                      incidents={ordered}
                      onOpen={openIncident}
                      focusedId={focusedId}
                    />
                  )}
                </TabsContent>
              </Tabs>
            </>
          )}

        </div>


      </main>

      <ShortcutsLegend open={showHelp} onClose={() => setShowHelp(false)} />
      <TargetHistory
        target={historyTarget}
        incidents={incidents}
        onOpenIncident={openIncidentFromHistory}
        onOpenChange={(v) => !v && setHistoryTarget(null)}
      />
      <ScrollToTop />
    </div>
    <StatusBar
      status={status}
      conn={conn}
      mock={mock}
      hints={section === 'incidents' ? hintItems : null}
      onShowHelp={() => setShowHelp(true)}
    />
    </div>
  )
}

function FeedSkeleton() {
  return (
    <div className="flex flex-col gap-2.5">
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          className="flex items-center gap-4 rounded-lg border bg-card py-3.5 pr-3 pl-5"
        >
          <Skeleton className="size-10 rounded-md" />
          <div className="flex-1 space-y-2">
            <Skeleton className="h-4 w-40" />
            <Skeleton className="h-3 w-56" />
          </div>
          <Skeleton className="h-6 w-20 rounded-full" />
        </div>
      ))}
    </div>
  )
}
