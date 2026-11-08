import { AlertTriangle, FlaskConical, Sparkles, WifiOff } from 'lucide-react'

function Banner({ icon: Icon, tone, title, children }) {
  return (
    <div
      role="status"
      className="flex items-start gap-3 border-b px-5 py-3 text-sm sm:px-8 lg:px-12"
      style={{ backgroundColor: `var(--${tone}-soft)` }}
    >
      <Icon className="mt-0.5 size-4 shrink-0" style={{ color: `var(--${tone}-text)` }} aria-hidden />
      <p className="min-w-0 text-foreground">
        <span className="font-semibold">{title}</span>{children ? ' ' : ''}{children}
      </p>
    </div>
  )
}

// Anything that changes how far the screen below can be trusted goes here, so it
// can't read as "all good" while Digdown isn't actually able to check.
export function StatusBanners({ status, conn, mock, onOpenSetup }) {
  if (mock || !status) return null
  return (
    <>
      {status.demo && (
        <Banner icon={FlaskConical} tone="info" title="This is a demo.">
          The containers and AI are simulated, so approving a fix changes nothing real. To watch
          your own containers, run <code className="font-data">digdown start</code>.
        </Banner>
      )}
      {conn === 'down' && (
        <Banner icon={WifiOff} tone="danger" title="Lost connection to Digdown.">
          Retrying now. This page may be out of date until it reconnects.
        </Banner>
      )}
      {!status.docker?.ok && (
        <Banner icon={AlertTriangle} tone="danger" title="Nothing is being watched, because Digdown can't reach Docker.">
          {status.docker?.fix} Digdown will reconnect by itself.
        </Banner>
      )}
      {!status.demo && status.docker?.ok && !status.ai?.enabled && (
        <Banner icon={Sparkles} tone="info" title="AI diagnosis is off.">
          Digdown will tell you what failed, but not why.{' '}
          <button type="button" onClick={onOpenSetup} className="font-semibold underline underline-offset-2">
            Connect an AI model
          </button>
        </Banner>
      )}
    </>
  )
}
