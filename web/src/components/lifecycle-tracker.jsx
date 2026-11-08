import { lifecyclePhase } from '@/lib/domain'

const TONE = {
  diagnose: 'var(--sev-disk)',
  await: 'var(--sev-unhealthy)',
  apply: 'var(--primary)',
  done: 'var(--live)',
  bad: 'var(--sev-crash)',
  muted: 'var(--muted-foreground)',
}

// Compact 4-node pipeline tracker: Diagnose · Approve · Apply · Done. Nodes up
// to the current index are filled; the current node is lit; a bad outcome
// paints the reached nodes in the revert/crash color. Decorative — the row's
// status verb carries the meaning, so this is aria-hidden.
export function LifecycleTracker({ state, tone = 'diagnose' }) {
  const { index, outcome } = lifecyclePhase(state)
  const lit =
    outcome === 'bad' ? TONE.bad : outcome === 'good' ? TONE.done : TONE[tone] || TONE.diagnose
  const fillDone = outcome === 'bad' ? TONE.bad : TONE.done
  return (
    <span className="flex items-center" aria-hidden>
      {[0, 1, 2, 3].map((i) => (
        <span key={i} className="flex items-center">
          {i > 0 && (
            <span
              className="h-px w-3.5"
              style={{ backgroundColor: i <= index ? fillDone : 'var(--muted-foreground)' }}
            />
          )}
          <span
            className="size-2 rounded-full border-[1.5px]"
            style={
              i < index
                ? { backgroundColor: fillDone, borderColor: fillDone }
                : i === index
                  ? {
                      backgroundColor: lit,
                      borderColor: lit,
                      boxShadow: `0 0 0 3px color-mix(in oklch, ${lit} 22%, transparent)`,
                    }
                  : { borderColor: 'var(--muted-foreground)', backgroundColor: 'transparent' }
            }
          />
        </span>
      ))}
    </span>
  )
}
