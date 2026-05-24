/**
 * Small presentation helpers shared across the UI. No React here — pure
 * functions only so they can run on the server or the client.
 */
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

import type { Confidence } from "@/lib/types";

/** Merge conditional class names and de-conflict Tailwind utilities. */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

const RELATIVE_UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 60 * 60 * 24 * 365],
  ["month", 60 * 60 * 24 * 30],
  ["week", 60 * 60 * 24 * 7],
  ["day", 60 * 60 * 24],
  ["hour", 60 * 60],
  ["minute", 60],
  ["second", 1],
];

const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });

/**
 * Human-friendly relative time, e.g. "3 minutes ago", "in 2 days".
 * Accepts an ISO string, epoch ms, or `Date`. Returns "" for invalid input.
 */
export function relativeTime(value: string | number | Date | null | undefined): string {
  if (value === null || value === undefined) return "";
  const date =
    value instanceof Date
      ? value
      : new Date(typeof value === "number" ? value : value);
  const ts = date.getTime();
  if (Number.isNaN(ts)) return "";

  const deltaSeconds = Math.round((ts - Date.now()) / 1000);
  const abs = Math.abs(deltaSeconds);
  if (abs < 5) return "just now";

  for (const [unit, secondsInUnit] of RELATIVE_UNITS) {
    if (abs >= secondsInUnit || unit === "second") {
      const amount = Math.round(deltaSeconds / secondsInUnit);
      return rtf.format(amount, unit);
    }
  }
  return "just now";
}

/**
 * Tailwind text/bg/border color tokens for a confidence (or risk) band.
 * Returns class fragments callers can compose, plus a hex-ish dot color.
 */
export function confidenceColor(
  confidence: Confidence | string | null | undefined,
): { text: string; bg: string; border: string; dot: string; label: string } {
  switch (confidence) {
    case "high":
      return {
        text: "text-ok",
        bg: "bg-ok/10",
        border: "border-ok/40",
        dot: "bg-ok",
        label: "High",
      };
    case "low":
      return {
        text: "text-danger",
        bg: "bg-danger/10",
        border: "border-danger/40",
        dot: "bg-danger",
        label: "Low",
      };
    case "medium":
      return {
        text: "text-warn",
        bg: "bg-warn/10",
        border: "border-warn/40",
        dot: "bg-warn",
        label: "Medium",
      };
    default:
      return {
        text: "text-muted",
        bg: "bg-surface-2",
        border: "border-border",
        dot: "bg-faint",
        label: confidence ? String(confidence) : "Unknown",
      };
  }
}

/** Short git hash (first `len` chars). Safe on null/empty input. */
export function shortHash(
  hash: string | null | undefined,
  len = 7,
): string {
  if (!hash) return "";
  return hash.slice(0, len);
}

/** Truncate a string to `max` chars with a trailing ellipsis. */
export function truncate(
  value: string | null | undefined,
  max = 80,
): string {
  if (!value) return "";
  if (value.length <= max) return value;
  return `${value.slice(0, Math.max(0, max - 1)).trimEnd()}…`;
}

/** Compact byte formatter (e.g. 1.4 MB). */
export function formatBytes(bytes: number | null | undefined): string {
  if (!bytes || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const exp = Math.min(
    units.length - 1,
    Math.floor(Math.log(bytes) / Math.log(1024)),
  );
  const value = bytes / 1024 ** exp;
  return `${value.toFixed(value >= 10 || exp === 0 ? 0 : 1)} ${units[exp]}`;
}

/** Compact integer formatter (1234 -> "1.2k"). */
export function formatCount(n: number | null | undefined): string {
  if (!n) return "0";
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

/** Format a millisecond duration as "820ms" or "1.4s". */
export function formatDuration(ms: number | null | undefined): string {
  if (!ms || ms < 0) return "0ms";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)}s`;
}

/** Score 0..1 rendered as a percentage, e.g. "84%". */
export function formatScore(score: number | null | undefined): string {
  if (score === null || score === undefined || Number.isNaN(score)) return "—";
  return `${Math.round(score * 100)}%`;
}
