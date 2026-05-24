import Link from "next/link";
import {
  AlertTriangle,
  Boxes,
  Database,
  FileCode2,
  GitBranch,
  GitCommitHorizontal,
  MessageSquare,
  SkipForward,
} from "lucide-react";

import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/Card";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Button } from "@/components/ui/Button";
import { LanguageBar } from "@/components/LanguageBar";
import { RepoActions } from "@/components/repositories/RepoActions";
import { ApiError, getRepository } from "@/lib/api";
import {
  formatBytes,
  formatCount,
  relativeTime,
  shortHash,
} from "@/lib/format";
import type { RepoStatus, Repository } from "@/lib/types";

const STATUS_META: Record<RepoStatus, { tone: BadgeTone; label: string }> = {
  indexed: { tone: "ok", label: "Indexed" },
  indexing: { tone: "warn", label: "Indexing" },
  registered: { tone: "neutral", label: "Not indexed" },
  error: { tone: "danger", label: "Error" },
};

export default async function RepositoryOverviewPage({
  params,
}: {
  params: { id: string };
}) {
  let repo: Repository | null = null;
  let error: string | null = null;
  try {
    repo = await getRepository(params.id);
  } catch (err) {
    const apiErr = err instanceof ApiError ? err : null;
    error = apiErr?.isNetworkError
      ? "Backend unreachable — start the API to load this repository."
      : apiErr?.status === 404
        ? "Repository not found."
        : apiErr?.message ?? "Failed to load the repository.";
  }

  if (!repo) {
    return (
      <div className="mx-auto max-w-5xl px-6 py-10">
        <EmptyState
          icon={AlertTriangle}
          title="Repository unavailable"
          description={error ?? "Unknown error."}
          action={
            <Link href="/">
              <Button variant="secondary" size="sm">
                Back to dashboard
              </Button>
            </Link>
          }
        />
      </div>
    );
  }

  const status = STATUS_META[repo.status] ?? STATUS_META.registered;
  const stats = repo.stats;
  const source = repo.git_url ?? repo.local_path ?? "—";
  const langCount = Object.keys(stats.languages).length;

  return (
    <div className="mx-auto max-w-5xl px-6 py-6">
      {/* Header */}
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h1 className="truncate text-lg font-semibold tracking-tight text-fg">
              {repo.name}
            </h1>
            <Badge tone={status.tone} dot>
              {status.label}
            </Badge>
          </div>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
            <span className="inline-flex items-center gap-1">
              <GitBranch className="h-3.5 w-3.5 text-faint" />
              <span className="mono text-fg">{repo.branch}</span>
            </span>
            {repo.head_commit ? (
              <span className="inline-flex items-center gap-1">
                <GitCommitHorizontal className="h-3.5 w-3.5 text-faint" />
                <span className="mono text-fg" title={repo.head_commit}>
                  {shortHash(repo.head_commit, 10)}
                </span>
              </span>
            ) : null}
            <span className="mono truncate text-faint" title={source}>
              {source}
            </span>
            {repo.last_indexed_at ? (
              <span className="text-faint">
                indexed {relativeTime(repo.last_indexed_at)}
              </span>
            ) : null}
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Link href={`/chat?repo=${repo.id}`}>
            <Button variant="secondary" size="sm">
              <MessageSquare className="h-3.5 w-3.5" />
              Ask about this repo
            </Button>
          </Link>
          <RepoActions repositoryId={repo.id} status={repo.status} />
        </div>
      </div>

      {/* Error banner */}
      {repo.status === "error" && repo.error ? (
        <Card className="mb-5 border-danger/30">
          <CardBody className="flex items-start gap-2.5">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-danger" />
            <div>
              <p className="text-sm font-medium text-danger">
                Last indexing run failed
              </p>
              <p className="mono mt-0.5 text-xs text-muted">{repo.error}</p>
            </div>
          </CardBody>
        </Card>
      ) : null}

      {/* Stats tiles */}
      <div className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatTile
          icon={FileCode2}
          label="Files"
          value={formatCount(stats.num_files)}
        />
        <StatTile
          icon={Boxes}
          label="Chunks"
          value={formatCount(stats.num_chunks)}
        />
        <StatTile
          icon={SkipForward}
          label="Skipped"
          value={formatCount(stats.num_skipped)}
        />
        <StatTile
          icon={Database}
          label="Indexed size"
          value={formatBytes(stats.bytes_indexed)}
        />
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        {/* Languages */}
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Language breakdown</CardTitle>
            <Badge tone="neutral">{langCount}</Badge>
          </CardHeader>
          <CardBody>
            {langCount === 0 ? (
              <EmptyState
                icon={FileCode2}
                title="No languages indexed"
                description="Index this repository to compute a language breakdown."
                compact
              />
            ) : (
              <LanguageBar languages={stats.languages} legend maxLegend={12} />
            )}
          </CardBody>
        </Card>

        {/* Indexed-files summary */}
        <Card>
          <CardHeader>
            <CardTitle>Index summary</CardTitle>
          </CardHeader>
          <CardBody className="space-y-2.5">
            <SummaryRow label="Status" value={status.label} tone={status.tone} />
            <SummaryRow
              label="Files indexed"
              value={formatCount(stats.num_files)}
            />
            <SummaryRow
              label="Searchable chunks"
              value={formatCount(stats.num_chunks)}
            />
            <SummaryRow
              label="Files skipped"
              value={formatCount(stats.num_skipped)}
            />
            <SummaryRow
              label="On-disk size"
              value={formatBytes(stats.bytes_indexed)}
            />
            <SummaryRow
              label="Branch"
              value={repo.branch}
              mono
            />
            <SummaryRow
              label="Head commit"
              value={repo.head_commit ? shortHash(repo.head_commit, 10) : "—"}
              mono
            />
            <SummaryRow
              label="Last indexed"
              value={
                repo.last_indexed_at
                  ? relativeTime(repo.last_indexed_at)
                  : "Never"
              }
            />
          </CardBody>
        </Card>
      </div>
    </div>
  );
}

function StatTile({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Database;
  label: string;
  value: string;
}) {
  return (
    <Card>
      <CardBody className="flex items-center gap-3 p-3">
        <span className="flex h-9 w-9 items-center justify-center rounded-md border border-border bg-surface-2 text-accent">
          <Icon className="h-4 w-4" />
        </span>
        <div>
          <div className="mono text-lg font-semibold text-fg">{value}</div>
          <div className="text-2xs uppercase tracking-wider text-faint">
            {label}
          </div>
        </div>
      </CardBody>
    </Card>
  );
}

function SummaryRow({
  label,
  value,
  mono,
  tone,
}: {
  label: string;
  value: string;
  mono?: boolean;
  tone?: BadgeTone;
}) {
  return (
    <div className="flex items-center justify-between gap-2 text-xs">
      <span className="text-faint">{label}</span>
      {tone ? (
        <Badge tone={tone} dot>
          {value}
        </Badge>
      ) : (
        <span className={mono ? "mono text-fg" : "text-fg"}>{value}</span>
      )}
    </div>
  );
}
