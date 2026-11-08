import { SearchX } from 'lucide-react'
import { emptyMessage } from '@/lib/domain'

export function AllClear({ status, services }) {
  const { title, body } = emptyMessage(status, services)
  return (
    <div className="flex flex-col items-center justify-center px-6 py-16 text-center">
      <h2 className="text-xl text-foreground">{title}</h2>
      <p className="mt-2 max-w-sm text-sm leading-relaxed text-muted-foreground">{body}</p>
    </div>
  )
}

export function NoMatch({ onClear }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-24 text-center">
      <span className="mb-4 grid size-12 place-items-center rounded-full border bg-muted/40 text-muted-foreground">
        <SearchX className="size-5" />
      </span>
      <h2 className="text-lg text-foreground">No incidents match</h2>
      <p className="mt-1.5 text-sm text-muted-foreground">
        Try removing a filter.
      </p>
      <button
        type="button"
        onClick={onClear}
        className="mt-4 text-sm text-primary underline-offset-4 hover:underline"
      >
        Clear filters
      </button>
    </div>
  )
}
