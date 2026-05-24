"use client";

import {
  Bug,
  Compass,
  GitPullRequestArrow,
  MessageCircleQuestion,
  Wrench,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/format";
import type { ChatMode } from "@/lib/types";

interface ModeMeta {
  value: ChatMode;
  label: string;
  hint: string;
  icon: LucideIcon;
}

export const CHAT_MODES: ModeMeta[] = [
  {
    value: "ask",
    label: "Ask",
    hint: "Grounded Q&A over your code",
    icon: MessageCircleQuestion,
  },
  {
    value: "onboard",
    label: "Onboard",
    hint: "Get oriented in an unfamiliar repo",
    icon: Compass,
  },
  {
    value: "debug",
    label: "Debug",
    hint: "Investigate a bug or stack trace",
    icon: Bug,
  },
  {
    value: "change_review",
    label: "Review",
    hint: "Assess the impact of a diff",
    icon: GitPullRequestArrow,
  },
  {
    value: "fix_plan",
    label: "Fix plan",
    hint: "Draft a concrete remediation plan",
    icon: Wrench,
  },
];

export interface ModeSelectorProps {
  value: ChatMode;
  onChange: (mode: ChatMode) => void;
  disabled?: boolean;
  className?: string;
}

/**
 * Segmented control for the investigation mode. Each mode reshapes the agent's
 * synthesizer prompt server-side; the active mode also drives which endpoint
 * the ChatPanel calls (chat vs. investigate/debug vs. review/diff).
 */
export function ModeSelector({
  value,
  onChange,
  disabled = false,
  className,
}: ModeSelectorProps) {
  const active = CHAT_MODES.find((m) => m.value === value) ?? CHAT_MODES[0]!;

  return (
    <div className={cn("space-y-1.5", className)}>
      <div
        role="radiogroup"
        aria-label="Investigation mode"
        className="inline-flex flex-wrap gap-1 rounded-md border border-border bg-surface-2 p-1"
      >
        {CHAT_MODES.map((mode) => {
          const Icon = mode.icon;
          const selected = mode.value === value;
          return (
            <button
              key={mode.value}
              type="button"
              role="radio"
              aria-checked={selected}
              disabled={disabled}
              title={mode.hint}
              onClick={() => onChange(mode.value)}
              className={cn(
                "inline-flex items-center gap-1.5 rounded px-2.5 py-1.5 text-xs font-medium transition-colors",
                "disabled:cursor-not-allowed disabled:opacity-50",
                selected
                  ? "bg-accent/15 text-accent shadow-panel"
                  : "text-muted hover:bg-surface hover:text-fg",
              )}
            >
              <Icon className="h-3.5 w-3.5" />
              {mode.label}
            </button>
          );
        })}
      </div>
      <p className="text-2xs text-faint">{active.hint}</p>
    </div>
  );
}
