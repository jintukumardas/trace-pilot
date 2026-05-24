import { Loader2 } from "lucide-react";

import { cn } from "@/lib/format";

export interface SpinnerProps {
  className?: string;
  /** Tailwind size class fragment; defaults to h-4 w-4. */
  size?: "sm" | "md" | "lg";
  label?: string;
}

const SIZES = {
  sm: "h-3.5 w-3.5",
  md: "h-4 w-4",
  lg: "h-6 w-6",
} as const;

/** Indeterminate loading spinner. Server-safe. */
export function Spinner({ className, size = "md", label }: SpinnerProps) {
  return (
    <span
      role="status"
      aria-live="polite"
      className={cn("inline-flex items-center gap-2 text-muted", className)}
    >
      <Loader2 className={cn("animate-spin text-accent", SIZES[size])} aria-hidden />
      {label ? <span className="text-xs">{label}</span> : null}
      <span className="sr-only">{label ?? "Loading"}</span>
    </span>
  );
}
