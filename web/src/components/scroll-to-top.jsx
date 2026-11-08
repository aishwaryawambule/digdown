import { useEffect, useState } from 'react'
import { ArrowUp } from 'lucide-react'

// A floating "back to top" control for long pages — chiefly the case file's
// reasoning trace / evidence, which can run very long. The page scrolls at the
// document level, so it watches window.scrollY and appears once you've scrolled
// past `threshold` (kept low so it shows on a normally-tall incident page, not
// just a fully-expanded trace), then jumps back to the top.
export function ScrollToTop({ threshold = 200 }) {
  const [show, setShow] = useState(false)

  useEffect(() => {
    const onScroll = () => setShow(window.scrollY > threshold)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [threshold])

  if (!show) return null
  // Bottom-LEFT, lifted clear of the status bar — the bottom-right corner is
  // taken by the toast stack, so this keeps the control in its own visible space.
  return (
    <button
      type="button"
      onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}
      aria-label="Back to top"
      className="fixed bottom-20 left-6 lg:bottom-10 lg:left-[calc(15rem+1.5rem)] z-40 flex items-center gap-1.5 rounded-full border bg-background/90 px-3.5 py-2 text-xs font-medium text-foreground shadow-lg backdrop-blur transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring/60 focus-visible:outline-none"
    >
      <ArrowUp className="size-3.5" /> Top
    </button>
  )
}
