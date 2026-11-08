import { useState } from 'react'
import { ArrowLeft, Boxes, Check, ChevronRight, Container, Globe, LayoutGrid, List, Search, BellOff, Bell, EyeOff, HeartPulse } from 'lucide-react'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { Separator } from '@/components/ui/separator'
import { SignalIcon } from '@/components/signal-icon'
import { StateBadge } from '@/components/state-badge'
import {
  serviceDegraded,
  serviceColor,
  serviceStatusLine,
  isCompletedJob,
  signalMeta,
  relTime,
  bySeverityThenTime,
  dockerCase,
  httpCase,
  isProjectMuted,
  unitMuteState,
  mutePatternFor,
  DOCKER_CASES,
  HTTP_CASES,
  NO_STACK,
} from '@/lib/domain'
import { cn } from '@/lib/utils'
import { SourceFilter } from '@/components/source-filter'
import { Sparkline } from '@/components/sparkline'
import { HostCard } from '@/components/host-card'
import { usePref } from '@/lib/prefs'

// The fleet board's signature: a status lamp. A filled dot in the unit's hue —
// live units carry a soft glow, degraded units glow brighter and pulse, and a
// clean exit reads as a quiet HOLLOW dot (finished, not failed) so one-shot
// init jobs stop looking like dead boxes.
function Lamp({ s, muted }) {
  // Muted = a powered-down channel: a hollow slate ring, no glow, no pulse —
  // so a silenced unit can never be mistaken for a live or alarming one.
  if (muted) {
    return (
      <span
        className="size-2.5 shrink-0 rounded-full border-[1.5px] bg-transparent"
        style={{ borderColor: 'var(--sev-stopped)' }}
        aria-hidden
      />
    )
  }
  // A completed one-shot is DONE, not down — a quiet check, never a lamp.
  if (isCompletedJob(s)) {
    return <Check className="size-3.5 shrink-0 text-muted-foreground/55" aria-hidden />
  }
  const color = serviceColor(s)
  const degraded = serviceDegraded(s)
  return (
    <span className="relative grid size-3 shrink-0 place-items-center" aria-hidden>
      
      <span
        className="size-2.5 rounded-full"
        style={{
          backgroundColor: color,
          boxShadow:
            `0 0 0 3px color-mix(in oklch, ${color} 15%, transparent),` +
            ` 0 0 9px color-mix(in oklch, ${color} ${degraded ? 75 : 50}%, transparent)`,
        }}
      />
    </span>
  )
}

const degradedSurface = {
  borderColor: 'color-mix(in oklch, var(--sev-unhealthy) 45%, var(--border))',
  backgroundColor: 'color-mix(in oklch, var(--sev-unhealthy) 8%, var(--card))',
}

// A muted unit drops the amber alarm look for a calm slate-edged card — it's
// silenced on purpose, not a problem to chase.
const mutedSurface = {
  borderColor: 'color-mix(in oklch, var(--sev-stopped) 28%, var(--border))',
  backgroundColor: 'var(--card)',
}

function RestartPill({ count }) {
  if (!count) return null
  return (
    <span
      className="font-data inline-flex shrink-0 items-center gap-0.5 rounded-full border px-1.5 py-0.5 text-[0.6875rem] tabular-nums text-muted-foreground"
      title={`${count} restarts`}
    >
      ↻ {count}
    </span>
  )
}

