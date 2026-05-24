"use client";

import { cn } from "@/lib/format";

export interface TabItem<T extends string = string> {
  value: T;
  label: React.ReactNode;
  /** Optional trailing count/badge. */
  count?: number | string;
  disabled?: boolean;
}

export interface TabsProps<T extends string = string> {
  items: TabItem<T>[];
  value: T;
  onValueChange: (value: T) => void;
  className?: string;
  size?: "sm" | "md";
  "aria-label"?: string;
}

/**
 * Controlled, underline-style tab strip. Purely presentational — the parent
 * owns the active value and renders the panel body itself.
 */
export function Tabs<T extends string = string>({
  items,
  value,
  onValueChange,
  className,
  size = "md",
  "aria-label": ariaLabel,
}: TabsProps<T>) {
  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      className={cn(
        "flex items-center gap-1 border-b border-border",
        className,
      )}
    >
      {items.map((item) => {
        const active = item.value === value;
        return (
          <button
            key={item.value}
            type="button"
            role="tab"
            aria-selected={active}
            disabled={item.disabled}
            onClick={() => onValueChange(item.value)}
            className={cn(
              "relative -mb-px inline-flex items-center gap-1.5 border-b-2 font-medium transition-colors",
              size === "sm" ? "px-2.5 py-1.5 text-xs" : "px-3 py-2 text-sm",
              "disabled:cursor-not-allowed disabled:opacity-40",
              active
                ? "border-accent text-fg"
                : "border-transparent text-muted hover:text-fg",
            )}
          >
            {item.label}
            {item.count !== undefined && (
              <span
                className={cn(
                  "rounded px-1 py-0.5 text-2xs leading-none",
                  active ? "bg-accent/15 text-accent" : "bg-surface-2 text-faint",
                )}
              >
                {item.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
