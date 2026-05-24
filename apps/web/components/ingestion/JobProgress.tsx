"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  CheckCircle2,
  GitBranch,
  Loader2,
  Play,
  RefreshCw,
} from "lucide-react";

import { Card } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import {
  ApiError,
  getRepositoryStatus,
  indexRepository,
} from "@/lib/api";
import {
  cn,
  formatBytes,
  formatCount,
  relativeTime,
  shortHash,
} from "@/lib/format";
import type {
  IndexJob,
  JobStatus,
  RepoStatus,
  Repository,
} from "@/lib/types";

const ACTIVE_POLL_MS = 1500;
const IDLE_POLL_MS = 15000;

const REPO_TONE: Record<RepoStatus, { tone: BadgeTone; label: string }> = {
  indexed: { tone: "ok", label: "Indexed" },
  indexing: { tone: "warn", label: "Indexing" },
  registered: { tone: "neutral", label: "Not indexed" },
  error: { tone: "danger", label: "Error" },
};

function isActive(job: IndexJob | null, repo: Repository): boolean {
  if (repo.status === "indexing") return true;
  if (!job) return false;
  return job.status === "pending" || job.status === "running";
}

export interface JobProgressProps {
  repository: Repository;
  /** Notify the parent when the repo's status transitions (e.g. to refresh). */
  onStatusChange?: (repo: Repository) => void;
}

/**
 * Per-repository ingestion card. Polls `GET /repositories/{id}/status` and
 * renders a live progress bar, the job message, and the resulting stats. Polls
 * fast while a job is active and slows to a heartbeat when idle. Exposes a
 * (re)index trigger. Fails soft on transport errors.
 */
