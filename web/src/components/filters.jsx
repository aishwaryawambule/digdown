import { Search, X } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { SourceFilter } from '@/components/source-filter'

const SCOPES = [
  { value: 'all', label: 'All' },
  { value: 'active', label: 'Active' },
  { value: 'resolved', label: 'Fixed' },
  { value: 'muted', label: 'Muted' },
]

export function Filters({ query, onQuery, scope, onScope, sources, source, onSource }) {
  return (
    <div className="flex flex-col gap-3">
      {/* Row 1 — the source (probe) selector always leads (shown whenever any
          incidents exist, so it never vanishes), with search opposite. */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        {sources?.length > 0 && (
          <SourceFilter sources={sources} value={source} onChange={onSource} />
        )}
        <div className="relative sm:w-56">
          <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            id="incident-search"
            value={query}
            onChange={(e) => onQuery(e.target.value)}
            placeholder="Filter by name"
            aria-label="Filter incidents by container name" aria-keyshortcuts="/"
            className="font-data bg-card pl-9"
          />
          {query && (
            <button
              type="button"
              onClick={() => onQuery('')}
              aria-label="Clear search"
              className="absolute top-1/2 right-2.5 -translate-y-1/2 text-muted-foreground hover:text-foreground"
            >
              <X className="size-4" />
            </button>
          )}
        </div>
      </div>

      {/* Row 2 — the scope tabs, on their own line. Progressive: they appear
          only after a specific source is chosen. The source dropdown above is
          always present, so you can always pick a source to reveal them. */}
      {source !== 'all' && (
        <ToggleGroup
          type="single"
          value={scope}
          onValueChange={(v) => v && onScope(v)}
          variant="outline"
          className="w-fit bg-card"
        >
          {SCOPES.map((s) => (
            <ToggleGroupItem
              key={s.value}
              value={s.value}
              className="label-eyebrow px-3 data-[state=on]:bg-accent data-[state=on]:text-foreground"
            >
              {s.label}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      )}
    </div>
  )
}