// Per-unit mute toggle. Mirrors the stack control on ProjectHeader. Three states:
//   none  — "Mute" (dashed; hover-revealed on cards via the parent `group`)
//   self  — individually muted → "Muted" / "Unmute" (lifts this unit's mute)
//   stack — silenced via its stack → "Muted via stack" / "Unmute stack"
//           (lifts the covering stack mute, i.e. resumes ALL its units)
// `pattern` is the exact mute to remove on unmute (the unit's own name when
// 'self', the covering stack pattern when 'stack'). `compact` is the dense card
// variant; the detail header uses the roomier one.
function UnitMute({ state, name, pattern, onMute, onUnmute, compact = false }) {
  // Cards are clickable, so the toggle must not also open the unit.
  const stop = (fn, arg) => (e) => { e.stopPropagation(); fn(arg) }
  const size = compact ? 'gap-1 px-1.5 py-0.5' : 'gap-1.5 px-2.5 py-1'

  let button, desc
  if (state === 'none') {
    // The tooltip spells out the one thing that isn't obvious: muting is a
    // monitoring switch, not a container action.
    desc = `Silences ${name}: Digdown stops raising incidents and diagnosing it. Doesn't stop or restart the container.`
    button = (
      <button
        type="button"
        onClick={stop(onMute, name)}
        aria-label={`Mute ${name}`}
        className={cn(
          'label-eyebrow inline-flex shrink-0 items-center rounded-full border border-dashed text-[0.625rem] text-muted-foreground/70 transition-all hover:border-solid hover:border-foreground/40 hover:text-foreground',
          size,
          compact && 'opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 focus-visible:opacity-100',
        )}
      >
        <BellOff className="size-3" /> Mute
      </button>
    )
  } else {
    // Muted (self or stack): an actionable Unmute pill. Stack unmute lifts the
    // whole stack, so it's labelled "Unmute stack".
    const stack = state === 'stack'
    const restLabel = stack ? 'Muted via stack' : 'Muted'
    const actLabel = stack ? 'Unmute stack' : 'Unmute'
    desc = stack
      ? `Lifts the mute on the whole ${pattern} stack. This resumes incidents and diagnosis for all its containers. Doesn't touch the containers.`
      : `Resumes incidents and diagnosis for ${name}. Doesn't change the container.`
    button = (
      <button
        type="button"
        onClick={stop(onUnmute, pattern)}
        aria-label={actLabel}
        className={cn(
          'group/mute label-eyebrow inline-flex shrink-0 items-center rounded-full border text-[0.625rem] text-muted-foreground transition-colors hover:border-foreground/40 hover:text-foreground',
          stack && 'border-dashed text-muted-foreground/70 hover:border-solid',
          size,
        )}
        style={stack ? undefined : { backgroundColor: 'color-mix(in oklch, var(--muted-foreground) 12%, transparent)' }}
      >
        {compact ? (
          <>
            <BellOff className="size-3 group-hover/mute:hidden" />
            <Bell className="hidden size-3 group-hover/mute:inline" />
            <span className="group-hover/mute:hidden">{restLabel}</span>
            <span className="hidden group-hover/mute:inline">{actLabel}</span>
          </>
        ) : (
          <><Bell className="size-3" /> {actLabel}</>
        )}
      </button>
    )
  }

  return (
    <Tooltip>
      <TooltipTrigger asChild>{button}</TooltipTrigger>
      <TooltipContent side="top" className="max-w-xs">{desc}</TooltipContent>
    </Tooltip>
  )
}

const HTTP_BADGE = {
  ok:    { label: 'HTTP', token: 'live' },
  error: { label: 'HTTP', token: 'sev-crash' },
  slow:  { label: 'SLOW', token: 'sev-latency' },
}
function HttpBadge({ s }) {
  const c = httpCase(s)
  if (!c) return null
  const b = HTTP_BADGE[c]
  const code = s.http?.code, lat = s.http?.latency_ms
  return (
    <span
      className="font-data inline-flex shrink-0 items-center gap-1 rounded-full border px-1.5 py-0.5 text-[0.625rem] leading-none"
      style={{ color: `var(--${b.token})`, borderColor: `color-mix(in oklch, var(--${b.token}) 30%, transparent)` }}
      title={`HTTP ${code ?? 'no response'}${lat != null ? `, ${lat}ms` : ''}`}
    >
      <span className="size-1.5 rounded-full" style={{ backgroundColor: `var(--${b.token})` }} />
      {b.label}
    </span>
  )
}

