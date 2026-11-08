import { X } from 'lucide-react'

// A keycap. Mono, so it reads as something you press, not something you read.
export function Kbd({ children }) {
  return (
    <kbd className="font-data inline-flex h-5 min-w-[1.25rem] items-center justify-center rounded border border-border bg-muted/60 px-1.5 text-[11px] leading-none text-muted-foreground">
      {children}
    </kbd>
  )
}

const GROUPS = [
  {
    title: 'Go to',
    keys: [
      { k: ['1'], label: 'Incidents' },
      { k: ['2'], label: 'Auto heal' },
      { k: ['3'], label: 'Containers' },
      { k: ['4'], label: 'Trends' },
      { k: ['5'], label: 'Settings' },
    ],
  },
  {
    title: 'Move',
    keys: [
      { k: ['j', '↓'], label: 'Next incident' },
      { k: ['k', '↑'], label: 'Previous incident' },
      { k: ['enter'], label: 'Open the focused incident' },
      { k: ['esc'], label: 'Close' },
    ],
  },
  {
    title: 'While an incident is open',
    keys: [
      { k: ['→'], label: 'Next incident' },
      { k: ['←'], label: 'Previous incident' },
      { k: ['a'], label: 'Approve the fix' },
      { k: ['r'], label: 'Reject the fix' },
    ],
  },
  {
    title: 'Find',
    keys: [
      { k: ['/'], label: 'Filter by name' },
      { k: ['?'], label: 'Toggle this help' },
    ],
  },
]

export function ShortcutsLegend({ open, onClose }) {
  if (!open) return null
  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4 motion-safe:animate-in motion-safe:fade-in"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="Keyboard shortcuts"
    >
      <div
        className="glass w-full max-w-lg rounded-xl p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h2 className="text-lg text-foreground">Keyboard shortcuts</h2>
          <button
            onClick={onClose}
            aria-label="Close"
            className="text-muted-foreground hover:text-foreground"
          >
            <X className="size-4" />
          </button>
        </div>
        <div className="mt-5 grid gap-6 sm:grid-cols-2">
          {GROUPS.map((g) => (
            <section key={g.title}>
              <div className="label-eyebrow mb-2 text-muted-foreground">{g.title}</div>
              <ul className="space-y-2">
                {g.keys.map((row) => (
                  <li key={row.label} className="flex items-center justify-between gap-3">
                    <span className="text-sm text-foreground">{row.label}</span>
                    <span className="flex shrink-0 gap-1">
                      {row.k.map((kk) => (
                        <Kbd key={kk}>{kk}</Kbd>
                      ))}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      </div>
    </div>
  )
}
