import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, Check, Loader2, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'
import { PROVIDERS, SETUP_CHOICES, setupChoice, setupPayload, previewLines, canTestAi, formFromCurrent, preferredModel } from '@/lib/setup'
import { Logo } from '@/components/logo'

const STEPS = [
  { key: 'watch', label: 'Choose containers' },
  { key: 'ai', label: 'Connect AI' },
  { key: 'fixes', label: 'Decide on fixes' },
]

function Choice({ name, checked, onChange, title, children }) {
  return (
    <label
      className={cn(
        'flex cursor-pointer gap-3 rounded-lg border bg-card p-3.5 transition-colors',
        'focus-within:outline-2 focus-within:outline-offset-3 focus-within:outline-ring',
        checked ? 'border-primary' : 'hover:border-ring/40',
      )}
    >
      <input type="radio" name={name} checked={checked} onChange={onChange} className="mt-1 size-4 shrink-0 accent-[var(--primary)]" />
      <span className="min-w-0">
        <span className="block font-semibold text-foreground">{title}</span>
        {children && <span className="mt-0.5 block text-sm text-muted-foreground">{children}</span>}
      </span>
    </label>
  )
}

function Field({ id, label, hint, children }) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="label-eyebrow">{label}</label>
      {children}
      {hint && <p className="text-sm text-muted-foreground">{hint}</p>}
    </div>
  )
}

function WatchStep({ options, form, set, reload }) {
  const docker = options?.docker
  if (docker?.status === 'fail') {
    return (
      <div className="rounded-lg border bg-card p-4" role="alert">
        <p className="flex items-center gap-2 font-semibold text-sev-oom">
          <AlertTriangle className="size-4 shrink-0" /> Digdown can&apos;t reach Docker
        </p>
        <p className="mt-2 text-sm text-foreground">{docker.detail} {docker.fix}</p>
        {docker.technical && (
          <details className="mt-2 text-sm text-muted-foreground">
            <summary>Technical details</summary>
            <p className="font-data mt-1 break-words text-xs">{docker.technical}</p>
          </details>
        )}
        <Button variant="outline" className="mt-3" onClick={reload}>
          <RefreshCw className="size-4" /> Check again
        </Button>
        <p className="mt-4 text-sm text-muted-foreground">
          You can continue and Digdown will connect by itself once Docker is running.
          To see how it works first, run <code className="font-data">digdown demo</code>.
        </p>
      </div>
    )
  }
  const stacks = (options?.stacks || []).filter((s) => s.name)
  const toggle = (name) =>
    set({ stacks: form.stacks.includes(name) ? form.stacks.filter((n) => n !== name) : [...form.stacks, name] })
  return (
    <fieldset className="flex flex-col gap-2.5">
      <legend className="sr-only">Which containers should Digdown watch?</legend>
      <Choice name="scope" checked={form.scope === 'all'} onChange={() => set({ scope: 'all' })} title="Everything on this computer">
        {docker?.detail}
      </Choice>
      {stacks.length > 0 && (
        <Choice name="scope" checked={form.scope === 'some'} onChange={() => set({ scope: 'some' })} title="Only the stacks I pick">
          Good for a shared machine.
        </Choice>
      )}
      {form.scope === 'some' && (
        <div className="flex flex-col gap-1 pl-7">
          {stacks.map((s) => (
            <label key={s.name} className="flex items-center gap-2.5 py-1 text-sm">
              <input
                type="checkbox"
                checked={form.stacks.includes(s.name)}
                onChange={() => toggle(s.name)}
                className="size-4 shrink-0 accent-[var(--primary)]"
              />
              <span className="font-data">{s.name}</span>
              <span className="text-muted-foreground">{s.containers} {s.containers === 1 ? 'container' : 'containers'}</span>
            </label>
          ))}
        </div>
      )}
    </fieldset>
  )
}