// Board view — one container as an instrument readout: lamp + name on the rail,
// status line and image aligned beneath it. The card is clickable (opens the
// unit's detail); it's a div, not a button, so the per-unit mute toggle can sit
// inside as its own button without nesting.
function FleetTile({ s, onOpen, muted, muteState, mutePattern, onMute, onUnmute }) {
  const degraded = serviceDegraded(s) && !muted
  const open = () => onOpen(s)
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={open}
      onKeyDown={(e) => {
        // Only the card's own key presses open it — not keys on the inner mute button.
        if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) {
          e.preventDefault()
          open()
        }
      }}
      className="group glass-lite glass-raise w-full cursor-pointer rounded-lg px-4 py-3.5 text-left focus-visible:ring-2 focus-visible:ring-ring/60 focus-visible:outline-none"
      style={muted ? mutedSurface : degraded ? degradedSurface : undefined}
    >
      <div className="flex min-w-0 items-center gap-2.5">
        <Lamp s={s} muted={muted} />
        <span
          className={cn('font-data min-w-0 flex-1 truncate text-sm font-medium',
                        muted ? 'text-muted-foreground' : 'text-foreground')}
          title={s.name}
        >
          {s.name}
        </span>
        <HttpBadge s={s} />
        <RestartPill count={s.restart_count} />
        <UnitMute state={muteState} name={s.name} pattern={mutePattern} onMute={onMute} onUnmute={onUnmute} compact />
        <ChevronRight className="size-4 shrink-0 text-muted-foreground/40 transition-transform group-hover:translate-x-0.5 group-hover:text-muted-foreground" />
      </div>
      <div
        className="mt-2 pl-[1.375rem] text-xs"
        style={{ color: degraded ? 'var(--sev-unhealthy)' : 'var(--muted-foreground)' }}
      >
        {serviceStatusLine(s)}
      </div>
      <div className="mt-2.5 pl-[1.375rem]">
        <span
          className="font-data inline-block max-w-full truncate rounded bg-muted/50 px-1.5 py-0.5 align-bottom text-[0.6875rem] leading-relaxed text-muted-foreground/80"
          title={s.image}
        >
          {s.image || 'Unknown image'}
        </span>
      </div>
      {(s.cpu_series?.length > 1 || s.mem_series?.length > 1) && (
        <div className="mt-2.5 flex items-center gap-3 pl-[1.375rem]">
          <Sparkline label="mem" points={s.mem_series} max={100} color="var(--sev-mempressure)" />
          <Sparkline label="cpu" points={s.cpu_series} max="auto" color="var(--muted-foreground)" />
        </div>
      )}
    </div>
  )
}

// List view — one compact row: lamp · name · status · restarts · image.
// Clickable (opens the unit's detail); a div, not a button, so the per-unit
// mute toggle can live inside as its own button.
const LIST_COLUMNS = 'grid-cols-[minmax(0,1.6fr)_minmax(0,1.2fr)_3.5rem] md:grid-cols-[minmax(0,1.5fr)_minmax(0,1.1fr)_minmax(0,1fr)_3.5rem_3.5rem_4rem_3.5rem]'

function percent(value) {
  return value == null ? '-' : `${Math.round(value)}%`
}

