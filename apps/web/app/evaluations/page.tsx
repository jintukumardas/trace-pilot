"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  FlaskConical,
  Play,
  RefreshCw,
  TrendingUp,
} from "lucide-react";

import { MetricCard, METRIC_META } from "@/components/evaluations/MetricCard";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";
import { ApiError, getEvals, runEvals } from "@/lib/api";
import { cn, formatScore, relativeTime } from "@/lib/format";
import type {
  EvalMetric,
  EvalResult,
  EvalRunSummary,
} from "@/lib/types";

const METRICS: EvalMetric[] = [
  "grounding",
  "relevance",
  "completeness",
  "tool_success",
  "retrieval_quality",
];

interface EvalsState {
  recent: EvalResult[];
  summary: Record<string, unknown>;
}

export default function EvaluationsPage() {
  const [data, setData] = useState<EvalsState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [runResult, setRunResult] = useState<EvalRunSummary | null>(null);
  const [runError, setRunError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await getEvals();
      setData({ recent: res.recent ?? [], summary: res.summary ?? {} });
    } catch (err) {
      const apiErr = err instanceof ApiError ? err : null;
      setError(
        apiErr?.isNetworkError
          ? "Backend unreachable — start the API to load evaluations."
          : apiErr?.message ?? "Failed to load evaluations.",
      );
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const handleRun = useCallback(async () => {
    if (running) return;
    setRunning(true);
    setRunError(null);
    try {
      const summary = await runEvals({});
      setRunResult(summary);
      await load();
    } catch (err) {
      const apiErr = err instanceof ApiError ? err : null;
      setRunError(
        apiErr?.isNetworkError
          ? "Backend unreachable — start the API and try again."
          : apiErr?.message ?? "Evaluation run failed.",
      );
    } finally {
      setRunning(false);
    }
  }, [running, load]);

  // Derive metric averages + pass rate. Prefer the just-finished run summary,
  // else the backend summary, else compute from the recent results.
  const computed = useMemo(
    () => computeSummary(data?.recent ?? [], data?.summary, runResult),
    [data, runResult],
  );

  return (
    <div className="mx-auto max-w-6xl px-6 py-6">
      {/* Header */}
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-lg font-semibold tracking-tight text-fg">
            <FlaskConical className="h-5 w-5 text-accent" />
            Evaluations
          </h1>
          <p className="mt-0.5 text-sm text-muted">
            Online and dataset-driven quality scores for grounded answers.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="icon"
            title="Refresh"
            onClick={() => void load()}
            disabled={loading}
          >
            <RefreshCw className="h-4 w-4" />
          </Button>
          <Button
            variant="primary"
            size="sm"
            loading={running}
            onClick={() => void handleRun()}
          >
            <Play className="h-3.5 w-3.5" />
            Run evaluation
          </Button>
        </div>
      </div>

      {runError ? (
        <Card className="mb-5 border-danger/30">
          <CardBody className="flex items-center gap-2.5">
            <AlertTriangle className="h-4 w-4 text-danger" />
            <span className="text-sm text-danger">{runError}</span>
          </CardBody>
        </Card>
      ) : null}

      {runResult ? (
        <Card className="mb-5 border-accent/30">
          <CardBody className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
            <span className="font-medium text-fg">Run complete</span>
            <span className="text-muted">
              dataset <span className="mono text-fg">{runResult.dataset}</span>
            </span>
            <span className="text-muted">
              {runResult.n} example{runResult.n === 1 ? "" : "s"}
            </span>
            <span className="text-muted">
              pass rate{" "}
              <span className="mono text-fg">
                {formatScore(runResult.pass_rate)}
              </span>
            </span>
          </CardBody>
        </Card>
      ) : null}

      {/* Pass-rate banner */}
      <div className="mb-5 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Card className="sm:col-span-1">
          <CardBody className="flex items-center gap-3">
            <span className="flex h-10 w-10 items-center justify-center rounded-md border border-border bg-surface-2 text-accent">
              <TrendingUp className="h-5 w-5" />
            </span>
            <div>
              <div className="mono text-2xl font-semibold text-fg">
                {computed.count > 0 ? formatScore(computed.passRate) : "—"}
              </div>
              <div className="text-2xs uppercase tracking-wider text-faint">
                Pass rate · {computed.count} eval
                {computed.count === 1 ? "" : "s"}
              </div>
            </div>
          </CardBody>
        </Card>
        <Card className="sm:col-span-2">
          <CardBody className="flex items-center gap-3">
            <span className="flex h-10 w-10 items-center justify-center rounded-md border border-border bg-surface-2 text-cyan">
              <FlaskConical className="h-5 w-5" />
            </span>
            <div>
              <div className="mono text-2xl font-semibold text-fg">
                {computed.count > 0 ? formatScore(computed.overall) : "—"}
              </div>
              <div className="text-2xs uppercase tracking-wider text-faint">
                Mean overall score
              </div>
            </div>
          </CardBody>
        </Card>
      </div>

      {/* Metric cards */}
      <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
        {METRICS.map((metric) => (
          <MetricCard
            key={metric}
            metric={metric}
            value={computed.averages[metric] ?? null}
            sampleSize={computed.count}
          />
        ))}
      </div>

      {/* Recent results table */}
      <Card>
        <CardHeader>
          <CardTitle>Recent evaluations</CardTitle>
          {data?.recent ? (
            <Badge tone="neutral">{data.recent.length}</Badge>
          ) : null}
        </CardHeader>
        <CardBody className="p-0">
          {loading ? (
            <div className="flex justify-center py-12">
              <Spinner size="lg" label="Loading…" />
            </div>
          ) : error ? (
            <div className="p-4">
              <EmptyState
                icon={AlertTriangle}
                title="Evaluations unavailable"
                description={error}
                compact
              />
            </div>
          ) : !data || data.recent.length === 0 ? (
            <div className="p-4">
              <EmptyState
                icon={FlaskConical}
                title="No evaluations yet"
                description="Chat responses are scored online automatically, or run the default dataset to populate this table."
                action={
                  <Button
                    variant="primary"
                    size="sm"
                    loading={running}
                    onClick={() => void handleRun()}
                  >
                    <Play className="h-3.5 w-3.5" />
                    Run evaluation
                  </Button>
                }
                compact
              />
            </div>
          ) : (
            <ResultsTable results={data.recent} />
          )}
        </CardBody>
      </Card>
    </div>
  );
}

function ResultsTable({ results }: { results: EvalResult[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs">
        <thead className="border-b border-border text-2xs uppercase tracking-wider text-faint">
          <tr>
            <th className="px-4 py-2 font-medium">Trace</th>
            <th className="px-4 py-2 font-medium">Workflow</th>
            <th className="px-4 py-2 font-medium">Overall</th>
            {METRICS.map((m) => (
              <th key={m} className="px-3 py-2 font-medium" title={METRIC_META[m].label}>
                {abbrev(m)}
              </th>
            ))}
            <th className="px-4 py-2 font-medium">When</th>
          </tr>
        </thead>
        <tbody>
          {results.map((res, i) => {
            const byMetric = new Map(res.scores.map((s) => [s.metric, s]));
            return (
              <tr
                key={res.trace_id ?? i}
                className="border-b border-border last:border-0 hover:bg-surface-2"
              >
                <td className="px-4 py-2">
                  <span className="mono text-muted" title={res.trace_id ?? ""}>
                    {res.trace_id ? truncMid(res.trace_id) : "—"}
                  </span>
                </td>
                <td className="px-4 py-2">
                  <Badge tone="neutral">{res.workflow}</Badge>
                </td>
                <td className="px-4 py-2">
                  <ScorePill value={res.overall} />
                </td>
                {METRICS.map((m) => {
                  const s = byMetric.get(m);
                  return (
                    <td key={m} className="px-3 py-2">
                      {s ? (
                        <span
                          className={cn(
                            "mono",
                            s.passed ? "text-fg" : "text-danger",
                          )}
                          title={s.rationale}
                        >
                          {formatScore(s.score)}
                        </span>
                      ) : (
                        <span className="text-faint">—</span>
                      )}
                    </td>
                  );
                })}
                <td className="px-4 py-2 text-muted">
                  {relativeTime(res.created_at)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ScorePill({ value }: { value: number }) {
  const tone: BadgeTone =
    value >= 0.8 ? "ok" : value >= 0.6 ? "warn" : "danger";
  return (
    <Badge tone={tone} mono>
      {formatScore(value)}
    </Badge>
  );
}

// --- helpers -----------------------------------------------------------------

function computeSummary(
  recent: EvalResult[],
  backendSummary: Record<string, unknown> | undefined,
  runResult: EvalRunSummary | null,
): {
  averages: Partial<Record<EvalMetric, number>>;
  passRate: number;
  overall: number;
  count: number;
} {
  // 1) A just-finished run is the freshest source of metric averages.
  if (runResult && runResult.n > 0) {
    return {
      averages: pickMetrics(runResult.metric_averages),
      passRate: runResult.pass_rate,
      overall: avg(runResult.results.map((r) => r.overall)),
      count: runResult.n,
    };
  }

  // 2) Backend-provided summary, if it carries the expected shape.
  const fromBackend = readBackendSummary(backendSummary);
  if (fromBackend && recent.length > 0) {
    return { ...fromBackend, count: recent.length };
  }

  // 3) Compute locally from recent results.
  const acc: Partial<Record<EvalMetric, number[]>> = {};
  let passed = 0;
  for (const res of recent) {
    let allPassed = res.scores.length > 0;
    for (const s of res.scores) {
      (acc[s.metric] ??= []).push(s.score);
      if (!s.passed) allPassed = false;
    }
    if (allPassed) passed += 1;
  }
  const averages: Partial<Record<EvalMetric, number>> = {};
  (Object.keys(acc) as EvalMetric[]).forEach((m) => {
    averages[m] = avg(acc[m] ?? []);
  });
  return {
    averages,
    passRate: recent.length > 0 ? passed / recent.length : 0,
    overall: avg(recent.map((r) => r.overall)),
    count: recent.length,
  };
}

function readBackendSummary(
  summary: Record<string, unknown> | undefined,
): { averages: Partial<Record<EvalMetric, number>>; passRate: number; overall: number } | null {
  if (!summary) return null;
  const ma = summary["metric_averages"];
  const pr = summary["pass_rate"];
  if (typeof ma !== "object" || ma === null) return null;
  const averages = pickMetrics(ma as Record<string, number>);
  return {
    averages,
    passRate: typeof pr === "number" ? pr : 0,
    overall:
      typeof summary["overall"] === "number"
        ? (summary["overall"] as number)
        : avg(Object.values(averages)),
  };
}

function pickMetrics(
  src: Record<string, number>,
): Partial<Record<EvalMetric, number>> {
  const out: Partial<Record<EvalMetric, number>> = {};
  for (const m of METRICS) {
    const v = src[m];
    if (typeof v === "number" && !Number.isNaN(v)) out[m] = v;
  }
  return out;
}

function avg(nums: number[]): number {
  if (nums.length === 0) return 0;
  return nums.reduce((s, n) => s + n, 0) / nums.length;
}

function abbrev(metric: EvalMetric): string {
  switch (metric) {
    case "grounding":
      return "Ground";
    case "relevance":
      return "Relev";
    case "completeness":
      return "Compl";
    case "tool_success":
      return "Tools";
    case "retrieval_quality":
      return "Retr";
    default:
      return metric;
  }
}

function truncMid(value: string, head = 6, tail = 4): string {
  if (value.length <= head + tail + 1) return value;
  return `${value.slice(0, head)}…${value.slice(-tail)}`;
}
