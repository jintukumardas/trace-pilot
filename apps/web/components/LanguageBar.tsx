import { cn, formatCount } from "@/lib/format";

/**
 * Deterministic color assignment for a language label. Uses a fixed palette of
 * theme-token classes so the same language always renders the same hue across
 * the app, with a stable hash fallback for long-tail languages.
 */
const LANG_PALETTE = [
  "bg-accent",
  "bg-cyan",
  "bg-ok",
  "bg-warn",
  "bg-info",
  "bg-danger",
] as const;

// Well-known languages get a fixed slot for visual consistency.
const LANG_INDEX: Record<string, number> = {
  python: 0,
  typescript: 1,
  javascript: 3,
  go: 4,
  rust: 5,
  java: 2,
  markdown: 4,
  json: 2,
  yaml: 3,
};

function colorFor(language: string): string {
  const key = language.toLowerCase();
  if (key in LANG_INDEX) {
    return LANG_PALETTE[LANG_INDEX[key]! % LANG_PALETTE.length]!;
  }
  let hash = 0;
  for (let i = 0; i < key.length; i += 1) {
    hash = (hash * 31 + key.charCodeAt(i)) >>> 0;
  }
  return LANG_PALETTE[hash % LANG_PALETTE.length]!;
}

export interface LanguageBarProps {
  /** Map of language -> count (chunks or files). */
  languages: Record<string, number>;
  /** Show a legend with per-language counts below the bar. */
  legend?: boolean;
  /** Max legend entries before collapsing into a "+N" pill. */
  maxLegend?: number;
  className?: string;
}

/**
 * Horizontal proportional language breakdown bar. Server-safe. Renders a thin
 * placeholder track when there is no language data so cards keep their rhythm.
 */
export function LanguageBar({
  languages,
  legend = false,
  maxLegend = 6,
  className,
}: LanguageBarProps) {
  const entries = Object.entries(languages)
    .filter(([, count]) => count > 0)
    .sort((a, b) => b[1] - a[1]);
  const total = entries.reduce((sum, [, count]) => sum + count, 0);

  if (entries.length === 0 || total === 0) {
    return (
      <div className={cn("space-y-1.5", className)}>
        <div className="h-1.5 w-full rounded-full bg-surface-2" />
        <p className="text-2xs text-faint">No languages indexed yet.</p>
      </div>
    );
  }

  const shown = legend ? entries.slice(0, maxLegend) : entries;
  const hiddenCount = entries.length - shown.length;

  return (
    <div className={cn("space-y-2", className)}>
      <div className="flex h-1.5 w-full overflow-hidden rounded-full bg-surface-2">
        {entries.map(([lang, count]) => {
          const pct = (count / total) * 100;
          return (
            <span
              key={lang}
              className={cn("h-full", colorFor(lang))}
              style={{ width: `${pct}%` }}
              title={`${lang} · ${formatCount(count)} (${pct.toFixed(1)}%)`}
            />
          );
        })}
      </div>

      {legend ? (
        <div className="flex flex-wrap gap-x-3 gap-y-1">
          {shown.map(([lang, count]) => {
            const pct = ((count / total) * 100).toFixed(0);
            return (
              <span
                key={lang}
                className="inline-flex items-center gap-1.5 text-2xs text-muted"
              >
                <span
                  className={cn("h-2 w-2 rounded-sm", colorFor(lang))}
                  aria-hidden
                />
                <span className="text-fg">{lang}</span>
                <span className="mono text-faint">
                  {formatCount(count)} · {pct}%
                </span>
              </span>
            );
          })}
          {hiddenCount > 0 ? (
            <span className="text-2xs text-faint">+{hiddenCount} more</span>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
