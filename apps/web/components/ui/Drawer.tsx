"use client";

import { useEffect } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";

import { cn } from "@/lib/format";

export interface DrawerProps {
  open: boolean;
  onClose: () => void;
  title?: React.ReactNode;
  description?: React.ReactNode;
  /** Tailwind width class for the panel. Defaults to a wide evidence panel. */
  widthClassName?: string;
  side?: "right" | "left";
  children?: React.ReactNode;
  footer?: React.ReactNode;
}

/**
 * Right-anchored overlay panel used for the evidence and trace inspectors.
 * Renders into a portal, traps Escape, and locks body scroll while open.
 */
export function Drawer({
  open,
  onClose,
  title,
  description,
  widthClassName = "w-full max-w-xl",
  side = "right",
  children,
  footer,
}: DrawerProps) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [open, onClose]);

  // Portals require the DOM; bail during SSR / before mount.
  if (!open || typeof document === "undefined") return null;

  return createPortal(
    <div className="fixed inset-0 z-50 flex" role="dialog" aria-modal="true">
      <button
        type="button"
        aria-label="Close panel"
        onClick={onClose}
        className="absolute inset-0 animate-fade-in bg-black/60 backdrop-blur-sm"
      />
      <div
        className={cn(
          "relative ml-auto flex h-full flex-col border-l border-border bg-surface shadow-drawer",
          "animate-slide-in-right",
          side === "left" && "mr-auto ml-0 border-l-0 border-r",
          widthClassName,
        )}
      >
        <header className="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
          <div className="min-w-0">
            {title ? (
              <h2 className="truncate text-sm font-semibold text-fg">{title}</h2>
            ) : null}
            {description ? (
              <p className="mt-0.5 truncate text-xs text-muted">{description}</p>
            ) : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded p-1 text-muted transition-colors hover:bg-surface-2 hover:text-fg"
          >
            <X className="h-4 w-4" />
          </button>
        </header>
        <div className="flex-1 overflow-y-auto">{children}</div>
        {footer ? (
          <footer className="border-t border-border px-4 py-3">{footer}</footer>
        ) : null}
      </div>
    </div>,
    document.body,
  );
}
