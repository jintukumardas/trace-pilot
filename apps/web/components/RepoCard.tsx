import Link from "next/link";
import {
  GitBranch,
  FileCode2,
  Boxes,
  MessageSquare,
  Database,
  ArrowUpRight,
} from "lucide-react";

import { Card } from "@/components/ui/Card";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { LanguageBar } from "@/components/LanguageBar";
import {
  cn,
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

/**
 * Dense repository tile for the dashboard grid. Shows status, the last-index
 * stats, a language breakdown bar, and quick links into the per-repo surfaces.
 * Degrades gracefully when a repo has never been indexed.
 */
export function RepoCard({ repo }: { repo: Repository }) {
  const status = STATUS_META[repo.status] ?? STATUS_META.registered;
  const stats = repo.stats;
  const source = repo.git_url ?? repo.local_path ?? "—";
  const overviewHref = `/repositories/${repo.id}`;

  return (
    <Card className="group flex flex-col transition-colors hover:border-border-strong">
      {/* Header */}
      <div className="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
        <div className="min-w-0">
          <Link
            href={overviewHref}
            className="flex items-center gap-1.5 text-sm font-semibold text-fg outline-none hover:text-accent focus-visible:text-accent"
          >
            <span className="truncate">{repo.name}</span>
            <ArrowUpRight className="h-3.5 w-3.5 shrink-0 text-faint transition-colors group-hover:text-accent" />
          </Link>
          <p className="mono mt-1 truncate text-2xs text-faint" title={source}>
            {source}
          </p>
        </div>
        <Badge tone={status.tone} dot>
          {status.label}
        </Badge>
      </div>

      {/* Body */}
      <div className="flex flex-1 flex-col gap-3 px-4 py-3">
        {/* Branch + commit */}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-2xs text-muted">
          <span className="inline-flex items-center gap-1">
            <GitBranch className="h-3 w-3 text-faint" />
            <span className="mono text-fg">{repo.branch}</span>
          </span>
          {repo.head_commit ? (
            <span className="mono text-faint" title={repo.head_commit}>
              @{shortHash(repo.head_commit)}
            </span>
          ) : null}
          {repo.last_indexed_at ? (
            <span className="text-faint">
              indexed {relativeTime(repo.last_indexed_at)}
            </span>
          ) : null}
        </div>

        {/* Error banner */}
        {repo.status === "error" && repo.error ? (
          <p className="rounded-md border border-danger/30 bg-danger/10 px-2 py-1.5 text-2xs text-danger">
            {repo.error}
          </p>
        ) : null}

        {/* Stats */}
        <div className="grid grid-cols-3 gap-2">
          <Stat
            icon={FileCode2}
            label="Files"
            value={formatCount(stats.num_files)}
          />
          <Stat
            icon={Boxes}
            label="Chunks"
            value={formatCount(stats.num_chunks)}
          />
          <Stat
            icon={Database}
            label="Size"
            value={formatBytes(stats.bytes_indexed)}
          />
        </div>

        {/* Languages */}
        <LanguageBar languages={stats.languages} />
      </div>

      {/* Footer quick-links */}
      <div className="flex items-center gap-1.5 border-t border-border px-4 py-2.5">
        <QuickLink href={overviewHref} label="Overview" />
        <QuickLink
          href={`/chat?repo=${repo.id}`}
          label="Ask"
          icon={MessageSquare}
        />
        <QuickLink href={`/ingestion?repo=${repo.id}`} label="Ingest" icon={Database} />
      </div>
    </Card>
  );
}

function Stat({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof FileCode2;
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-md border border-border bg-surface-2 px-2 py-1.5">
      <div className="flex items-center gap-1 text-2xs text-faint">
        <Icon className="h-3 w-3" />
        {label}
      </div>
      <div className="mono mt-0.5 text-sm font-medium text-fg">{value}</div>
    </div>
  );
}

function QuickLink({
  href,
  label,
  icon: Icon,
}: {
  href: string;
  label: string;
  icon?: typeof MessageSquare;
}) {
  return (
    <Link
      href={href}
      className={cn(
        "inline-flex items-center gap-1 rounded px-1.5 py-1 text-2xs font-medium text-muted transition-colors",
        "hover:bg-surface-2 hover:text-fg",
      )}
    >
      {Icon ? <Icon className="h-3 w-3" /> : null}
      {label}
    </Link>
  );
}
