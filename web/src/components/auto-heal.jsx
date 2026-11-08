import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { ConfirmAction } from '@/components/confirm-action'
import { EditableField } from '@/components/settings'
import { MODES } from '@/lib/setup'
import { healOutcome, NO_STACK, relTime, stackLabel, watchedStacks } from '@/lib/domain'
import { routeHash } from '@/lib/routes'
import { cn } from '@/lib/utils'

// Turning auto heal on also allows fixes, if they aren't already, so it takes one
// step from here rather than a trip to Settings first.
const ALSO_ALLOWS = ` This also sets Digdown to "${MODES.execute.title}", so you can approve fixes for other apps too.`

function Row({ title, mono, children, action }) {
  return (
    <li className="flex items-center justify-between gap-4 py-3">
      <div className="min-w-0">
        <span className={cn('block truncate text-sm text-foreground', mono && 'font-data')}>{title}</span>
        <span className="block max-w-prose text-sm text-muted-foreground">{children}</span>
      </div>
      <div className="shrink-0">{action}</div>
    </li>
  )
}

function Apps({ fields, mode, services, onPatch, onReset }) {
  const stacksField = fields.find((f) => f.key === 'auto_heal_stacks')
  const allField = fields.find((f) => f.key === 'auto_heal_all')
  const attempts = fields.find((f) => f.key === 'auto_heal_max_attempts')
  const cleanup = fields.find((f) => f.key === 'auto_heal_disk_cleanup')
  const enabled = stacksField?.value || []
  const all = Boolean(allField?.value)
  const canRun = mode === 'execute'
  const stacks = watchedStacks(services, enabled)
  const paused = !canRun && (all || enabled.length > 0)
  const report = (e) => toast.error(String(e.message || e))
  // Allowing fixes comes first, as Digdown refuses auto heal without it.
  const turnOn = async (key, value, message) => {
    try {
      if (!canRun) await onPatch('mode', 'execute')
      await onPatch(key, value)
      toast(message)
    } catch (e) {
      report(e)
    }
  }
  const turnOff = (key, value, message) => onPatch(key, value).then(() => toast(message)).catch(report)
  const also = canRun ? '' : ALSO_ALLOWS
  return (
    <section className="glass min-w-0 rounded-xl p-3.5">
      <h2 className="panel-title">Which apps it looks after</h2>
      <p className="mt-1 max-w-prose text-sm text-muted-foreground">
        For these apps, fixes that need no decision from you run without a click. Digdown checks that each
        fix held, undoes it if it didn&apos;t, and tells you what it did.
      </p>
      <details className="mt-1.5 max-w-prose text-sm text-muted-foreground">
        <summary className="cursor-pointer text-foreground underline-offset-2 hover:underline">What it fixes, and what it leaves for you</summary>
        <p className="mt-2">
          It fixes a crash, a restart loop, running out of memory, a failed health check, or a database an app
          needs. It only starts or restarts containers and raises memory limits, up to double.
        </p>
        <p className="mt-2">
          A wrong setting, a missing secret or a bug in the app needs you, so it explains the cause and asks.
          So does anything someone stopped or paused on purpose, or a job that finished.
        </p>
      </details>
      {paused && (
        <div className="mt-3 flex flex-col gap-2 rounded-lg border bg-background px-3.5 py-2.5 text-sm text-foreground sm:flex-row sm:items-center sm:justify-between">
          <span>Auto heal is paused, because Digdown is set to &quot;{MODES[mode]?.title || mode}&quot;.</span>
          <ConfirmAction
            title="Resume auto heal?"
            description={`Fixes for the apps below will run by themselves again.${ALSO_ALLOWS}`}
            confirmLabel="Resume auto heal"
            onConfirm={() => onPatch('mode', 'execute').then(() => toast('Auto heal is running again')).catch(report)}
          >
            <Button size="sm" variant="outline" className="shrink-0">Resume</Button>
          </ConfirmAction>
        </div>
      )}
      <ul className="mt-2 divide-y">
        {allField && (
          <Row
            title="Every app"
            action={all ? (
              <Button size="sm" variant="outline" aria-label="Turn off auto heal for every app"
                onClick={() => turnOff('auto_heal_all', false, 'Auto heal is off for every app. Apps you turned on one by one keep it.')}>
                Turn off
              </Button>
            ) : (
              <ConfirmAction
                title="Let Digdown fix every app by itself?"
                description={`When any container fails, including in apps you add later, Digdown can start or restart it, start something it depends on, or raise its memory limit, with nobody checking first. If the AI gets the cause wrong, a working part of an app could restart. After a few tries it stops and asks you.${also}`}
                confirmLabel="Turn on for every app"
                onConfirm={() => turnOn('auto_heal_all', true, 'Auto heal is on for every app')}
              >
                <Button size="sm" variant="outline" aria-label="Turn on auto heal for every app">Turn on</Button>
              </ConfirmAction>
            )}
          >
            {all ? 'On, including apps you add later' : 'Including apps you add later'}
          </Row>
        )}
        {stacks.map((stack) => {
          const own = enabled.includes(stack.name)
          const label = stackLabel(stack.name)
          return (
            <Row
              key={stack.name}
              title={label}
              mono={stack.name !== NO_STACK}
              action={all ? null : own ? (
                <Button size="sm" variant="outline" aria-label={`Turn off auto heal for ${label}`}
                  onClick={() => turnOff('auto_heal_stacks', enabled.filter((n) => n !== stack.name), `Auto heal is off for ${label}`)}>
                  Turn off
                </Button>
              ) : (
                <ConfirmAction
                  title={`Let Digdown fix ${label} by itself?`}
                  description={`When a container here fails, Digdown can start or restart it, start something it depends on, or raise its memory limit, with nobody checking first. If the AI gets the cause wrong, a working part of the app could restart. After a few tries it stops and asks you.${also}`}
                  confirmLabel="Turn on auto heal"
                  onConfirm={() => turnOn('auto_heal_stacks', [...enabled, stack.name], `Auto heal is on for ${label}`)}
                >
                  <Button size="sm" variant="outline" aria-label={`Turn on auto heal for ${label}`}>Turn on</Button>
                </ConfirmAction>
              )}
            >
              {all || own ? 'Fixes run by themselves' : 'Fixes wait for your approval'}
            </Row>
          )
        })}
        {stacks.length === 0 && (
          <li className="py-3 text-sm text-muted-foreground">
            Digdown isn&apos;t watching any apps yet.{' '}
            <a href={routeHash({ section: 'settings', tab: 'apps' })} className="underline underline-offset-2">Choose apps to watch</a>.
          </li>
        )}
        {cleanup && (
          <Row
            title="Clear unused Docker data when the disk is full"
            action={cleanup.value ? (
              <Button size="sm" variant="outline" onClick={() => turnOff('auto_heal_disk_cleanup', false, 'Disk cleanup is off')}>
                Turn off
              </Button>
            ) : (
              <ConfirmAction
                title="Let Digdown delete unused Docker data?"
                description={`If the disk is full now, this runs straight away. It deletes images nothing uses and Docker's build cache, which can't be undone, and later builds will be slower. It never deletes containers or volumes.${also}`}
                confirmLabel="Turn on disk cleanup"
                onConfirm={() => turnOn('auto_heal_disk_cleanup', true, 'Disk cleanup is on')}
              >
                <Button size="sm" variant="outline">Turn on</Button>
              </ConfirmAction>
            )}
          >
            {cleanup.value
              ? 'On. If the disk fills up, this deletes images nothing uses and the build cache, at most once a day.'
              : "Off. Turning this on deletes images nothing uses and the build cache when the disk is full. It can't be undone."}
          </Row>
        )}
      </ul>
      {attempts && (
        <div className="border-t">
          <EditableField field={attempts} onSave={onPatch} onReset={onReset} />
        </div>
      )}
    </section>
  )
}

