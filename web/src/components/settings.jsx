import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Check, RotateCcw } from 'lucide-react'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { Button } from '@/components/ui/button'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Input } from '@/components/ui/input'
import { ConfirmAction } from '@/components/confirm-action'
import { groupFields, FIELD_META, fieldLabel, fieldDesc } from '@/lib/config-fields'
import { MODES } from '@/lib/setup'
import { NO_STACK, stackLabel } from '@/lib/domain'
import { routeHash } from '@/lib/routes'
import { cn } from '@/lib/utils'

export function EditableField({ field, onSave, onReset }) {
  const meta = FIELD_META[field.key] || { input: 'text' }
  const [val, setVal] = useState(field.value)
  const dirty = String(val) !== String(field.value)
  const id = `setting-${field.key}`
  const save = async (next) => {
    try {
      await onSave(field.key, next)
      toast(`${fieldLabel(field.key)}: saved`)
    } catch (e) {
      toast.error(String(e.message || e))
    }
  }
  return (
    <div className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center sm:justify-between sm:gap-6">
      <div className="min-w-0">
        <label htmlFor={id} className="text-sm font-medium text-foreground">{fieldLabel(field.key)}</label>
        {fieldDesc(field.key) && <p className="text-sm text-muted-foreground">{fieldDesc(field.key)}</p>}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {meta.input === 'toggle' ? (
          <ToggleGroup id={id} type="single" value={String(field.value)} onValueChange={(v) => v && save(v === 'true')} variant="outline" size="sm">
            <ToggleGroupItem value="true" className="px-3">On</ToggleGroupItem>
            <ToggleGroupItem value="false" className="px-3">Off</ToggleGroupItem>
          </ToggleGroup>
        ) : (
          <>
            <Input
              id={id}
              type={meta.input === 'number' ? 'number' : 'text'}
              value={val}
              onChange={(e) => setVal(meta.input === 'number' ? Number(e.target.value) : e.target.value)}
              className="font-data h-8 w-24 text-right"
            />
            <Button size="sm" variant="outline" disabled={!dirty} aria-label={`Save ${fieldLabel(field.key)}`} onClick={() => save(val)}>Save</Button>
          </>
        )}
        {field.source === 'override' && (
          <Button size="icon-sm" variant="ghost" aria-label={`Reset ${fieldLabel(field.key)} to its original value`} onClick={() => onReset(field.key)}>
            <RotateCcw className="size-3.5" />
          </Button>
        )}
      </div>
    </div>
  )
}

