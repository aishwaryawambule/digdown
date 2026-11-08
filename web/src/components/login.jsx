import { useState } from 'react'
import { Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Logo } from '@/components/logo'

export function Login({ onSignedIn, linkFailed = false }) {
  const [token, setToken] = useState('')
  const [error, setError] = useState(linkFailed ? "That sign-in link didn't work. The token may have changed, so paste the one Digdown printed last." : '')
  const [busy, setBusy] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      const r = await fetch('/api/session', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token }),
      })
      if (r.ok) return onSignedIn()
      setError(r.status === 401 ? "That token isn't right. Check it and try again." : `Sign-in failed (${r.status}).`)
    } catch {
      setError("Can't reach Digdown. Check that it's running.")
    } finally {
      setBusy(false)
    }
    return undefined
  }

  return (
    <main className="mx-auto flex min-h-svh w-full max-w-md flex-col justify-center px-4 py-10">
      <Logo className="mb-6" />
      <h1 className="text-4xl leading-none">Sign in to Digdown</h1>
      <p className="mt-3 text-muted-foreground">
        This copy asks for a token, because Digdown has full control of Docker. Digdown prints a sign-in
        link and the token when it starts. See them with <code className="font-data text-sm">docker logs digdown</code>.
        If you set your own, it&apos;s the <code className="font-data text-sm">auth_token</code> setting.
      </p>
      <form onSubmit={submit} className="mt-6 flex flex-col gap-3">
        <label htmlFor="token" className="label-eyebrow">Token</label>
        <Input
          id="token"
          type="password"
          autoComplete="current-password"
          autoFocus
          value={token}
          onChange={(e) => setToken(e.target.value)}
          aria-invalid={Boolean(error)}
          aria-describedby={error ? 'token-error' : undefined}
          className="font-data h-10"
        />
        {error && <p id="token-error" role="alert" className="text-sm text-sev-oom">{error}</p>}
        <Button type="submit" size="lg" className="h-10" disabled={busy || !token}>
          {busy && <Loader2 className="size-4 animate-spin" />}
          Sign in
        </Button>
      </form>
    </main>
  )
}
