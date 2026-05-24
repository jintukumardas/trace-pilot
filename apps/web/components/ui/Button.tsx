"use client";

import { forwardRef } from "react";
import { Loader2 } from "lucide-react";

import { cn } from "@/lib/format";

export type ButtonVariant =
  | "primary"
  | "secondary"
  | "ghost"
  | "danger"
  | "outline";
export type ButtonSize = "sm" | "md" | "icon";

const VARIANTS: Record<ButtonVariant, string> = {
  primary:
    "bg-accent text-accent-fg hover:bg-accent/90 active:bg-accent/80 border border-accent/60",
  secondary:
    "bg-surface-2 text-fg hover:bg-elevated active:bg-elevated border border-border-strong",
  ghost:
    "bg-transparent text-muted hover:text-fg hover:bg-surface-2 border border-transparent",
  danger:
    "bg-danger/15 text-danger hover:bg-danger/25 border border-danger/40",
  outline:
    "bg-transparent text-fg hover:bg-surface-2 border border-border-strong",
};

const SIZES: Record<ButtonSize, string> = {
  sm: "h-7 px-2.5 text-xs gap-1.5",
  md: "h-9 px-3.5 text-sm gap-2",
  icon: "h-8 w-8 p-0 justify-center",
};

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  function Button(
    {
      className,
      variant = "secondary",
      size = "md",
      loading = false,
      disabled,
      children,
      ...props
    },
    ref,
  ) {
    return (
      <button
        ref={ref}
        disabled={disabled || loading}
        className={cn(
          "inline-flex select-none items-center rounded-md font-medium transition-colors",
          "focus-visible:ring-2 focus-visible:ring-accent/70 focus-visible:ring-offset-2 focus-visible:ring-offset-bg",
          "disabled:pointer-events-none disabled:opacity-50",
          VARIANTS[variant],
          SIZES[size],
          className,
        )}
        {...props}
      >
        {loading && (
          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" aria-hidden />
        )}
        {children}
      </button>
    );
  },
);
