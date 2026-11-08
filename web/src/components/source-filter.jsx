import { ChevronDown } from 'lucide-react'
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
} from '@/components/ui/dropdown-menu'
import { cn } from '@/lib/utils'

// The source (detecting probe) selector — a single-select glass dropdown.
// `sources` is [{ key, label, count }] and always leads with the synthetic
// { key: 'all', label: 'All sources' }. Picking a source filters the feed to it
// and reveals that source's type chips; counts are scope-aware.
export function SourceFilter({ sources, value, onChange }) {
  const current = sources.find((s) => s.key === value) || sources[0]
  // With only one real choice, a menu offers nothing.
  if (!current || sources.filter((s) => s.key !== 'all').length < 2) return null

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="glass-lite glass-raise inline-flex h-8 items-center gap-2 rounded-md px-3 text-xs focus-visible:ring-2 focus-visible:ring-ring/60 focus-visible:outline-none"
        >
          <span className="text-muted-foreground">Show</span>
          <span className="text-foreground">{current.label}</span>
          {value !== 'all' && (
            <span className="tabular-nums text-muted-foreground">{current.count}</span>
          )}
          <ChevronDown className="size-3.5 text-muted-foreground" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-auto min-w-[13rem]">
        <DropdownMenuRadioGroup value={value} onValueChange={onChange}>
          {sources.map((s) => (
            <DropdownMenuRadioItem key={s.key} value={s.key} className="text-xs">
              <span className={cn(s.key === 'all' && 'text-muted-foreground')}>{s.label}</span>
              <span className="ml-auto tabular-nums text-muted-foreground">{s.count}</span>
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
