import { cn, formatScore } from "@/lib/format";
import type { EvalMetric } from "@/lib/types";

export const METRIC_META: Record<
  EvalMetric,
  { label: string; description: string }
> = {
  grounding: {
    label: "Grounding",
    description: "Answer claims supported by cited evidence",
  },
  relevance: {
    label: "Relevance",
    description: "Answer addresses the question asked",
  },
  completeness: {
    label: "Completeness",
    description: "Required sections present (citations, next steps)",
  },
  tool_success: {
    label: "Tool success",
    description: "Fraction of invoked tools that succeeded",
  },
  retrieval_quality: {
    label: "Retrieval quality",
    description: "Relevant chunks surfaced above threshold",
  },
};

/** Color band for a 0..1 score. */
function scoreBand(score: number): { text: string; bar: string } {
  if (score >= 0.8) return { text: "text-ok", bar: "bg-ok" };
  if (score >= 0.6) return { text: "text-warn", bar: "bg-warn" };
  return { text: "text-danger", bar: "bg-danger" };
}

export interface MetricCardProps {
  metric: EvalMetric;
  /** Average score 0..1, or null/undefined when there's no data. */
  value: number | null | undefined;
  /** Optional sample size shown as context. */
  sampleSize?: number;
  className?: string;
}

/**
 * Single evaluation metric summary tile — a large score, a progress meter, and
 * the metric's plain-language definition. Renders a neutral placeholder when no
 * evaluations have been run.
 */
export function MetricCard({
  metric,
  value,
  sampleSize,
  className,
}: MetricCardProps) {
  const meta = METRIC_META[metric];
  const hasValue =
    value !== null && value !== undefined && !Number.isNaN(value);
  const pct = hasValue ? Math.min(100, Math.max(0, value * 100)) : 0;
  const band = hasValue ? scoreBand(value) : { text: "text-faint", bar: "bg-surface-2" };

  return (
    <div
      className={cn(
        "rounded-lg border border-border bg-surface p-3.5 shadow-panel",
        className,
      )}
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-2xs font-medium uppercase tracking-wider text-faint">
          {meta.label}
        </span>
        {sampleSize !== undefined ? (
          <span className="mono text-2xs text-faint">n={sampleSize}</span>
        ) : null}
      </div>

      <div className={cn("mono mt-1 text-2xl font-semibold", band.text)}>
        {hasValue ? formatScore(value) : "—"}
      </div>

      <div className="mt-2 h-1 w-full overflow-hidden rounded-full bg-surface-2">
        <div
          className={cn("h-full rounded-full transition-[width]", band.bar)}
          style={{ width: `${pct}%` }}
        />
      </div>

      <p className="mt-2 text-2xs leading-relaxed text-muted">
        {meta.description}
      </p>
    </div>
  );
}