function Activity({ incidents, onOpenIncident }) {
  const [rows, setRows] = useState(null)
  // Refreshes as incidents change, so a fix shows up here as it finishes.
  useEffect(() => {
    let cancelled = false
    fetch('/api/auto-heal/activity')
      .then((r) => (r.ok ? r.json() : []))
      .then((d) => { if (!cancelled) setRows(d) })
      .catch(() => { if (!cancelled) setRows([]) })
    return () => { cancelled = true }
  }, [incidents])
  return (
    <section className="glass min-w-0 rounded-xl p-3.5">
      <h2 className="panel-title">What it has done</h2>
      
      {rows === null ? (
        <p className="mt-3 text-sm text-muted-foreground">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="mt-3 text-sm text-muted-foreground">
          Nothing yet. When auto heal fixes something, or leaves something for you, it shows up here.
        </p>
      ) : (
        <div className="mt-2 overflow-x-auto">
          <table className="w-full min-w-[34rem] text-sm">
            <thead>
              <tr className="border-b text-left text-xs text-muted-foreground">
                <th className="py-1.5 pr-3 font-medium">When</th>
                <th className="py-1.5 pr-3 font-medium">Container</th>
                <th className="py-1.5 pr-3 font-medium">Result</th>
                <th className="py-1.5 font-medium">What happened</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => {
                const outcome = healOutcome(row.outcome)
                const incident = incidents.find((x) => x.id === row.incident_id)
                return (
                  <tr
                    key={`${row.incident_id}-${row.ts}-${i}`}
                    tabIndex={incident ? 0 : undefined}
                    onClick={incident ? () => onOpenIncident(incident) : undefined}
                    onKeyDown={incident ? (e) => { if (e.key === 'Enter') onOpenIncident(incident) } : undefined}
                    className={cn('border-b align-top last:border-b-0', incident && 'cursor-pointer hover:bg-accent/40 focus-visible:outline-2 focus-visible:outline-ring')}
                  >
                    <td className="font-data py-2 pr-3 text-xs whitespace-nowrap text-muted-foreground">{relTime(row.ts)}</td>
                    <td className="font-data py-2 pr-3 whitespace-nowrap text-foreground">{row.target}</td>
                    <td className="py-2 pr-3 font-medium whitespace-nowrap" style={{ color: outcome.tone }}>{outcome.label}</td>
                    <td className="py-2 text-muted-foreground">{row.reason || row.summary}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

export function AutoHealPage({ config, mock, services, incidents, onPatch, onReset, onOpenIncident }) {
  if (mock || !config) {
    return <p className="text-sm text-muted-foreground">{mock ? "Auto heal isn't available with sample data." : 'Loading…'}</p>
  }
  const mode = config.fields.find((f) => f.key === 'mode')?.value
  return (
    <div className="flex w-full max-w-4xl flex-col gap-3">
      <Apps fields={config.fields} mode={mode} services={services} onPatch={onPatch} onReset={onReset} />
      <Activity incidents={incidents} onOpenIncident={onOpenIncident} />
    </div>
  )
}