// Fine-tuning holds several settings, so they're edited together and saved once.
function TuningForm({ groups, onSave, onReset }) {
  const fields = groups.flatMap((g) => g.fields)
  const [draft, setDraft] = useState(() => Object.fromEntries(fields.map((f) => [f.key, f.value])))
  const [busy, setBusy] = useState(false)
  const changed = fields.filter((f) => String(draft[f.key]) !== String(f.value))
  const set = (key, value) => setDraft((d) => ({ ...d, [key]: value }))
  const save = async () => {
    setBusy(true)
    for (const f of changed) {
      const meta = FIELD_META[f.key] || {}
      try {
        await onSave(f.key, meta.input === 'number' ? Number(draft[f.key]) : draft[f.key])
      } catch (e) {
        toast.error(`${fieldLabel(f.key)}: ${e.message || e}`)
        setBusy(false)
        return
      }
    }
    toast(changed.length === 1 ? 'Saved 1 change' : `Saved ${changed.length} changes`)
    setBusy(false)
  }
  return (
    <>
      {groups.map((g) => (
        <section key={g.title} className="glass rounded-xl p-3.5">
          <h2 className="panel-title">{g.title}</h2>
          <div className="mt-1 divide-y">
            {g.fields.map((field) => {
              const meta = FIELD_META[field.key] || { input: 'text' }
              const id = `setting-${field.key}`
              return (
                <div key={field.key} className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center sm:justify-between sm:gap-6">
                  <div className="min-w-0">
                    <label htmlFor={id} className="text-sm font-medium text-foreground">{fieldLabel(field.key)}</label>
                    {fieldDesc(field.key) && <p className="text-sm text-muted-foreground">{fieldDesc(field.key)}</p>}
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    {meta.input === 'toggle' ? (
                      <ToggleGroup id={id} type="single" value={String(draft[field.key])} onValueChange={(v) => v && set(field.key, v === 'true')} variant="outline" size="sm">
                        <ToggleGroupItem value="true" className="px-3">On</ToggleGroupItem>
                        <ToggleGroupItem value="false" className="px-3">Off</ToggleGroupItem>
                      </ToggleGroup>
                    ) : (
                      <Input
                        id={id}
                        type={meta.input === 'number' ? 'number' : 'text'}
                        value={draft[field.key]}
                        onChange={(e) => set(field.key, e.target.value)}
                        className="font-data h-8 w-24 text-right"
                      />
                    )}
                    {field.source === 'override' && (
                      <Button size="icon-sm" variant="ghost" aria-label={`Reset ${fieldLabel(field.key)} to its original value`} onClick={() => onReset(field.key)}>
                        <RotateCcw className="size-3.5" />
                      </Button>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        </section>
      ))}
      <div className="glass sticky bottom-2 z-10 flex flex-wrap items-center gap-3 rounded-xl p-3">
        <Button disabled={!changed.length || busy} onClick={save}>{busy ? 'Saving…' : 'Save changes'}</Button>
        <Button variant="ghost" disabled={!changed.length || busy} onClick={() => setDraft(Object.fromEntries(fields.map((f) => [f.key, f.value])))}>Discard</Button>
        <span className="text-sm text-muted-foreground" role="status">
          {changed.length ? `${changed.length} unsaved ${changed.length === 1 ? 'change' : 'changes'}` : 'These apply straight away. Nothing restarts.'}
        </span>
      </div>
    </>
  )
}

function ModeOption({ value, current, onChoose }) {
  const selected = current === value
  const body = (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      onClick={value === 'execute' || selected ? undefined : () => onChoose(value)}
      className={cn(
        'flex w-full items-start gap-3 rounded-lg border p-3.5 text-left transition-colors',
        selected ? 'border-primary' : 'hover:border-ring/40',
      )}
    >
      <span className={cn('mt-0.5 grid size-4 shrink-0 place-items-center rounded-full border', selected && 'border-primary bg-primary text-primary-foreground')}>
        {selected && <Check className="size-3" aria-hidden />}
      </span>
      <span>
        <span className="block font-semibold text-foreground">{MODES[value].title}</span>
        <span className="mt-0.5 block text-sm text-muted-foreground">{MODES[value].about}</span>
      </span>
    </button>
  )
  if (value === 'execute' && !selected) {
    return (
      <ConfirmAction
        title="Let Digdown apply fixes you approve?"
        description="When you click Approve on an incident, Digdown will change that container, check that it worked, and undo the change if it didn't. Nothing runs without your click unless you turn on auto heal for an app."
        confirmLabel="Allow fixes"
        onConfirm={() => onChoose('execute')}
      >
        {body}
      </ConfirmAction>
    )
  }
  return body
}

// Connections to Docker are made at startup, so these changes need a restart.
async function waitForRestart() {
  await new Promise((r) => setTimeout(r, 1500))
  for (let i = 0; i < 40; i++) {
    const up = await fetch('/api/health').then((r) => r.ok).catch(() => false)
    if (up) return window.location.reload()
    await new Promise((r) => setTimeout(r, 1000))
  }
}

function Apps({ demo }) {
  const [data, setData] = useState(null)
  const [all, setAll] = useState(true)
  const [chosen, setChosen] = useState(() => new Set())
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    let cancelled = false
    fetch('/api/apps').then((r) => (r.ok ? r.json() : null)).then((d) => {
      if (cancelled || !d) return
      setData(d)
      setAll(d.all)
      setChosen(new Set(d.stacks.filter((s) => s.watched).map((s) => s.name)))
    }).catch(() => {})
    return () => { cancelled = true }
  }, [])
  if (!data) return <p className="text-sm text-muted-foreground">Looking for apps…</p>
  const before = new Set(data.stacks.filter((s) => s.watched).map((s) => s.name))
  const dirty = all !== data.all || (!all && (chosen.size !== before.size || [...chosen].some((n) => !before.has(n))))
  const toggle = (name) => {
    const next = new Set(chosen)
    if (next.has(name)) next.delete(name); else next.add(name)
    setChosen(next)
  }
  const save = async () => {
    setBusy(true)
    const r = await fetch('/api/apps', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ all, stacks: [...chosen] }),
    }).catch(() => null)
    if (!r?.ok) {
      toast.error((await r?.json().catch(() => null))?.detail || "That didn't save.")
      setBusy(false)
      return
    }
    toast('Saved. Restarting Digdown…')
    await waitForRestart()
  }
  return (
    <section className="glass rounded-xl p-3.5">
      <h2 className="panel-title">Apps to watch</h2>
      <p className="mt-1 max-w-prose text-sm text-muted-foreground">
        Everything Docker is running on this machine, whether you started it with Compose, Coolify or a
        plain <code className="font-data">docker run</code>. Digdown only reads an app&apos;s details and
        logs when it&apos;s on this list.
      </p>
      {demo && <p className="mt-3 text-sm text-muted-foreground">The demo can&apos;t change what it watches.</p>}
      <fieldset disabled={demo || busy} className="mt-3 flex flex-col gap-2.5">
        <legend className="sr-only">Which apps should Digdown watch?</legend>
        <label className="flex items-start gap-2.5 text-sm">
          <input type="radio" name="watch" checked={all} onChange={() => setAll(true)} className="mt-1 accent-[var(--primary)]" />
          <span><span className="font-semibold text-foreground">Everything</span>
            <span className="block text-muted-foreground">New apps are picked up by themselves as they start.</span></span>
        </label>
        <label className="flex items-start gap-2.5 text-sm">
          <input type="radio" name="watch" checked={!all} onChange={() => setAll(false)} className="mt-1 accent-[var(--primary)]" />
          <span><span className="font-semibold text-foreground">Only the ones I choose</span>
            <span className="block text-muted-foreground">Good for a shared machine, or to keep an app&apos;s logs away from the AI.</span></span>
        </label>
        <ul className="mt-1 divide-y border-t">
          {data.stacks.map((stack) => {
            const on = all || chosen.has(stack.name)
            return (
              <li key={stack.name} className="flex items-center gap-3 py-2.5 text-sm">
                <input
                  type="checkbox"
                  aria-label={`Watch ${stackLabel(stack.name)}`}
                  checked={on}
                  disabled={all || demo || busy}
                  onChange={() => toggle(stack.name)}
                  className="size-4 shrink-0 accent-[var(--primary)]"
                />
                <span className={cn('min-w-0 flex-1 truncate', stack.name !== NO_STACK && 'font-data')}>{stackLabel(stack.name)}</span>
                {stack.auto_heal && (
                  <a href={routeHash({ section: 'heal' })} className="shrink-0 rounded-sm border px-1.5 py-px text-xs text-primary hover:border-primary">
                    Auto heal on
                  </a>
                )}
                <span className="shrink-0 text-muted-foreground">
                  {stack.containers} {stack.containers === 1 ? 'container' : 'containers'}
                </span>
              </li>
            )
          })}
          {data.stacks.length === 0 && <li className="py-3 text-sm text-muted-foreground">Docker has no containers yet.</li>}
        </ul>
      </fieldset>
      {!demo && (
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <Button disabled={!dirty || busy} onClick={save}>{busy ? 'Restarting…' : 'Save and restart'}</Button>
          <span className="text-sm text-muted-foreground">Digdown restarts to apply this. Your containers aren&apos;t touched.</span>
        </div>
      )}
    </section>
  )
}

// Checks that an app's web address answers, and quickly. Saving restarts Digdown,
// as the checks start with it.
function WebsiteChecks({ demo, services = [] }) {
  const [checks, setChecks] = useState(null)
  const [url, setUrl] = useState('')
  const [target, setTarget] = useState('')
  const [busy, setBusy] = useState(false)
  const inContainer = useInContainer()
  useEffect(() => {
    let cancelled = false
    fetch('/api/checks').then((r) => (r.ok ? r.json() : [])).then((d) => { if (!cancelled) setChecks(d) }).catch(() => { if (!cancelled) setChecks([]) })
    return () => { cancelled = true }
  }, [])
  const names = [...new Set(services.map((s) => s.name).filter(Boolean))].sort()
  const save = async (next, message) => {
    setBusy(true)
    const r = await fetch('/api/checks', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(next),
    }).catch(() => null)
    if (!r?.ok) {
      toast.error((await r?.json().catch(() => null))?.detail || "That didn't save.")
      setBusy(false)
      return
    }
    toast(`${message} Restarting Digdown…`)
    await waitForRestart()
  }
  const add = (e) => {
    e.preventDefault()
    save([...checks, { url: url.trim(), target }], 'Check added.')
  }
  return (
    <section className="glass rounded-xl p-3.5">
      <h2 className="panel-title">Website checks</h2>
      <p className="mt-1 max-w-prose text-sm text-muted-foreground">
        Docker can&apos;t tell when an app is running but its site returns errors or is slow. Add the app&apos;s
        address, and Digdown opens it on every check. If it fails, you get an incident, and auto heal restarts
        the container if it&apos;s on for that app.
      </p>
      {demo ? (
        <p className="mt-3 text-sm text-muted-foreground">The demo can&apos;t add checks.</p>
      ) : checks === null ? (
        <p className="mt-3 text-sm text-muted-foreground">Loading…</p>
      ) : (
        <>
          {checks.length > 0 && (
            <ul className="mt-3 divide-y border-y">
              {checks.map((c) => (
                <li key={c.name} className="flex items-center gap-3 py-2.5 text-sm">
                  <span className="font-data min-w-0 flex-1 truncate text-foreground" title={c.url}>{c.url}</span>
                  <span className="font-data shrink-0 text-muted-foreground">{c.target}</span>
                  <Button size="sm" variant="ghost" disabled={busy} aria-label={`Remove the check for ${c.url}`}
                    onClick={() => save(checks.filter((x) => x.name !== c.name), 'Check removed.')}>
                    Remove
                  </Button>
                </li>
              ))}
            </ul>
          )}
          <form onSubmit={add} className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-end">
            <div className="flex min-w-0 flex-1 flex-col gap-1.5">
              <label htmlFor="check-url" className="text-sm font-medium text-foreground">Address</label>
              <Input id="check-url" type="url" required spellCheck={false} autoComplete="off" placeholder="http://localhost:3000" value={url} onChange={(e) => setUrl(e.target.value)} className="font-data" />
            </div>
            <div className="flex flex-col gap-1.5">
              <label htmlFor="check-target" className="text-sm font-medium text-foreground">Container</label>
              <select id="check-target" required value={target} onChange={(e) => setTarget(e.target.value)}
                className="font-data h-9 rounded-md border bg-background px-2 text-sm text-foreground">
                <option value="">Choose…</option>
                {names.map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </div>
            <Button type="submit" disabled={busy || !url.trim() || !target}>Add check</Button>
          </form>
          <p className="mt-2 text-sm text-muted-foreground">
            {inContainer
              ? <>Digdown runs in Docker, so <code className="font-data">localhost</code> means its own container. Use <code className="font-data">http://host.docker.internal:PORT</code> for a port published on this computer.</>
              : 'Use the address you open in your browser, such as http://localhost:3000.'}
            {' '}Adding or removing a check restarts Digdown. Your containers aren&apos;t touched.
          </p>
        </>
      )}
    </section>
  )
}

function useInContainer() {
  const [inside, setInside] = useState(false)
  useEffect(() => {
    let cancelled = false
    fetch('/api/setup').then((r) => (r.ok ? r.json() : null)).then((d) => { if (!cancelled && d) setInside(Boolean(d.in_container)) }).catch(() => {})
    return () => { cancelled = true }
  }, [])
  return inside
}

function StartupField({ field }) {
  const meta = FIELD_META[field.key] || { input: 'text' }
  const [val, setVal] = useState(field.value)
  const [busy, setBusy] = useState(false)
  const id = `startup-${field.key}`
  const dirty = String(val) !== String(field.value)
  const save = async () => {
    setBusy(true)
    const value = meta.input === 'number' ? Number(val) : val
    const r = await fetch('/api/config/file', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key: field.key, value }),
    }).catch(() => null)
    if (!r?.ok) {
      toast.error((await r?.json().catch(() => null))?.detail || "That didn't save.")
      setBusy(false)
      return
    }
    toast('Saved. Restarting Digdown…')
    await waitForRestart()
  }
  return (
    <div className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center sm:justify-between sm:gap-6">
      <div className="min-w-0">
        <label htmlFor={id} className="text-sm font-medium text-foreground">{fieldLabel(field.key)}</label>
        {fieldDesc(field.key) && <p className="text-sm text-muted-foreground">{fieldDesc(field.key)}</p>}
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-2">
        {meta.input === 'toggle' ? (
          <ToggleGroup id={id} type="single" value={String(val)} onValueChange={(v) => v && setVal(v === 'true')} variant="outline" size="sm">
            <ToggleGroupItem value="true" className="px-3">On</ToggleGroupItem>
            <ToggleGroupItem value="false" className="px-3">Off</ToggleGroupItem>
          </ToggleGroup>
        ) : (
          <Input
            id={id}
            type={meta.input === 'number' ? 'number' : 'text'}
            step="any"
            value={val}
            onChange={(e) => setVal(e.target.value)}
            className={cn('font-data h-8 text-right', meta.input === 'number' ? 'w-24' : 'w-40')}
          />
        )}
        <Button size="sm" variant="outline" disabled={!dirty || busy} aria-label={`Save ${fieldLabel(field.key)} and restart`} onClick={save}>
          Save and restart
        </Button>
      </div>
    </div>
  )
}

