"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Play, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/Button";
import {
  ApiError,
  getRepositoryStatus,
  indexRepository,
} from "@/lib/api";
import type { IndexJob, RepoStatus } from "@/lib/types";

export interface RepoActionsProps {
  repositoryId: string;
  status: RepoStatus;
}

/**
 * (Re)index trigger for the repository overview header. After kicking a job it
 * polls status until the run settles, then refreshes the server component so
 * the overview reflects the new stats. Client-only.
 */
export function RepoActions({ repositoryId, status }: RepoActionsProps) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [indexing, setIndexing] = useState(status === "indexing");
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (pollRef.current) clearTimeout(pollRef.current);
    },
    [],
  );

  const pollUntilDone = useCallback(() => {
    const tick = async () => {
      try {
        const s = await getRepositoryStatus(repositoryId);
        const job: IndexJob | null = s.job;
        const active =
          s.repository.status === "indexing" ||
          job?.status === "running" ||
          job?.status === "pending";
        setIndexing(active);
        if (active) {
          pollRef.current = setTimeout(tick, 1500);
        } else {
          // Job finished: pull fresh server data into the overview.
          router.refresh();
        }
      } catch {
        // Stop polling on error; leave the button usable again.
        setIndexing(false);
      }
    };
    void tick();
  }, [repositoryId, router]);

  const handleIndex = useCallback(async () => {
    if (busy || indexing) return;
    setBusy(true);
    setError(null);
    try {
      await indexRepository(repositoryId, { incremental: true });
      setIndexing(true);
      pollUntilDone();
    } catch (err) {
      const apiErr = err instanceof ApiError ? err : null;
      setError(
        apiErr?.isNetworkError
          ? "API unreachable."
          : apiErr?.message ?? "Failed to start indexing.",
      );
    } finally {
      setBusy(false);
    }
  }, [busy, indexing, repositoryId, pollUntilDone]);

  const isIndexed = status === "indexed";

  return (
    <div className="flex flex-col items-end gap-1">
      <Button
        variant="primary"
        size="sm"
        loading={busy || indexing}
        disabled={busy || indexing}
        onClick={() => void handleIndex()}
      >
        {isIndexed ? (
          <RefreshCw className="h-3.5 w-3.5" />
        ) : (
          <Play className="h-3.5 w-3.5" />
        )}
        {indexing ? "Indexing…" : isIndexed ? "Reindex" : "Index now"}
      </Button>
      {error ? <span className="text-2xs text-danger">{error}</span> : null}
    </div>
  );
}
