"use client";

import { useCallback } from "react";
import { Database, Plus } from "lucide-react";

import { useWorkspace } from "@/components/WorkspaceProvider";
import { ConnectRepoForm } from "@/components/ingestion/ConnectRepoForm";
import { JobProgress } from "@/components/ingestion/JobProgress";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Spinner } from "@/components/ui/Spinner";
import { Badge } from "@/components/ui/Badge";
import type { Repository } from "@/lib/types";

export default function IngestionPage() {
  const {
    workspace,
    repositories,
    loading,
    reposLoading,
    offline,
    refreshRepositories,
  } = useWorkspace();

  const handleConnected = useCallback(
    (_repo: Repository) => {
      void refreshRepositories();
    },
    [refreshRepositories],
  );

  const handleStatusChange = useCallback(
    (_repo: Repository) => {
      // A repo flipped state (e.g. indexing -> indexed). Refresh the shared
      // list so the sidebar/topbar badges stay in sync.
      void refreshRepositories();
    },
    [refreshRepositories],
  );

  return (
    <div className="mx-auto max-w-5xl px-6 py-6">
      {/* Header */}
      <div className="mb-5">
        <h1 className="flex items-center gap-2 text-lg font-semibold tracking-tight text-fg">
          <Database className="h-5 w-5 text-accent" />
          Ingestion
        </h1>
        <p className="mt-0.5 text-sm text-muted">
          Connect repositories and (re)index them into the vector store.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-5">
        {/* Connect form */}
        <div className="lg:col-span-2">
          <Card>
            <CardHeader>
              <CardTitle>Connect a repository</CardTitle>
              <Plus className="h-4 w-4 text-faint" />
            </CardHeader>
            <CardBody>
              {workspace ? (
                <ConnectRepoForm
                  workspaceId={workspace.id}
                  onConnected={handleConnected}
                />
              ) : (
                <p className="text-sm text-muted">
                  {offline
                    ? "API unreachable — start the backend to connect a repository."
                    : "Select or create a workspace first."}
                </p>
              )}
            </CardBody>
          </Card>
        </div>

        {/* Repo list with live progress */}
        <div className="space-y-3 lg:col-span-3">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold text-fg">Repositories</h2>
            <Badge tone="neutral">{repositories.length}</Badge>
            {reposLoading ? <Spinner size="sm" /> : null}
          </div>

          {loading ? (
            <div className="flex justify-center py-12">
              <Spinner size="lg" label="Loading…" />
            </div>
          ) : repositories.length === 0 ? (
            <EmptyState
              icon={Database}
              title="No repositories yet"
              description="Connect a local path or git URL on the left, then index it to make it searchable."
              compact
            />
          ) : (
            <div className="space-y-3">
              {repositories.map((repo) => (
                <JobProgress
                  key={repo.id}
                  repository={repo}
                  onStatusChange={handleStatusChange}
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