function AiStep({ options, form, set, test, onTest }) {
  const provider = PROVIDERS[form.provider]
  const ollamaModels = options?.ollama?.models || []
  return (
    <div className="flex flex-col gap-5">
      <fieldset className="flex flex-col gap-2.5">
        <legend className="sr-only">Which AI should explain failures?</legend>
        {Object.entries(PROVIDERS).map(([key, p]) => (
          <Choice
            key={key}
            name="provider"
            checked={form.provider === key}
            onChange={() => set({
              provider: key,
              baseUrl: key === 'ollama' ? options?.ollama?.base_url || p.baseUrl : p.baseUrl,
              model: key === 'ollama' ? preferredModel(ollamaModels) || p.model : p.model,
              apiKey: '',
              hasSavedKey: false,
            })}
            title={p.title}
          >
            {p.about}
          </Choice>
        ))}
      </fieldset>

      {provider.needsModel && (
        <div className="flex flex-col gap-4 border-t pt-5">
          {provider.showUrl && (
            <Field id="ai-url" label="Address" hint={provider.urlHint}>
              <Input id="ai-url" value={form.baseUrl} onChange={(e) => set({ baseUrl: e.target.value })} className="font-data h-10" />
            </Field>
          )}
          {provider.needsKey && (
            <Field id="ai-key" label="API key" hint={form.hasSavedKey ? 'Leave blank to keep the saved key.' : 'Saved only in your settings file, readable only by your user.'}>
              <Input id="ai-key" type="password" autoComplete="off" value={form.apiKey} onChange={(e) => set({ apiKey: e.target.value })} className="font-data h-10" />
            </Field>
          )}
          <Field
            id="ai-model"
            label="Model"
            hint={form.provider === 'ollama' && ollamaModels.length === 0
              ? 'No Ollama models found. Install one with: ollama pull qwen3:14b'
              : 'It must support tool calling. Testing checks this.'}
          >
            <Input id="ai-model" list="ai-models" value={form.model} onChange={(e) => set({ model: e.target.value })} className="font-data h-10" />
            {form.provider === 'ollama' && (
              <datalist id="ai-models">
                {ollamaModels.map((m) => <option key={m} value={m} />)}
              </datalist>
            )}
          </Field>
          <div className="flex flex-wrap items-center gap-3">
            <Button variant="outline" onClick={onTest} disabled={test.busy || !canTestAi(form)}>
              {test.busy && <Loader2 className="size-4 animate-spin" />}
              {test.busy ? 'Testing. A large model can take a few minutes to load' : 'Test connection'}
            </Button>
          </div>
          {test.result && (
            <div role="status" className="text-sm">
              <p className={cn('flex items-start gap-2', test.result.status === 'ok' ? 'text-live' : 'text-sev-oom')}>
                {test.result.status === 'ok' ? <Check className="mt-0.5 size-4 shrink-0" /> : <AlertTriangle className="mt-0.5 size-4 shrink-0" />}
                {test.result.detail}
              </p>
              {test.result.fix && <p className="mt-1 pl-6 text-muted-foreground">{test.result.fix}</p>}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function FixesStep({ form, set }) {
  const available = form.provider === 'none' ? ['observe'] : SETUP_CHOICES
  return (
    <fieldset className="flex flex-col gap-2.5">
      <legend className="sr-only">What may Digdown do about a failure?</legend>
      {available.map((key) => (
        <Choice key={key} name="mode" checked={form.mode === key} onChange={() => set({ mode: key })} title={setupChoice(key).title}>
          {setupChoice(key).about}
        </Choice>
      ))}
      {form.mode === 'auto' && (
        <p className="text-sm text-muted-foreground">
          The AI can get a cause wrong, and nobody checks before a fix runs. You can turn auto heal off for any
          app later, on the Auto heal page.
        </p>
      )}
      {form.provider === 'none' && (
        <p className="text-sm text-muted-foreground">Fixes need an AI model. You can add one later in Settings.</p>
      )}
    </fieldset>
  )
}

const HEADINGS = {
  watch: ['Choose what to watch', 'Digdown tells you when a container crashes, runs out of memory, or stops responding.'],
  ai: ['Connect an AI to explain failures', 'It reads logs and container state to work out why something broke. It can look, not change anything.'],
  fixes: ['Decide what happens next', 'You can change this any time in Settings.'],
}

// What Digdown will show, given the choices so far. It changes with every pick,
// so the consequence of each answer is visible before it's saved.
function Preview({ form }) {
  const lines = previewLines(form)
  return (
    <section aria-label="What you'll see" className="flex flex-col justify-center bg-card p-6 lg:border-l lg:p-12">
      <p className="label-eyebrow text-muted-foreground">When a container fails, you&apos;ll see</p>
      <div className="glass mt-4 rounded-lg p-5">
        <p className="font-data text-lg text-foreground">shop-cache</p>
        <p className="mt-0.5 text-sm text-sev-oom">Out of memory</p>
        <p className="mt-4 text-[15px] leading-relaxed text-foreground">{lines.finding}</p>
        {lines.fix && (
          <div className="mt-4 rounded-md border bg-background p-3 text-sm text-foreground">{lines.fix}</div>
        )}
        {lines.action ? (
          <div className="mt-4 flex gap-2.5" aria-hidden>
            <span className="inline-flex h-10 flex-1 items-center justify-center rounded-md bg-primary px-4 text-sm font-semibold text-primary-foreground">{lines.action}</span>
            <span className="inline-flex h-10 items-center justify-center rounded-md border px-4 text-sm font-semibold text-foreground">Reject</span>
          </div>
        ) : null}
        <p className="mt-3 text-sm text-muted-foreground">{lines.note}</p>
      </div>
      <p className="mt-4 text-sm text-muted-foreground">{lines.privacy}</p>
    </section>
  )
}

export function Setup({ onCancel }) {
  const [options, setOptions] = useState(null)
  const [step, setStep] = useState(0)
  const [form, setFormState] = useState(() => ({
    scope: 'all', stacks: [], provider: 'ollama', baseUrl: PROVIDERS.ollama.baseUrl,
    model: PROVIDERS.ollama.model, apiKey: '', mode: 'observe', hasSavedKey: false,
  }))
  const [test, setTest] = useState({ busy: false, result: null })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const heading = useRef(null)

  const set = useCallback((patch) => {
    setFormState((f) => {
      const next = { ...f, ...patch }
      if (next.provider === 'none') next.mode = 'observe'
      return next
    })
    if ('provider' in patch || 'baseUrl' in patch || 'model' in patch || 'apiKey' in patch) {
      setTest({ busy: false, result: null })
    }
  }, [])

  const reload = useCallback(() => {
    fetch('/api/setup')
      .then((r) => r.json())
      .then((o) => {
        setOptions(o)
        // Changing an existing setup starts from what's saved. First run starts
        // from whatever is already installed here.
        setFormState((f) => (onCancel
          ? { ...f, ...formFromCurrent(o.current) }
          : { ...f, baseUrl: o.ollama?.base_url || f.baseUrl, model: preferredModel(o.ollama?.models) || f.model }))
      })
      .catch(() => setError("Can't reach Digdown. Check that it's still running."))
  }, [onCancel])

  useEffect(() => {
    const t = setTimeout(reload, 0)
    return () => clearTimeout(t)
  }, [reload])

  useEffect(() => {
    heading.current?.focus()
  }, [step])

  const runTest = async () => {
    setTest({ busy: true, result: null })
    try {
      const r = await fetch('/api/setup/test-ai', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(setupPayload(form)),
      })
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `Test failed (${r.status})`)
      setTest({ busy: false, result: await r.json() })
    } catch (e) {
      setTest({ busy: false, result: { status: 'fail', detail: String(e.message || e) } })
    }
  }

  const finish = async () => {
    setSaving(true)
    setError('')
    try {
      const r = await fetch('/api/setup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(setupPayload(form)),
      })
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `Saving failed (${r.status})`)
      // Digdown restarts itself to apply this. Wait for it to come back.
      await new Promise((done) => setTimeout(done, 800))
      for (let i = 0; i < 40; i += 1) {
        const up = await fetch('/api/status').then((x) => x.ok).catch(() => false)
        if (up) return window.location.assign('/')
        await new Promise((done) => setTimeout(done, 500))
      }
      throw new Error('Digdown saved your settings but hasn\'t restarted. Check its output.')
    } catch (e) {
      setError(String(e.message || e))
      setSaving(false)
    }
    return undefined
  }

  const current = STEPS[step].key
  const [title, lede] = HEADINGS[current]
  const last = step === STEPS.length - 1
  const blocked = current === 'watch' && form.scope === 'some' && form.stacks.length === 0

  return (
    <div className="flex min-h-svh flex-col">
      <header className="flex flex-wrap items-center gap-x-8 gap-y-2 border-b bg-card px-5 py-3 lg:px-12">
        <Logo />
        <ol aria-label="Setup progress" className="flex flex-wrap items-center gap-x-5 gap-y-1 text-sm">
          {STEPS.map((s, i) => (
            <li key={s.key} aria-current={i === step ? 'step' : undefined} className={cn('flex items-center gap-2', i === step ? 'font-semibold text-foreground' : 'text-muted-foreground', i !== step && 'max-sm:hidden')}>
              <span className={cn('grid size-5 place-items-center rounded-full border text-xs', i === step && 'border-primary bg-primary text-primary-foreground', i < step && 'border-foreground bg-foreground text-background')}>
                {i < step ? <Check className="size-3" aria-label="Done" /> : i + 1}
              </span>
              {s.label}
            </li>
          ))}
        </ol>
      </header>

      <main className="grid flex-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="flex flex-col px-5 py-8 lg:px-12 lg:py-12">
          <div className="w-full max-w-xl">
            <h1 ref={heading} tabIndex={-1} className="text-3xl leading-tight outline-none sm:text-4xl">{title}</h1>
            <p className="mt-3 text-muted-foreground">{lede}</p>
            <div className="mt-7">
              {!options && !error ? (
                <p className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
                  <Loader2 className="size-4 animate-spin" /> Looking for Docker and AI models
                </p>
              ) : current === 'watch' ? (
                <WatchStep options={options} form={form} set={set} reload={reload} />
              ) : current === 'ai' ? (
                <AiStep options={options} form={form} set={set} test={test} onTest={runTest} />
              ) : (
                <FixesStep form={form} set={set} />
              )}
            </div>
            {error && <p role="alert" className="mt-4 text-sm text-sev-oom">{error}</p>}
            <div className="mt-8 flex items-center gap-2.5 border-t pt-5">
              {step > 0 ? (
                <Button variant="outline" size="lg" className="h-10" disabled={saving} onClick={() => setStep(step - 1)}>Back</Button>
              ) : onCancel ? (
                <Button variant="outline" size="lg" className="h-10" onClick={onCancel}>Cancel</Button>
              ) : null}
              {last ? (
                <Button size="lg" className="ml-auto h-10" disabled={saving} onClick={finish}>
                  {saving && <Loader2 className="size-4 animate-spin" />}
                  {saving ? 'Starting' : 'Start watching'}
                </Button>
              ) : (
                <Button size="lg" className="ml-auto h-10" disabled={blocked || !options} onClick={() => setStep(step + 1)}>
                  Continue
                </Button>
              )}
            </div>
          </div>
        </div>
        <Preview form={form} />
      </main>
    </div>
  )
}