function FleetRow({ s, onOpen, muted, muteState, mutePattern, onMute, onUnmute }) {
  const degraded = serviceDegraded(s) && !muted
  const open = () => onOpen(s)
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={open}
      onKeyDown={(e) => {
        // Only the row's own key presses open it, not keys on the inner mute button.
        if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) {
          e.preventDefault()
          open()
        }
      }}
      className={cn('group grid w-full cursor-pointer items-center gap-x-3 px-3 py-1.5 text-left transition-colors hover:bg-accent/40 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring', LIST_COLUMNS)}
      style={muted ? mutedSurface : degraded ? degradedSurface : undefined}
    >
      <span className="flex min-w-0 items-center gap-2.5">
        <Lamp s={s} muted={muted} />
        <span
          className={cn('font-data min-w-0 truncate text-sm', muted ? 'text-muted-foreground' : 'text-foreground')}
          title={s.name}
        >
          {s.name}
        </span>
      </span>
      <span className="flex min-w-0 items-center gap-2 text-sm" style={{ color: degraded ? 'var(--sev-unhealthy)' : 'var(--muted-foreground)' }}>
        <span className="truncate">{serviceStatusLine(s)}</span>
        <HttpBadge s={s} />
      </span>
      <span className="font-data hidden min-w-0 truncate text-xs text-muted-foreground md:block" title={s.image}>
        {s.image || 'Unknown image'}
      </span>
      <span className="font-data hidden text-right text-xs tabular-nums text-muted-foreground md:block">{percent(s.cpu_percent)}</span>
      <span className="font-data hidden text-right text-xs tabular-nums text-muted-foreground md:block">{percent(s.mem_percent)}</span>
      <span className="font-data hidden text-right text-xs tabular-nums md:block" style={{ color: s.restart_count > 0 ? 'var(--sev-unhealthy)' : 'var(--muted-foreground)' }}>
        {s.restart_count ?? 0}
      </span>
      <span className="flex items-center justify-end gap-1">
        <UnitMute state={muteState} name={s.name} pattern={mutePattern} onMute={onMute} onUnmute={onUnmute} compact />
        <ChevronRight className="size-4 shrink-0 text-muted-foreground/40" />
      </span>
    </div>
  )
}

function FleetListHeader() {
  return (
    <div aria-hidden className={cn('grid items-center gap-x-3 border-b px-3 py-1.5 text-xs text-muted-foreground', LIST_COLUMNS)}>
      <span>Container</span>
      <span>Status</span>
      <span className="hidden md:block">Image</span>
      <span className="hidden text-right md:block">CPU</span>
      <span className="hidden text-right md:block">Memory</span>
      <span className="hidden text-right md:block">Restarts</span>
      <span />
    </div>
  )
}

// A compose project as a board section — a dot (warm + pulsing if any unit in it
// is degraded), the project name, its unit count, a Mute/Unmute toggle for the
// whole stack, then a hairline to the edge. A muted stack reads quiet (no pulse)
// and carries a "Muted" badge so it's obvious it's intentionally silenced.
function ProjectHeader({ project, count, degraded, muted, onMute, onUnmute, healing }) {
  const color = muted
    ? 'var(--muted-foreground)'
    : degraded ? 'var(--sev-unhealthy)' : 'var(--muted-foreground)'
  return (
    <div className="flex items-center gap-2.5">
      <span
        className={'size-2 shrink-0 rounded-full'}
        style={{ backgroundColor: color, opacity: muted ? 0.6 : 1 }}
      />
      <span className={cn('label-eyebrow', muted ? 'text-muted-foreground' : 'text-foreground/80')}>
        {project}
      </span>
      <span className="font-data text-xs tabular-nums text-muted-foreground">{count}</span>
      {healing && !muted && (
        <a
          href="#/auto-heal"
          title="Fixes in this stack run by themselves. Click to change this."
          className="inline-flex items-center gap-1 rounded-sm border px-1.5 py-px text-xs text-primary hover:border-primary"
        >
          <HeartPulse className="size-3" aria-hidden /> Auto heal on
        </a>
      )}
      {/* One control, not two. Muted: a filled pill reading "Muted" that swaps to
          "Unmute" on hover (state at rest, action on hover). Unmuted: a quiet
          dashed "Mute". The label is never duplicated by a separate badge. */}
      {(onMute || onUnmute) && project !== 'No stack' && (
        muted ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                onClick={() => onUnmute?.(project)}
                aria-label={`Unmute ${project}`}
                className="group/mute label-eyebrow inline-flex items-center gap-1 rounded-sm border px-1.5 py-px text-[0.625rem] text-muted-foreground transition-colors hover:border-foreground/40 hover:text-foreground"
                style={{ backgroundColor: 'color-mix(in oklch, var(--muted-foreground) 12%, transparent)' }}
              >
                <BellOff className="size-3 group-hover/mute:hidden" />
                <Bell className="hidden size-3 group-hover/mute:inline" />
                <span className="group-hover/mute:hidden">Muted</span>
                <span className="hidden group-hover/mute:inline">Unmute</span>
              </button>
            </TooltipTrigger>
            <TooltipContent side="top" className="max-w-xs">
              Resumes incidents and diagnosis for the whole {project} stack. Doesn&apos;t touch the containers.
            </TooltipContent>
          </Tooltip>
        ) : (
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                onClick={() => onMute?.(project)}
                aria-label={`Mute ${project}`}
                className="label-eyebrow inline-flex items-center gap-1 rounded-sm border border-dashed px-2 py-0.5 text-[0.625rem] text-muted-foreground/70 transition-colors hover:border-solid hover:border-foreground/40 hover:text-foreground"
              >
                <BellOff className="size-3" /> Mute
              </button>
            </TooltipTrigger>
            <TooltipContent side="top" className="max-w-xs">
              Silences the whole {project} stack: Digdown stops raising incidents and diagnosing it. Doesn&apos;t stop or restart the containers.
            </TooltipContent>
          </Tooltip>
        )
      )}
      <span className="h-px flex-1 bg-border" />
    </div>
  )
}

