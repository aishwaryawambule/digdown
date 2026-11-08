import { useSyncExternalStore } from "react";
import { Toaster as Sonner } from "sonner";
import { isDark } from "@/lib/theme";
import { CircleCheckIcon, InfoIcon, TriangleAlertIcon, OctagonXIcon, Loader2Icon } from "lucide-react"

const subscribe = (notify) => {
  window.addEventListener("digdown-theme", notify)
  return () => window.removeEventListener("digdown-theme", notify)
}

// With the sidebar showing, notices sit in its empty lower part, so they never cover
// what's on the page. Without it, they sit under the top bar.
const wide = "(min-width: 1024px)"
const subscribeWidth = (notify) => {
  const query = window.matchMedia(wide)
  query.addEventListener("change", notify)
  return () => query.removeEventListener("change", notify)
}
const isWide = () => window.matchMedia(wide).matches

const Toaster = ({
  ...props
}) => {
  const dark = useSyncExternalStore(subscribe, isDark)
  const sidebar = useSyncExternalStore(subscribeWidth, isWide)
  const placement = sidebar
    ? { position: "bottom-left", offset: { bottom: "4.75rem", left: "0.5rem" }, width: "14rem" }
    : { position: "top-center", offset: { top: "3.5rem" }, width: "min(356px, calc(100vw - 2rem))" }
  return (
    <Sonner
      theme={dark ? "dark" : "light"}
      position={placement.position}
      offset={placement.offset}
      mobileOffset={placement.offset}
      className="toaster group"
      icons={{
        success: (
          <CircleCheckIcon className="size-4" />
        ),
        info: (
          <InfoIcon className="size-4" />
        ),
        warning: (
          <TriangleAlertIcon className="size-4" />
        ),
        error: (
          <OctagonXIcon className="size-4" />
        ),
        loading: (
          <Loader2Icon className="size-4 animate-spin" />
        ),
      }}
      style={
        {
          "--normal-bg": "var(--popover)",
          "--normal-text": "var(--popover-foreground)",
          "--normal-border": "var(--border)",
          "--border-radius": "var(--radius)",
          "--width": placement.width
        }
      }
      {...props} />
  );
}

export { Toaster }
