import { useState } from 'react'
import { AlertCircle, BarChart3, Boxes, LogOut, Menu, Moon, Settings as SettingsIcon, Sun, TriangleAlert, HeartPulse } from 'lucide-react'
import { Sheet, SheetContent, SheetTitle } from '@/components/ui/sheet'
import { ConnectionStatus } from '@/components/connection-status'
import { isActive } from '@/lib/domain'
import { MODES } from '@/lib/setup'
import { isDark, toggleTheme } from '@/lib/theme'
import { cn } from '@/lib/utils'
import { Logo } from '@/components/logo'
import { routeHash } from '@/lib/routes'

const DOCS_URL = 'https://aishwaryawambule.github.io/digdown-website/docs/'

const NAV = [
  { key: 'incidents', label: 'Incidents', icon: TriangleAlert },
  { key: 'heal', label: 'Auto heal', icon: HeartPulse },
  { key: 'fleet', label: 'Containers', icon: Boxes },
  { key: 'insights', label: 'Trends', icon: BarChart3 },
  { key: 'settings', label: 'Settings', icon: SettingsIcon },
]

function aiSummary(status) {
  if (!status) return 'Checking'
  if (status.demo) return 'Scripted for the demo'
  if (!status.ai?.configured) return 'Off'
  return status.ai.model
}

function SidebarBody({ section, onSection, incidents, services, status, conn, mock, onNavigated }) {
  const [dark, setDark] = useState(isDark)
  const active = incidents.filter(isActive).length
  const counts = { incidents: active, fleet: services.length }
  const healing = status?.mode === 'execute' && status?.auto_heal?.stacks?.length > 0
  const mode = healing ? 'Auto heal on' : MODES[status?.mode]?.short || status?.mode
  const go = (key) => {
    onSection(key)
    onNavigated?.()
  }
  const signOut = async () => {
    await fetch('/api/session', { method: 'DELETE' }).catch(() => {})
    window.location.assign('/')
  }

  return (
    <div className="flex h-full min-h-0 flex-col p-2">
      <div className="px-3 pt-2 pb-4">
        <Logo />
      </div>

      <nav aria-label="Primary">
        <ul className="flex flex-col gap-1">
          {NAV.map(({ key, label, icon: Icon }) => {
            const current = section === key
            const count = counts[key]
            return (
              <li key={key}>
                <a
                  href={routeHash({ section: key })}
                  onClick={(e) => {
                    if (e.metaKey || e.ctrlKey || e.shiftKey) return // open in a new tab as usual
                    e.preventDefault()
                    go(key)
                  }}
                  aria-current={current ? 'page' : undefined}
                  className={cn(
                    'flex h-10 w-full items-center gap-3 rounded-md px-3 text-[0.97rem] transition-colors',
                    current ? 'bg-sidebar-accent font-medium text-foreground' : 'text-sidebar-foreground hover:bg-sidebar-accent hover:text-foreground',
                  )}
                >
                  <Icon className="size-[1.15rem] shrink-0" aria-hidden />
                  {label}
                  {count > 0 && (
                    <span
                      aria-label={key === 'incidents' ? `${count} open` : `${count} watched`}
                      className={cn(
                        'font-data ml-auto rounded px-1.5 text-sm tabular-nums',
                        current && key === 'incidents' && 'bg-primary text-primary-foreground',
                      )}
                    >
                      {count}
                    </span>
                  )}
                </a>
              </li>
            )
          })}
        </ul>
      </nav>

      <div className="flex-1" />

      <dl className="flex flex-col gap-2 border-t px-2.5 py-3 text-xs lg:hidden">
        <div className="flex items-center justify-between gap-3">
          <dt className="text-muted-foreground">Updates</dt>
          <dd>{mock ? <span className="text-muted-foreground">Sample data</span> : <ConnectionStatus conn={conn} />}</dd>
        </div>
        <div className="flex items-center justify-between gap-3">
          <dt className="text-muted-foreground">AI</dt>
          <dd className="min-w-0 truncate text-foreground" title={aiSummary(status)}>{aiSummary(status)}</dd>
        </div>
        <div className="flex items-center justify-between gap-3">
          <dt className="text-muted-foreground">Fixes</dt>
          <dd className="truncate text-foreground">
            <a
              href={routeHash({ section: healing ? 'heal' : 'settings' })}
              onClick={(e) => { e.preventDefault(); go(healing ? 'heal' : 'settings') }}
              className="hover:underline"
            >
              {mode || 'Checking'}
            </a>
          </dd>
        </div>
      </dl>

      <div className="flex items-center gap-1 border-t px-1 pt-2">
        <button
          type="button"
          onClick={() => setDark(toggleTheme())}
          aria-label={dark ? 'Use light theme' : 'Use dark theme'}
          className="grid size-10 place-items-center rounded-md text-sidebar-foreground transition-colors hover:bg-sidebar-accent hover:text-foreground lg:size-8"
        >
          {dark ? <Sun className="size-4" aria-hidden /> : <Moon className="size-4" aria-hidden />}
        </button>
        {status?.auth_required && (
          <button
            type="button"
            onClick={signOut}
            aria-label="Sign out"
            className="grid size-10 place-items-center rounded-md text-sidebar-foreground transition-colors hover:bg-sidebar-accent hover:text-foreground lg:size-8"
          >
            <LogOut className="size-4" aria-hidden />
          </button>
        )}
        <span className="ml-auto flex items-center gap-3 px-1.5 text-xs text-muted-foreground lg:hidden">
          <a href={DOCS_URL} target="_blank" rel="noreferrer" className="hover:text-foreground">Docs</a>
          {status?.version && <span className="font-data">v{status.version}</span>}
        </span>
      </div>
    </div>
  )
}

export function Sidebar(props) {
  const [open, setOpen] = useState(false)
  const active = props.incidents.filter(isActive).length
  return (
    <>
      <aside aria-label="Digdown" className="sticky top-0 hidden h-[calc(100svh-var(--statusbar))] w-60 shrink-0 border-r bg-sidebar lg:block">
        <SidebarBody {...props} />
      </aside>

      <div className="sticky top-0 z-30 flex h-12 items-center gap-2 border-b bg-sidebar px-2 lg:hidden">
        <button
          type="button"
          onClick={() => setOpen(true)}
          aria-label="Open menu"
          aria-expanded={open}
          className="grid size-10 place-items-center rounded-md hover:bg-sidebar-accent"
        >
          <Menu className="size-5" aria-hidden />
        </button>
        <Logo />
        {active > 0 && (
          <span className="ml-auto mr-2 flex items-center gap-1.5 text-sm text-sev-unhealthy">
            <AlertCircle className="size-4" aria-hidden /> {active} open
          </span>
        )}
      </div>
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent side="left" className="w-[min(320px,88vw)] gap-0 p-0">
          <SheetTitle className="sr-only">Menu</SheetTitle>
          <SidebarBody {...props} onNavigated={() => setOpen(false)} />
        </SheetContent>
      </Sheet>
    </>
  )
}