// Read-only banner for projects silenced via config (`exclude_projects`): those
// containers aren't watched, so they never appear as units — this is the only
// place they're visible. Editing `digdown.yml` + restart is how you change it.
function ExcludedBanner({ projects = [] }) {
  if (!projects.length) return null
  return (
    <div className="glass-lite flex flex-wrap items-center gap-2 rounded-lg px-4 py-2.5 text-xs text-muted-foreground">
      <EyeOff className="size-3.5 shrink-0" />
      <span>Not watched, because your settings exclude them:</span>
      {projects.map((p) => (
        <span key={p} className="font-data rounded-full border px-1.5 py-0.5 text-foreground/80">{p}</span>
      ))}
      <span className="text-muted-foreground">To change this, edit <span className="font-data">exclude_projects</span> in your settings file</span>
    </div>
  )
}

const bySeverityThenName = (a, b) =>
  serviceDegraded(b) - serviceDegraded(a) || a.name.localeCompare(b.name)

// Group the fleet by compose project; trouble leads — projects with a degraded
// unit sort first, then alphabetically.
function groupByProject(services) {
  const groups = new Map()
  for (const s of services) {
    const key = s.project || 'No stack'
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key).push(s)
  }
  return [...groups.entries()]
    .map(([project, items]) => ({
      project,
      items: [...items].sort(bySeverityThenName),
      degraded: items.some(serviceDegraded),
    }))
    .sort((a, b) => b.degraded - a.degraded || a.project.localeCompare(b.project))
}

// One fact line in the detail readout.
function Row({ label, children }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5">
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className="font-data text-right text-sm text-foreground">{children}</span>
    </div>
  )
}

function Section({ title, children }) {
  return (
    <section className="space-y-3">
      <h3 className="label-eyebrow text-muted-foreground">{title}</h3>
      {children}
    </section>
  )
}

// The status word for the header badge — mirrors the lamp's states.
function statusBadge(s) {
  if (serviceDegraded(s)) return { label: 'Has a problem', color: 'var(--sev-unhealthy)' }
  if (isCompletedJob(s)) return { label: 'Completed', color: 'var(--muted-foreground)' }
  if (s.status === 'running') return { label: 'Running', color: 'var(--live)' }
  return { label: 'Idle', color: 'var(--muted-foreground)' }
}

