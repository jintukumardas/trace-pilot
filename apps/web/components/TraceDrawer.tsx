"use client";

import { useEffect, useState } from "react";
import {
  AlertTriangle,
  Boxes,
  Cpu,
  Search,
  Wrench,
  Activity,
} from "lucide-react";

import { Drawer } from "@/components/ui/Drawer";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Spinner } from "@/components/ui/Spinner";
import { EmptyState } from "@/components/ui/EmptyState";
import { ApiError, getTrace } from "@/lib/api";
import { cn, formatDuration, formatScore } from "@/lib/format";
import type { SpanRecord, TraceRecord } from "@/lib/types";

export interface TraceDrawerProps {
  open: boolean;
  onClose: () => void;
  traceId?: string | null;
}

const SPAN_ICON: Record<string, typeof Cpu> = {
  generation: Cpu,
  retrieval: Search,
  tool: Wrench,
  span: Boxes,
};

const STATUS_TONE: Record<string, BadgeTone> = {
  ok: "ok",
  success: "ok",
  error: "danger",
  warning: "warn",
};

/**
 * Developer diagnostics panel. Lazily fetches the full `TraceRecord` for a
 * response's `trace_id` and renders the span tree with per-span durations,
 * statuses and (when present) Langfuse scores. Tolerant of a missing trace or
 * an offline backend.
 */
export function TraceDrawer({ open, onClose, traceId }: TraceDrawerProps) {
  const [trace, setTrace] = useState<TraceRecord | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open || !traceId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setTrace(null);
    getTrace(traceId)
      .then((t) => {
        if (!cancelled) setTrace(t);
      })
      .catch((err) => {
        if (cancelled) return;
        const apiErr = err instanceof ApiError ? err : null;
        setError(
          apiErr?.isNetworkError
            ? "Backend unreachable — cannot load the trace."
            : apiErr?.status === 404
              ? "Trace not found. It may not have been persisted yet."
              : apiErr?.message ?? "Failed to load trace.",
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, traceId]);

  const totalMs = trace?.latency_ms ?? 0;
  const scoreEntries = trace ? Object.entries(trace.scores ?? {}) : [];

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title="Diagnostics"
      description={
        traceId ? (
          <span className="mono">{traceId}</span>
        ) : (
          "No trace attached"
        )
      }
      widthClassName="w-full max-w-2xl"
    >
      <div className="p-4">
        {!traceId ? (
          <EmptyState
            icon={Activity}
            title="No trace for this response"
            description="Tracing produces a trace_id only when the backend has Langfuse/telemetry enabled."
            compact
          />
        ) : loading ? (
          <div className="flex items-center justify-center py-16">
            <Spinner size="lg" label="Loading trace…" />
          </div>
        ) : error ? (
          <EmptyState
            icon={AlertTriangle}
            title="Trace unavailable"
            description={error}
            compact
          />
        ) : trace ? (
          <div className="space-y-4">
            {/* Summary */}
            <div className="rounded-lg border border-border bg-surface-2 p-3">
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-sm font-medium text-fg">
                  {trace.name}
                </span>
                <Badge tone={STATUS_TONE[trace.status] ?? "neutral"} dot>
                  {trace.status}
                </Badge>
              </div>
              <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-2xs text-muted">
                <span>
                  workflow{" "}
                  <span className="mono text-fg">{trace.workflow}</span>
                </span>
                <span>
                  latency{" "}
                  <span className="mono text-fg">
                    {formatDuration(totalMs)}
                  </span>
                </span>
                <span>
                  spans{" "}
                  <span className="mono text-fg">{trace.spans.length}</span>
                </span>
              </div>
              {scoreEntries.length > 0 ? (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {scoreEntries.map(([metric, value]) => (
                    <Badge key={metric} tone="cyan" mono>
                      {metric} {formatScore(value)}
                    </Badge>
                  ))}
                </div>
              ) : null}
              {trace.tags.length > 0 ? (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {trace.tags.map((tag) => (
                    <Badge key={tag} tone="neutral">
                      {tag}
                    </Badge>
                  ))}
                </div>
              ) : null}
            </div>

            {/* Span tree */}
            {trace.spans.length === 0 ? (
              <EmptyState
                icon={Boxes}
                title="No spans recorded"
                description="The trace exists but carries no sub-steps."
                compact
              />
            ) : (
              <ol className="space-y-1.5">
                {trace.spans.map((span) => (
                  <SpanRow
                    key={span.id}
                    span={span}
                    totalMs={totalMs || spanDuration(span) || 1}
                  />
                ))}
              </ol>
            )}
          </div>
        ) : null}
      </div>
    </Drawer>
  );
}

