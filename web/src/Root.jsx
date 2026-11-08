import { useCallback, useEffect, useState } from 'react'
import { Loader2 } from 'lucide-react'
import App from '@/App'
import { Login } from '@/components/login'
import { Setup } from '@/components/setup'
import { Button } from '@/components/ui/button'
import { tokenFromHash } from '@/lib/routes'

const MOCK = new URLSearchParams(window.location.search).has('mock')

// A sign-in link carries the token after the #. It's taken out of the address bar
// straight away, so it isn't left in history or shown on screen.
const LINK_TOKEN = tokenFromHash(window.location.hash)
if (LINK_TOKEN) window.history.replaceState(null, '', window.location.pathname + window.location.search)

async function signInWith(token) {
  const r = await fetch('/api/session', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token }),
  })
  return r.ok
}

// Decides what the first screen is: sign in, first-run setup, or the dashboard.
export default function Root() {
  const [screen, setScreen] = useState(MOCK ? 'app' : 'loading')
  const [linkFailed, setLinkFailed] = useState(false)

  const load = useCallback(async () => {
    try {
      const session = await (await fetch('/api/session')).json()
      if (session.auth_required && !session.authenticated) {
        if (!LINK_TOKEN || !(await signInWith(LINK_TOKEN))) {
          setLinkFailed(Boolean(LINK_TOKEN))
          return setScreen('login')
        }
      }
      const r = await fetch('/api/status')
      if (r.status === 401) return setScreen('login')
      const status = await r.json()
      setScreen(status.first_run && !status.demo ? 'setup' : 'app')
    } catch {
      setScreen('unreachable')
    }
  }, [])

  useEffect(() => {
    if (MOCK) return undefined
    const start = setTimeout(load, 0)
    return () => clearTimeout(start)
  }, [load])

  if (screen === 'loading') {
    return (
      <div className="grid min-h-svh place-items-center text-muted-foreground" role="status">
        <Loader2 className="size-5 animate-spin" aria-label="Loading Digdown" />
      </div>
    )
  }
  if (screen === 'login') return <Login onSignedIn={load} linkFailed={linkFailed} />
  if (screen === 'setup') return <Setup />
  if (screen === 'unreachable') {
    return (
      <main className="mx-auto flex min-h-svh max-w-md flex-col justify-center gap-4 px-4">
        <h1 className="text-3xl">Can&apos;t reach Digdown</h1>
        <p className="text-muted-foreground">
          The dashboard is open, but Digdown itself isn&apos;t answering. Check that it&apos;s
          still running, then try again.
        </p>
        <Button className="w-fit" onClick={load}>Try again</Button>
      </main>
    )
  }
  return <App />
}