// Detail view — opened by clicking a unit. Mirrors the incident review: a back
// link, the unit's vitals, and the incidents Digdown has raised against it
// (each opens the case file). Owns the column while a unit is selected.
function FleetDetail({ s, incidents, muteState = 'none', mutePattern, onBack, onOpenIncident, onMute, onUnmute }) {
  const muted = muteState !== 'none'
  const degraded = serviceDegraded(s) && !muted
  const badge = muted ? { label: 'Muted', color: 'var(--sev-stopped)' } : statusBadge(s)
  const related = incidents
    .filter((i) => i.target === s.name || i.target === s.service)
    .sort(bySeverityThenTime)

  const identity = [
    ['Image', s.image],
    ['Project', s.project],
    ['Service', s.service],
    ['Container', s.id],
  ].filter(([, v]) => v)

  return (
    <div className="mx-auto w-full max-w-3xl">
      <button
        type="button"
        onClick={onBack}
        className="mb-5 flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-4" /> Back to fleet
      </button>

      <div className="mb-6 flex items-center gap-3">
        <Lamp s={s} muted={muted} />
        <h1 className="font-data min-w-0 flex-1 truncate text-lg text-foreground" title={s.name}>
          {s.name}
        </h1>
        <RestartPill count={s.restart_count} />
        {(onMute || onUnmute) && s.name && (
          <UnitMute state={muteState} name={s.name} pattern={mutePattern} onMute={onMute} onUnmute={onUnmute} />
        )}
        <span
          className="label-eyebrow inline-flex items-center gap-1.5 rounded-sm border px-1.5 py-px leading-none"
          style={{
            color: badge.color,
            backgroundColor: `color-mix(in oklch, ${badge.color} 10%, transparent)`,
            borderColor: `color-mix(in oklch, ${badge.color} 26%, transparent)`,
          }}
        >
          {badge.label}
        </span>
      </div>

      <div className="grid gap-6 sm:grid-cols-2 sm:items-start">
        <Section title="Status">
          <div className="glass rounded-lg px-4 py-1" style={degraded ? degradedSurface : undefined}>
            <Row label="State">{serviceStatusLine(s)}</Row>
            {s.health && s.health !== 'none' && (
              <>
                <Separator />
                <Row label="Health">{s.health}</Row>
              </>
            )}
            {s.exit_code != null && (
              <>
                <Separator />
                <Row label="Exit code">{s.exit_code}</Row>
              </>
            )}
            {s.oom_killed && (
              <>
                <Separator />
                <Row label="Ran out of memory">yes</Row>
              </>
            )}
            <Separator />
            <Row label="Restarts">{s.restart_count ?? 0}</Row>
            {s.http && (
              <>
                <Separator />
                <Row label="HTTP">{`${s.http.status}, ${s.http.code ?? 'no response'} · ${s.http.latency_ms ?? '?'}ms`}</Row>
              </>
            )}
          </div>
        </Section>

        <Section title="About">
          <div className="glass rounded-lg px-4 py-1">
            {identity.length > 0 ? (
              identity.map(([label, value], i) => (
                <div key={label}>
                  {i > 0 && <Separator />}
                  <Row label={label}>
                    <span className="break-all">{value}</span>
                  </Row>
                </div>
              ))
            ) : (
              <p className="py-2 text-sm text-muted-foreground">No image metadata reported.</p>
            )}
          </div>
        </Section>
      </div>

      <div className="mt-6">
        <Section title={`Incidents (${related.length})`}>
          {related.length === 0 ? (
            <div className="glass rounded-lg px-4 py-5 text-center text-sm text-muted-foreground">
              Digdown hasn&apos;t raised any incidents for this container.
            </div>
          ) : (
            <div className="flex flex-col gap-2">
              {related.map((i) => {
                const meta = signalMeta(i.signal?.type)
                return (
                  <button
                    key={i.id}
                    type="button"
                    onClick={() => onOpenIncident?.(i)}
                    disabled={!onOpenIncident}
                    className="group glass-lite glass-raise flex items-center gap-3 rounded-lg px-3.5 py-2.5 text-left focus-visible:ring-2 focus-visible:ring-ring/60 focus-visible:outline-none disabled:cursor-default"
                  >
                    <SignalIcon type={i.signal?.type} size="sm" />
                    <span className="label-eyebrow shrink-0" style={{ color: `var(--${meta.token})` }}>
                      {meta.short}
                    </span>
                    <StateBadge incident={i} className="shrink-0" />
                    <span className="flex-1" />
                    <span className="font-data shrink-0 text-sm tabular-nums text-muted-foreground">
                      {relTime(i.created_at)}
                    </span>
                    {onOpenIncident && (
                      <ChevronRight className="size-4 shrink-0 text-muted-foreground/40 transition-transform group-hover:translate-x-0.5 group-hover:text-muted-foreground" />
                    )}
                  </button>
                )
              })}
            </div>
          )}
        </Section>
      </div>
    </div>
  )
}

