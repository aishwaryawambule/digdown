import { Kbd } from '@/components/keyboard'
import { ConnectionStatus } from '@/components/connection-status'
import { MODES } from '@/lib/setup'
import { routeHash } from '@/lib/routes'
import { autoHealRunning } from '@/lib/domain'

const DOCS_URL = 'https://aishwaryawambule.github.io/digdown-website/docs/'

function aiLabel(status) {
  if (!status) return 'Checking'
  if (status.demo) return 'Scripted for the demo'
  return status.ai?.configured ? status.ai.model : 'Off'
}

function Item({ label, children }) {
  return (
    <span className="flex h-full items-center gap-1.5 border-r px-3 whitespace-nowrap">
      <span className="text-muted-foreground">{label}</span>
      <span className="text-foreground">{children}</span>
    </span>
  )
}

// Always on screen, so you can tell at a glance whether Digdown can be trusted right
// now: is it connected, which AI is it using, and how far may it go.
export function StatusBar({ status, conn, mock, hints, onShowHelp }) {
  const healing = autoHealRunning(status)
  const dockerOk = status?.docker?.ok
  return (
    <footer className="sticky bottom-0 z-30 hidden h-[var(--statusbar)] items-center border-t bg-sidebar text-xs lg:flex">
      <Item label="Docker">
        <span style={{ color: !status ? undefined : dockerOk ? 'var(--live)' : 'var(--destructive)' }}>
          {!status ? 'Checking' : dockerOk ? 'Connected' : "Can't reach it"}
        </span>
      </Item>
      <span className="flex h-full items-center border-r px-3">
        {mock ? <span className="text-muted-foreground">Sample data</span> : <ConnectionStatus conn={conn} />}
      </span>
      <Item label="AI">{aiLabel(status)}</Item>
      <Item label="Fixes">
        <a href={routeHash({ section: healing ? 'heal' : 'settings' })} className="hover:underline">
          {healing ? 'Auto heal on' : MODES[status?.mode]?.short || 'Checking'}
        </a>
      </Item>
      <span className="ml-auto flex h-full items-center gap-3 px-3 text-muted-foreground">
        {hints?.map((hint) => (
          <span key={hint.label} className="hidden items-center gap-1 xl:flex">
            {hint.keys.map((k) => <Kbd key={k}>{k}</Kbd>)}
            {hint.label}
          </span>
        ))}
        <button type="button" onClick={onShowHelp} className="hover:text-foreground">Shortcuts</button>
        <a href={DOCS_URL} target="_blank" rel="noreferrer" className="hover:text-foreground">Docs</a>
        {status?.version && <span className="font-data">v{status.version}</span>}
      </span>
    </footer>
  )
}