export function JobProgress({ repository, onStatusChange }: JobProgressProps) {
  const [repo, setRepo] = useState<Repository>(repository);
  const [job, setJob] = useState<IndexJob | null>(null);
  const [triggering, setTriggering] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lastStatus = useRef<RepoStatus>(repository.status);
  const onStatusChangeRef = useRef(onStatusChange);
  onStatusChangeRef.current = onStatusChange;

  // Keep local state in sync if the parent hands us a fresher repo object.
  useEffect(() => {
    setRepo(repository);
  }, [repository]);

  const poll = useCallback(async () => {
    try {
      const status = await getRepositoryStatus(repository.id);
      setRepo(status.repository);
      setJob(status.job);
      setError(null);
      if (status.repository.status !== lastStatus.current) {
        lastStatus.current = status.repository.status;
        onStatusChangeRef.current?.(status.repository);
      }
    } catch (err) {
      const apiErr = err instanceof ApiError ? err : null;
      // Don't blow away the last good render; just note the blip.
      setError(
        apiErr?.isNetworkError
          ? "Status unavailable — API unreachable."
          : apiErr?.message ?? null,
      );
    }
  }, [repository.id]);

  // Adaptive polling loop.
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const tick = async () => {
      if (cancelled) return;
      await poll();
      if (cancelled) return;
      const delay = isActive(job, repo) ? ACTIVE_POLL_MS : IDLE_POLL_MS;
      timer = setTimeout(tick, delay);
    };

    void tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
    // Re-arm cadence when activity flips; `poll` is stable per repo id.
  }, [poll, isActive(job, repo)]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleIndex = useCallback(async () => {
    if (triggering) return;
    setTriggering(true);
    setError(null);
    try {
      const newJob = await indexRepository(repository.id, { incremental: true });
      setJob(newJob);
      setRepo((r) => ({ ...r, status: "indexing" }));
      lastStatus.current = "indexing";
      // Kick an immediate poll so the bar starts moving without waiting.
      void poll();
    } catch (err) {
      const apiErr = err instanceof ApiError ? err : null;
      setError(
        apiErr?.isNetworkError
          ? "Backend unreachable — start the API and try again."
          : apiErr?.message ?? "Failed to start indexing.",
      );
    } finally {
      setTriggering(false);
    }
  }, [triggering, repository.id, poll]);

  const active = isActive(job, repo);
  const repoTone = REPO_TONE[repo.status] ?? REPO_TONE.registered;
  const stats = (job?.stats ?? repo.stats) || repo.stats;
  const progress = job ? Math.round(job.progress * 100) : repo.status === "indexed" ? 100 : 0;
  const errorText = error ?? job?.error ?? repo.error ?? null;

  return (
    <Card className="overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
        <div className="min-w-0">
          <Link
            href={`/repositories/${repo.id}`}
            className="truncate text-sm font-semibold text-fg hover:text-accent"
          >
            {repo.name}
          </Link>
          <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-2xs text-faint">
            <span className="inline-flex items-center gap-1">
              <GitBranch className="h-3 w-3" />
              <span className="mono text-muted">{repo.branch}</span>
            </span>
            {repo.head_commit ? (
              <span className="mono" title={repo.head_commit}>
                @{shortHash(repo.head_commit)}
              </span>
            ) : null}
            {repo.last_indexed_at ? (
              <span>indexed {relativeTime(repo.last_indexed_at)}</span>
            ) : null}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Badge tone={repoTone.tone} dot>
            {repoTone.label}
          </Badge>
          <Button
            variant={repo.status === "indexed" ? "secondary" : "primary"}
            size="sm"
            loading={triggering}
            disabled={active || triggering}
            onClick={() => void handleIndex()}
          >
            {repo.status === "indexed" ? (
              <RefreshCw className="h-3.5 w-3.5" />
            ) : (
              <Play className="h-3.5 w-3.5" />
            )}
            {repo.status === "indexed" ? "Reindex" : "Index"}
          </Button>
        </div>
      </div>

      {/* Progress + status */}
      <div className="space-y-3 px-4 py-3">
        <ProgressBar
          progress={progress}
          status={job?.status ?? null}
          active={active}
          error={Boolean(errorText) && repo.status === "error"}
        />

        <div className="flex items-center gap-2 text-xs">
          <JobIcon job={job} repo={repo} active={active} />
          <span className="truncate text-muted">
            {job?.message ||
              (repo.status === "indexed"
                ? "Index up to date."
                : repo.status === "indexing"
                  ? "Indexing in progress…"
                  : "Not indexed yet.")}
          </span>
          {active ? (
            <span className="mono ml-auto shrink-0 text-faint">
              {progress}%
            </span>
          ) : null}
        </div>

        {errorText ? (
          <p className="rounded-md border border-danger/30 bg-danger/10 px-2.5 py-1.5 text-2xs text-danger">
            {errorText}
          </p>
        ) : null}

        {/* Stats */}
        <div className="grid grid-cols-4 gap-2">
          <StatCell label="Files" value={formatCount(stats.num_files)} />
          <StatCell label="Chunks" value={formatCount(stats.num_chunks)} />
          <StatCell label="Skipped" value={formatCount(stats.num_skipped)} />
          <StatCell label="Size" value={formatBytes(stats.bytes_indexed)} />
        </div>
      </div>
    </Card>
  );
}

function ProgressBar({
  progress,
  status,
  active,
  error,
}: {
  progress: number;
  status: JobStatus | null;
  active: boolean;
  error: boolean;
}) {
  const pct = Math.min(100, Math.max(0, progress));
  const barColor = error
    ? "bg-danger"
    : status === "succeeded" || pct >= 100
      ? "bg-ok"
      : "bg-accent";
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-2">
      <div
        className={cn(
          "h-full rounded-full transition-[width] duration-500",
          barColor,
          active && "animate-pulse",
        )}
        style={{ width: `${pct}%` }}
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
      />
    </div>
  );
}

function JobIcon({
  job,
  repo,
  active,
}: {
  job: IndexJob | null;
  repo: Repository;
  active: boolean;
}) {
  if (active) {
    return <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-accent" />;
  }
  if (repo.status === "error" || job?.status === "failed") {
    return <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-danger" />;
  }
  if (repo.status === "indexed" || job?.status === "succeeded") {
    return <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-ok" />;
  }
  return <Play className="h-3.5 w-3.5 shrink-0 text-faint" />;
}

function StatCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border bg-surface-2 px-2 py-1.5">
      <div className="text-2xs text-faint">{label}</div>
      <div className="mono mt-0.5 text-sm font-medium text-fg">{value}</div>
    </div>
  );
}