export function Fleet({ autoHealStacks = [], services = [], incidents = [], mutes = [], excludedProjects = [], host = null, onMute, onUnmute, onOpenIncident, selected: selectedProp, onSelect, initialStatus = 'all' }) {
  const [view, setView] = usePref('container-view', ['board', 'list'], 'list')
  // Seeded from the entry point (e.g. the Situation "degraded" tile); Fleet
  // remounts whenever the section opens, so this honors the lens each time.
  const [source, setSource] = useState('docker')   // 'docker' | 'http'
  const [caseKey, setCaseKey] = useState(initialStatus === 'degraded' ? 'unhealthy' : 'all')
  const [query, setQuery] = useState('')
  // Selection can be driven externally (e.g. clicking a unit on the overview
  // constellation deep-links here) or kept locally when uncontrolled.
  const [selfSelected, setSelfSelected] = useState(null)
  const selected = selectedProp !== undefined ? selectedProp : selfSelected
  const setSelected = onSelect || setSelfSelected

  if (!services.length) {
    return (
      <div className="glass rounded-lg border-dashed py-16 text-center">
        <Boxes className="mx-auto size-6 text-muted-foreground/50" />
        <p className="mt-3 font-semibold text-foreground">No containers to show</p>
        <p className="mt-1.5 text-sm text-muted-foreground">
          Check that Docker is running, and which stacks you chose in Settings.
        </p>
      </div>
    )
  }

  // A unit is open — hand the column to its detail view. Re-resolve from the
  // live roster so the detail reflects fresh status as it refreshes.
  if (selected) {
    const live =
      services.find((s) => (s.id || s.name) === (selected.id || selected.name)) || selected
    return (
      <FleetDetail
        s={live}
        incidents={incidents}
        muteState={unitMuteState(live, mutes)}
        mutePattern={mutePatternFor(live, mutes)}
        onBack={() => setSelected(null)}
        onOpenIncident={onOpenIncident}
        onMute={onMute}
        onUnmute={onUnmute}
      />
    )
  }

  const inHttp = source === 'http'
  const cases = inHttp ? HTTP_CASES : DOCKER_CASES
  const caseOf = (s) => (inHttp ? httpCase(s) : dockerCase(s))
  const scoped = inHttp ? services.filter((s) => s.http != null) : services
  const counts = Object.fromEntries(cases.map((c) => [c.key,
    c.key === 'all' ? scoped.length : scoped.filter((s) => caseOf(s) === c.key).length]))
  const q = query.trim().toLowerCase()
  const visible = scoped.filter((s) => {
    const okCase = caseKey === 'all' || caseOf(s) === caseKey
    const okQuery = !q || s.name.toLowerCase().includes(q) || (s.image || '').toLowerCase().includes(q)
    return okCase && okQuery
  })
  // Muted stacks sink below the live fleet — they're silenced on purpose, not
  // work to attend to. (Array.sort is stable, so trouble-first order holds
  // within each partition.)
  const groups = groupByProject(visible)
    .sort((a, b) => (isProjectMuted(a.project, mutes) ? 1 : 0) - (isProjectMuted(b.project, mutes) ? 1 : 0))
  const filtered = visible.length !== scoped.length

  return (
    <section aria-label="Containers" className="space-y-5">
      <ExcludedBanner projects={excludedProjects} />
      <div className="flex flex-wrap items-center gap-2">
        {services.some((s) => s.http != null) && (
          <Tabs value={source} onValueChange={(v) => { setSource(v); setCaseKey('all') }}>
            <TabsList>
              <TabsTrigger value="docker" className="label-eyebrow gap-1.5">
                <Container className="size-3.5" /> Docker
              </TabsTrigger>
              <TabsTrigger value="http" className="label-eyebrow gap-1.5">
                <Globe className="size-3.5" /> HTTP
              </TabsTrigger>
            </TabsList>
          </Tabs>
        )}
        <SourceFilter
          sources={cases.map((c) => ({ key: c.key, label: c.label, count: counts[c.key] }))}
          value={caseKey}
          onChange={setCaseKey}
        />
        <span className="text-xs text-muted-foreground">
          {filtered ? `${visible.length} of ${scoped.length}` : scoped.length} {scoped.length === 1 ? 'container' : 'containers'}
        </span>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <div className="relative">
            <Search
              aria-hidden
              className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground"
            />
            <input
              id="fleet-search"
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Filter by name"
              aria-label="Filter containers by name or image"
              className="glass-lite h-8 w-44 rounded-md pr-2.5 pl-8 text-sm text-foreground placeholder:text-muted-foreground focus:ring-1 focus:ring-foreground/25 focus:outline-none"
            />
          </div>
          <Tabs value={view} onValueChange={setView}>
            <TabsList>
              <TabsTrigger value="board" className="label-eyebrow gap-1.5">
                <LayoutGrid className="size-3.5" /> Board
              </TabsTrigger>
              <TabsTrigger value="list" className="label-eyebrow gap-1.5">
                <List className="size-3.5" /> List
              </TabsTrigger>
            </TabsList>
          </Tabs>
        </div>
      </div>

      {groups.length === 0 ? (
        <div className="glass rounded-lg border-dashed py-12 text-center text-sm text-muted-foreground">
          Nothing matches
          <button
            type="button"
            onClick={() => {
              setSource('docker')
              setCaseKey('all')
              setQuery('')
            }}
            className="ml-2 text-foreground underline-offset-2 hover:underline"
          >
            clear filters
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          <HostCard host={host} />
          {groups.map((g) => {
            const gMuted = isProjectMuted(g.project, mutes)
            return (
              <div key={g.project} className="space-y-1.5">
                <ProjectHeader
                  project={g.project}
                  count={g.items.length}
                  degraded={g.degraded}
                  muted={gMuted}
                  healing={autoHealStacks?.includes(g.project === 'No stack' ? NO_STACK : g.project)}
                  onMute={onMute}
                  onUnmute={onUnmute}
                />
                {/* A muted stack reads as powered-down: desaturated and dimmed,
                    restoring to full on hover so you can still inspect it. */}
                <div
                  className={cn(
                    'transition-[opacity,filter] duration-200',
                    gMuted && 'opacity-60 saturate-50 hover:opacity-100 hover:saturate-100',
                  )}
                >
                  {view === 'board' ? (
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                      {g.items.map((s) => {
                        const ms = unitMuteState(s, mutes)
                        return (
                          <FleetTile
                            key={s.id || s.name}
                            s={s}
                            onOpen={setSelected}
                            muted={ms !== 'none'}
                            muteState={ms}
                            mutePattern={mutePatternFor(s, mutes)}
                            onMute={onMute}
                            onUnmute={onUnmute}
                          />
                        )
                      })}
                    </div>
                  ) : (
                    <div className="glass divide-y overflow-hidden rounded-xl">
                      <FleetListHeader />
                      {g.items.map((s) => {
                        const ms = unitMuteState(s, mutes)
                        return (
                          <FleetRow
                            key={s.id || s.name}
                            s={s}
                            onOpen={setSelected}
                            muted={ms !== 'none'}
                            muteState={ms}
                            mutePattern={mutePatternFor(s, mutes)}
                            onMute={onMute}
                            onUnmute={onUnmute}
                          />
                        )
                      })}
                    </div>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
}
