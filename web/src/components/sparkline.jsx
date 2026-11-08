import { sparklinePath } from '@/lib/domain'

// A tiny inline trend line. Renders nothing when there isn't enough data.
export function Sparkline({ points, max = 'auto', width = 48, height = 14,
                            color = 'var(--muted-foreground)', label }) {
  const d = sparklinePath(points, width, height, max)
  if (!d) return null
  return (
    <span className="inline-flex items-center gap-1" title={label}>
      {label && (
        <span className="label-eyebrow text-[0.5625rem] text-muted-foreground/60">{label}</span>
      )}
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`}
           aria-hidden className="overflow-visible">
        <path d={d} fill="none" stroke={color} strokeWidth="1"
              strokeLinejoin="round" strokeLinecap="round" />
      </svg>
    </span>
  )
}