function spanDuration(span: SpanRecord): number {
  return Math.max(0, span.end_ms - span.start_ms);
}

function SpanRow({ span, totalMs }: { span: SpanRecord; totalMs: number }) {
  const [expanded, setExpanded] = useState(false);
  const Icon = SPAN_ICON[span.type] ?? Boxes;
  const dur = spanDuration(span);
  const widthPct = Math.min(100, Math.max(2, (dur / totalMs) * 100));
  const failed = span.status === "error" || Boolean(span.error);
  const scoreMeta =
    span.metadata && typeof span.metadata === "object"
      ? (span.metadata as Record<string, unknown>)["score"]
      : undefined;

  return (
    <li className="rounded-md border border-border bg-surface-2">
      <button
        type="button"
        onClick={() => setExpanded((e) => !e)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        <Icon
          className={cn(
            "h-3.5 w-3.5 shrink-0",
            failed ? "text-danger" : "text-faint",
          )}
        />
        <span className="mono min-w-0 flex-1 truncate text-xs text-fg">
          {span.name}
        </span>
        {typeof scoreMeta === "number" ? (
          <Badge tone="cyan" mono>
            {formatScore(scoreMeta)}
          </Badge>
        ) : null}
        <span
          className={cn(
            "mono shrink-0 text-2xs",
            failed ? "text-danger" : "text-muted",
          )}
        >
          {formatDuration(dur)}
        </span>
      </button>

      {/* Duration bar */}
      <div className="px-3 pb-2">
        <div className="h-1 w-full overflow-hidden rounded-full bg-bg/60">
          <div
            className={cn(
              "h-full rounded-full",
              failed ? "bg-danger" : "bg-accent",
            )}
            style={{ width: `${widthPct}%` }}
          />
        </div>
      </div>

      {expanded ? (
        <div className="space-y-2 border-t border-border px-3 py-2">
          {failed && span.error ? (
            <p className="rounded border border-danger/30 bg-danger/10 px-2 py-1 text-2xs text-danger">
              {span.error}
            </p>
          ) : null}
          <KeyValue label="type" value={span.type} mono />
          <KeyValue label="status" value={span.status} mono />
          {span.input !== undefined && span.input !== null ? (
            <JsonBlock label="input" value={span.input} />
          ) : null}
          {span.output !== undefined && span.output !== null ? (
            <JsonBlock label="output" value={span.output} />
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

function KeyValue({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-center gap-2 text-2xs">
      <span className="w-14 shrink-0 text-faint">{label}</span>
      <span className={cn("text-muted", mono && "mono")}>{value}</span>
    </div>
  );
}

function JsonBlock({ label, value }: { label: string; value: unknown }) {
  let text: string;
  if (typeof value === "string") {
    text = value;
  } else {
    try {
      text = JSON.stringify(value, null, 2);
    } catch {
      text = String(value);
    }
  }
  // Bound the rendered payload so a giant context dump can't lock the drawer.
  const truncated = text.length > 4000;
  const display = truncated ? `${text.slice(0, 4000)}\n… (truncated)` : text;

  return (
    <div>
      <span className="text-2xs text-faint">{label}</span>
      <pre className="mono mt-1 max-h-48 overflow-auto rounded border border-border bg-bg/40 p-2 text-2xs leading-relaxed text-muted">
        {display}
      </pre>
    </div>
  );
}