function Alerts({ demo }) {
  const [saved, setSaved] = useState(false)
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    let cancelled = false
    fetch('/api/notifications')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (d && !cancelled) setSaved(d.set) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [])
  const send = async (path, method, body) => {
    setBusy(true)
    try {
      const r = await fetch(path, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const data = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(data.detail || `That didn't work (${r.status})`)
      return data
    } catch (e) {
      toast.error(String(e.message || e))
      return null
    } finally {
      setBusy(false)
    }
  }
  const save = async (next) => {
    const data = await send('/api/notifications', 'PUT', { url: next })
    if (!data) return
    setSaved(data.set)
    setUrl('')
    toast(data.set ? 'Alerts: saved' : 'Alerts: turned off')
  }
  const test = async () => {
    const data = await send('/api/notifications/test', 'POST', { url })
    if (data) (data.ok ? toast : toast.error)(data.detail)
  }
  return (
    <section className="glass rounded-xl p-3.5">
      <h2 className="panel-title">Alerts</h2>
      <p className="mt-1 max-w-prose text-sm text-muted-foreground">
        Get a message when something needs you or auto heal has fixed something. Paste a Discord or
        Slack webhook address, or an ntfy topic address for alerts on your phone.
      </p>
      {demo ? (
        <p className="mt-3 text-sm text-muted-foreground">The demo doesn't send alerts.</p>
      ) : (
        <>
          <label htmlFor="alert-url" className="mt-3 block text-sm font-medium text-foreground">
            {saved ? 'Replace the alert address' : 'Alert address'}
          </label>
          <div className="mt-1.5 flex flex-col gap-2 sm:flex-row">
            <Input
              id="alert-url"
              type="url"
              autoComplete="off"
              spellCheck={false}
              placeholder="https://ntfy.sh/your-topic"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              className="font-data min-w-0 flex-1"
            />
            <div className="flex shrink-0 gap-2">
              <Button variant="outline" disabled={busy || (!url && !saved)} onClick={test}>Send test</Button>
              <Button disabled={busy || !url} onClick={() => save(url)}>Save</Button>
            </div>
          </div>
          {saved && (
            <p className="mt-2 flex flex-wrap items-center gap-x-2 text-sm text-muted-foreground">
              An alert address is saved.
              <Button size="sm" variant="ghost" disabled={busy} onClick={() => save('')}>Turn off alerts</Button>
            </p>
          )}
        </>
      )}
    </section>
  )
}

