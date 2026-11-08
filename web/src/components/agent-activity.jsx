import { ArrowRight } from 'lucide-react'
import { flattenSteps } from '@/lib/domain'

// Pure render from props — no Date.now(), no Math.random().
export function AgentActivity({ steps }) {
  if (!steps || steps.length === 0) return null

  const items = flattenSteps(steps)

  return (
    <div className="glass rounded-lg p-4">
      {/* Live indicator */}
      <div className="mb-3 flex items-center gap-2">
        <span
          className="size-1.5 rounded-full"
          style={{ backgroundColor: 'var(--live)' }}
        />
        <span className="label-eyebrow text-muted-foreground">What the AI is checking</span>
      </div>

      {items.length === 0 ? (
        <p className="text-sm text-muted-foreground">Starting…</p>
      ) : (
        <ol className="space-y-2">
          {items.map((it, i) =>
            it.kind === 'call' ? (
              <li key={i} className="flex items-start gap-2 text-[13px]">
                <ArrowRight className="mt-0.5 size-3.5 shrink-0 text-primary" />
                <span className="font-data min-w-0 break-words text-foreground">{it.text}</span>
              </li>
            ) : (
              <li key={i} className="pl-5 text-[13px]">
                <span className="font-data block break-words whitespace-pre-wrap text-muted-foreground">
                  {it.text}
                </span>
              </li>
            ),
          )}
        </ol>
      )}
    </div>
  )
}
