import { ArrowDown, ArrowUp, Minus } from 'lucide-react'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'

const ARROW = { up: ArrowUp, down: ArrowDown, flat: Minus }

// One headline metric: big value + a trend chip vs the previous window. `delta`
// comes from domain.kpiDelta(); `formatMagnitude` formats Math.abs(delta.diff).
// `hint` (optional) attaches an explanatory tooltip to the label.
export function InsightsKpi({ label, value, delta, formatMagnitude = (n) => String(n), hint }) {
  const Arrow = delta?.show ? ARROW[delta.dir] : null
  const labelClass = 'label-eyebrow text-muted-foreground'
  return (
    <div className="glass rounded-xl p-4">
      {hint ? (
        <Tooltip>
          <TooltipTrigger asChild>
            <div
              className={`${labelClass} inline-flex w-fit cursor-help items-center underline decoration-dotted decoration-muted-foreground/40 underline-offset-2`}
            >
              {label}
            </div>
          </TooltipTrigger>
          <TooltipContent side="top" className="max-w-xs">{hint}</TooltipContent>
        </Tooltip>
      ) : (
        <div className={labelClass}>{label}</div>
      )}
      <div className="mt-2 flex items-end justify-between gap-2">
        <span className="font-data text-3xl leading-none tabular-nums text-foreground">{value}</span>
        {delta?.show && Arrow && (
          <span
            className="font-data inline-flex items-center gap-0.5 text-xs tabular-nums"
            style={{ color: delta.tone }}
          >
            <Arrow className="size-3" />
            {formatMagnitude(Math.abs(delta.diff))}
          </span>
        )}
      </div>
    </div>
  )
}
