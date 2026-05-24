"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import {
  Boxes,
  Database,
  FileCode2,
  FolderPlus,
  LayoutDashboard,
  Plus,
  RefreshCw,
  WifiOff,
} from "lucide-react";

import { useWorkspace } from "@/components/WorkspaceProvider";
import { RepoCard } from "@/components/RepoCard";
import { Button } from "@/components/ui/Button";
import { Badge } from "@/components/ui/Badge";
import { Card, CardBody } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";
import { ApiError, createWorkspace } from "@/lib/api";
import { formatBytes, formatCount } from "@/lib/format";
import type { Repository } from "@/lib/types";

export default function DashboardPage() {
  const {
    workspaces,
    workspace,
    repositories,
    loading,
    reposLoading,
    offline,
    selectWorkspace,
    refresh,
  } = useWorkspace();

  const [creating, setCreating] = useState(false);

  const totals = useMemo(() => aggregate(repositories), [repositories]);

  return (
    <div className="mx-auto max-w-6xl px-6 py-6">
      {/* Header */}
      <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-lg font-semibold tracking-tight text-fg">
            <LayoutDashboard className="h-5 w-5 text-accent" />
            Dashboard
          </h1>
          <p className="mt-0.5 text-sm text-muted">
            Connected repositories and indexing status across your workspace.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <WorkspacePicker
            onCreate={() => setCreating(true)}
            selectWorkspace={selectWorkspace}
            workspaces={workspaces}
            currentId={workspace?.id ?? null}
          />
          <Button
            variant="ghost"
            size="icon"
            title="Refresh"
            onClick={() => void refresh()}
            disabled={loading}
          >
            <RefreshCw className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {/* Offline / loading states */}
      {offline ? (
        <Card className="mb-6 border-danger/30">
          <CardBody className="flex items-center gap-3">
            <WifiOff className="h-5 w-5 text-danger" />
            <div>
              <p className="text-sm font-medium text-fg">API unreachable</p>
              <p className="text-xs text-muted">
                Start the TracePilot API (default{" "}
                <span className="mono">http://localhost:8000</span>) and refresh.
              </p>
            </div>
          </CardBody>
        </Card>
      ) : null}

      {/* Create-workspace inline form */}
      {creating ? (
        <CreateWorkspaceForm
          onClose={() => setCreating(false)}
          onCreated={async (id) => {
            setCreating(false);
            await refresh();
            selectWorkspace(id);
          }}
        />
      ) : null}

      {/* Empty: no workspaces */}
      {loading ? (
        <div className="flex justify-center py-20">
          <Spinner size="lg" label="Loading workspaces…" />
        </div>
      ) : workspaces.length === 0 && !offline ? (
        <EmptyState
          icon={FolderPlus}
          title="No workspaces yet"
          description="A workspace groups the repositories your team investigates together. Create one to get started."
          action={
            <Button variant="primary" size="sm" onClick={() => setCreating(true)}>
              <Plus className="h-3.5 w-3.5" />
              New workspace
            </Button>
          }
        />
      ) : workspace ? (
        <>
          {/* Workspace summary tiles */}
          <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <SummaryTile
              icon={Database}
              label="Repositories"
              value={formatCount(repositories.length)}
            />
            <SummaryTile
              icon={FileCode2}
              label="Indexed files"
              value={formatCount(totals.files)}
            />
            <SummaryTile
              icon={Boxes}
              label="Chunks"
              value={formatCount(totals.chunks)}
            />
            <SummaryTile
              icon={Database}
              label="Indexed size"
              value={formatBytes(totals.bytes)}
            />
          </div>

          {/* Repo grid */}
          <div className="mb-3 flex items-center justify-between">
            <h2 className="flex items-center gap-2 text-sm font-semibold text-fg">
              Repositories
              <Badge tone="neutral">{repositories.length}</Badge>
              {reposLoading ? <Spinner size="sm" /> : null}
            </h2>
            <Link href="/ingestion">
              <Button variant="secondary" size="sm">
                <Plus className="h-3.5 w-3.5" />
                Connect repo
              </Button>
            </Link>
          </div>

          {repositories.length === 0 ? (
            <EmptyState
              icon={Database}
              title="No repositories connected"
              description="Connect a local path or a git URL, then index it to make it searchable by the copilot."
              action={
                <Link href="/ingestion">
                  <Button variant="primary" size="sm">
                    <Plus className="h-3.5 w-3.5" />
                    Connect a repository
                  </Button>
                </Link>
              }
            />
          ) : (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
              {repositories.map((repo) => (
                <RepoCard key={repo.id} repo={repo} />
              ))}
            </div>
          )}
        </>
      ) : null}
    </div>
  );
}

function aggregate(repos: Repository[]): {
  files: number;
  chunks: number;
  bytes: number;
} {
  return repos.reduce(
    (acc, r) => ({
      files: acc.files + r.stats.num_files,
      chunks: acc.chunks + r.stats.num_chunks,
      bytes: acc.bytes + r.stats.bytes_indexed,
    }),
    { files: 0, chunks: 0, bytes: 0 },
  );
}

function SummaryTile({
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

function WorkspacePicker({
  workspaces,
  currentId,
  selectWorkspace,
  onCreate,
}: {
  workspaces: { id: string; name: string }[];
  currentId: string | null;
  selectWorkspace: (id: string) => void;
  onCreate: () => void;
}) {
  return (
    <div className="flex items-center gap-2">
      {workspaces.length > 0 ? (
        <select
          value={currentId ?? ""}
          onChange={(e) => selectWorkspace(e.target.value)}
          aria-label="Select workspace"
          className="h-9 rounded-md border border-border-strong bg-surface-2 px-2.5 text-sm text-fg outline-none focus-visible:ring-2 focus-visible:ring-accent/60"
        >
          {workspaces.map((w) => (
            <option key={w.id} value={w.id}>
              {w.name}
            </option>
          ))}
        </select>
      ) : null}
      <Button variant="secondary" size="sm" onClick={onCreate}>
        <Plus className="h-3.5 w-3.5" />
        Workspace
      </Button>
    </div>
  );
}

function CreateWorkspaceForm({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (id: string) => Promise<void> | void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const ws = await createWorkspace({
        name: name.trim(),
        description: description.trim() || null,
      });
      await onCreated(ws.id);
    } catch (err) {
      const apiErr = err instanceof ApiError ? err : null;
      setError(
        apiErr?.isNetworkError
          ? "Backend unreachable — start the API and try again."
          : apiErr?.message ?? "Failed to create the workspace.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card className="mb-6">
      <form onSubmit={handleSubmit}>
        <CardBody className="space-y-3">
          <div className="flex items-center gap-2 text-sm font-semibold text-fg">
            <FolderPlus className="h-4 w-4 text-accent" />
            New workspace
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <label className="space-y-1">
              <span className="text-2xs font-medium uppercase tracking-wider text-faint">
                Name
              </span>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Platform team"
                autoFocus
                className="w-full rounded-md border border-border-strong bg-surface-2 px-2.5 py-1.5 text-sm text-fg placeholder:text-faint outline-none focus-visible:ring-2 focus-visible:ring-accent/60"
              />
            </label>
            <label className="space-y-1">
              <span className="text-2xs font-medium uppercase tracking-wider text-faint">
                Description (optional)
              </span>
              <input
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Repositories owned by the platform team"
                className="w-full rounded-md border border-border-strong bg-surface-2 px-2.5 py-1.5 text-sm text-fg placeholder:text-faint outline-none focus-visible:ring-2 focus-visible:ring-accent/60"
              />
            </label>
          </div>
          {error ? (
            <p className="rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger">
              {error}
            </p>
          ) : null}
          <div className="flex items-center justify-end gap-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={onClose}
              disabled={submitting}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              variant="primary"
              size="sm"
              loading={submitting}
              disabled={!name.trim() || submitting}
            >
              Create workspace
            </Button>
          </div>
        </CardBody>
      </form>
    </Card>
  );
}
