import { cn } from "@/lib/format";

export type BadgeTone =
  | "neutral"
  | "accent"
  | "cyan"
  | "ok"
  | "warn"
  | "danger"
  | "info";

const TONES: Record<BadgeTone, string> = {
  neutral: "bg-surface-2 text-muted border-border",
  accent: "bg-accent/15 text-accent border-accent/40",
  cyan: "bg-cyan/10 text-cyan border-cyan/40",
  ok: "bg-ok/10 text-ok border-ok/40",
  warn: "bg-warn/10 text-warn border-warn/40",
  danger: "bg-danger/10 text-danger border-danger/40",
  info: "bg-info/10 text-info border-info/40",
};

const DOT_COLORS: Record<BadgeTone, string> = {
  neutral: "bg-faint",
  accent: "bg-accent",
  cyan: "bg-cyan",
  ok: "bg-ok",
  warn: "bg-warn",
  danger: "bg-danger",
  info: "bg-info",
};

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone;
  /** Render a leading status dot in the tone color. */
  dot?: boolean;
  mono?: boolean;
}

/** Small status pill. Server-safe. */
export function Badge({
  className,
  tone = "neutral",
  dot = false,
  mono = false,
  children,
  ...props
}: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded border px-1.5 py-0.5 text-2xs font-medium leading-none",
        TONES[tone],
        mono && "mono tracking-tight",
        className,
      )}
      {...props}
    >
      {dot && (
        <span
          className={cn(
            "h-1.5 w-1.5 shrink-0 rounded-full",
            DOT_COLORS[tone],
          )}
          aria-hidden
        />
      )}
      {children}
    </span>
  );
}