export function Settings({ config, onPatch, onReset, mock, status, services, onChangeSetup, tab, onTab }) {
  if (mock || !config) {
    return <p className="text-sm text-muted-foreground">{mock ? "Settings aren't available with sample data." : 'Loading settings…'}</p>
  }
  const groups = groupFields(config.fields)
  const mode = config.fields.find((f) => f.key === 'mode')
  const changeMode = (next) => {
    onPatch('mode', next)
      .then(() => toast(`Now: ${MODES[next].title}`))
      .catch((e) => toast.error(String(e.message || e)))
  }
  const aiOn = status?.ai?.enabled
  const other = groups.find((g) => g.title === 'Other')

  const tabs = [
    { key: 'fixes', label: 'Fixes' },
    { key: 'apps', label: 'Apps' },
    { key: 'alerts', label: 'Alerts' },
    ...(status?.demo ? [] : [{ key: 'ai', label: 'AI' }]),
    { key: 'tuning', label: 'Fine-tuning' },
  ]
  const active = tabs.some((t) => t.key === tab) ? tab : 'fixes'

  return (
    <div className="flex w-full max-w-4xl flex-col gap-3">
      <Tabs value={active} onValueChange={(next) => onTab?.(next)}>
        <TabsList variant="line" aria-label="Settings sections" className="h-auto flex-wrap">
          {tabs.map((t) => <TabsTrigger key={t.key} value={t.key}>{t.label}</TabsTrigger>)}
        </TabsList>
      </Tabs>

      {active === 'fixes' && (
      <>
      <section className="glass rounded-xl p-3.5">
        <h2 className="panel-title">When something fails</h2>
        <div role="radiogroup" aria-label="When something fails" className="mt-3 flex flex-col gap-2.5">
          {FIELD_META.mode.options.map((m) => (
            <ModeOption key={m} value={m} current={mode.value} onChoose={changeMode} />
          ))}
        </div>
      </section>

      <section className="glass flex flex-col gap-3 rounded-xl p-3.5 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <h2 className="panel-title">Auto heal</h2>
          <p className="mt-1 text-sm text-muted-foreground">Let fixes that need no decision from you run by themselves, app by app.</p>
        </div>
        <Button variant="outline" className="shrink-0" asChild>
          <a href={routeHash({ section: 'heal' })}>Open Auto heal</a>
        </Button>
      </section>
      </>
      )}

      {active === 'apps' && <Apps demo={status?.demo} />}
      {active === 'apps' && <WebsiteChecks demo={status?.demo} services={services} />}

      {active === 'alerts' && <Alerts demo={status?.demo} />}

      {active === 'ai' && (
        <section className="glass flex flex-col gap-3 rounded-xl p-3.5 sm:flex-row sm:items-center sm:justify-between">
          <div className="min-w-0">
            <h2 className="panel-title">AI and containers</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              {aiOn ? <>Using <span className="font-data">{status.ai.model}</span> to explain failures.</> : 'No AI model is connected.'}{' '}
              No tracking. Your data goes only to the AI model you choose.
            </p>
          </div>
          <Button variant="outline" className="shrink-0" onClick={onChangeSetup}>
            {aiOn ? 'Change setup' : 'Connect an AI model'}
          </Button>
        </section>
      )}

      {active === 'tuning' && (() => {
        const tuning = groups.filter((g) => !['Mode', 'Auto heal', 'Other'].includes(g.title))
        // Starts again from the saved values whenever they change, such as after a reset.
        const saved = JSON.stringify(tuning.flatMap((g) => g.fields.map((f) => [f.key, f.value])))
        return <TuningForm key={saved} groups={tuning} onSave={onPatch} onReset={onReset} />
      })()}

      {active === 'tuning' && other && (
        <details className="glass rounded-xl p-3.5">
          <summary className="font-semibold text-foreground">Advanced settings</summary>
          {!status?.demo && (
            <div className="mt-2 divide-y">
              {other.fields.filter((f) => f.file_editable).map((f) => <StartupField key={f.key} field={f} />)}
            </div>
          )}
          <h3 className="mt-5 text-sm text-foreground">Only in the settings file</h3>
          <p className="mt-1 text-sm text-muted-foreground">
            Network, passwords and file locations can&apos;t be changed from a browser, for safety. To change
            them, edit <code className="font-data">{status?.config_file || 'digdown.yml'}</code> and restart
            Digdown. Each one is explained in <code className="font-data">docs/configuration.md</code>.
          </p>
          <dl className="mt-3 divide-y">
            {other.fields.filter((f) => status?.demo || !f.file_editable).map((f) => (
              <div key={f.key} className="flex flex-col gap-0.5 py-2 text-sm sm:flex-row sm:items-baseline sm:justify-between sm:gap-6">
                <dt className="font-data text-foreground">{f.key}</dt>
                <dd className="font-data min-w-0 break-words text-muted-foreground sm:text-right">{String(f.value)}</dd>
              </div>
            ))}
          </dl>
        </details>
      )}
    </div>
  )
}
