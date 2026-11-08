// The mark: layers narrowing down to the one point that caused the failure.
export function LogoMark({ className = 'size-5' }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={className} aria-hidden>
      <g stroke="currentColor" strokeWidth="3" strokeLinecap="round">
        <path d="M3.5 4.5h17" />
        <path d="M6.5 10h11" />
        <path d="M9.5 15.5h5" />
      </g>
      <circle cx="12" cy="20.75" r="2" fill="currentColor" />
    </svg>
  )
}

export function Logo({ className = '' }) {
  return (
    <span className={`inline-flex items-center gap-2 text-foreground ${className}`}>
      <LogoMark className="size-5 text-primary" />
      <span className="font-data text-[1.1rem] font-medium tracking-tight">digdown</span>
    </span>
  )
}
