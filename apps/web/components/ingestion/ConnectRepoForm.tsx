"use client";

import { useState } from "react";
import { FolderGit2, GitBranch, Link2, Plus } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { Tabs, type TabItem } from "@/components/ui/Tabs";
import { ApiError, connectRepository } from "@/lib/api";
import { cn } from "@/lib/format";
import type { Repository, RepositoryConnectRequest } from "@/lib/types";

type SourceMode = "local" | "git";

const SOURCE_TABS: TabItem<SourceMode>[] = [
  { value: "local", label: "Local path" },
  { value: "git", label: "Git URL" },
];

export interface ConnectRepoFormProps {
  workspaceId: string | null;
  /** Called with the newly connected repository on success. */
  onConnected: (repo: Repository) => void;
  className?: string;
}

/**
 * Connect a repository to the active workspace, either by absolute local path
 * (host/mount) or by clonable git URL with a branch. Validates inputs client
 * side and surfaces backend errors inline; the parent refreshes its repo list
 * via `onConnected`.
 */
export function ConnectRepoForm({
  workspaceId,
  onConnected,
  className,
}: ConnectRepoFormProps) {
  const [source, setSource] = useState<SourceMode>("local");
  const [name, setName] = useState("");
  const [localPath, setLocalPath] = useState("");
  const [gitUrl, setGitUrl] = useState("");
  const [branch, setBranch] = useState("main");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const valid =
    Boolean(workspaceId) &&
    (source === "local" ? localPath.trim().length > 0 : gitUrl.trim().length > 0);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!workspaceId || !valid || submitting) return;
    setError(null);

    if (source === "local" && !localPath.trim().startsWith("/")) {
      setError("Local path must be absolute (start with “/”).");
      return;
    }

    const body: RepositoryConnectRequest = {
      workspace_id: workspaceId,
      name: name.trim() || null,
      branch: branch.trim() || "main",
      local_path: source === "local" ? localPath.trim() : null,
      git_url: source === "git" ? gitUrl.trim() : null,
    };

    setSubmitting(true);
    try {
      const repo = await connectRepository(body);
      onConnected(repo);
      // Reset the volatile fields; keep the branch + source for fast repeats.
      setName("");
      setLocalPath("");
      setGitUrl("");
    } catch (err) {
      const apiErr = err instanceof ApiError ? err : null;
      setError(
        apiErr?.isNetworkError
          ? "Backend unreachable — start the API and try again."
          : apiErr?.message ?? "Failed to connect the repository.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className={cn("space-y-3", className)}>
      <Tabs items={SOURCE_TABS} value={source} onValueChange={setSource} size="sm" />

      <div className="space-y-3 pt-1">
        {source === "local" ? (
          <Field
            label="Local path"
            icon={FolderGit2}
            hint="Absolute path on the API host or mounted volume."
          >
            <input
              value={localPath}
              onChange={(e) => setLocalPath(e.target.value)}
              placeholder="/workspaces/my-service"
              spellCheck={false}
              autoComplete="off"
              className={inputCls}
            />
          </Field>
        ) : (
          <Field
            label="Git URL"
            icon={Link2}
            hint="HTTPS or SSH clonable URL. Cloned into the workspaces dir."
          >
            <input
              value={gitUrl}
              onChange={(e) => setGitUrl(e.target.value)}
              placeholder="https://github.com/org/repo.git"
              spellCheck={false}
              autoComplete="off"
              className={inputCls}
            />
          </Field>
        )}

        <div className="grid grid-cols-2 gap-3">
          <Field label="Branch" icon={GitBranch}>
            <input
              value={branch}
              onChange={(e) => setBranch(e.target.value)}
              placeholder="main"
              spellCheck={false}
              autoComplete="off"
              className={inputCls}
            />
          </Field>
          <Field label="Name" hint="Optional — defaults to the directory name.">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="my-service"
              spellCheck={false}
              autoComplete="off"
              className={inputCls}
            />
          </Field>
        </div>
      </div>

      {error ? (
        <p className="rounded-md border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger">
          {error}
        </p>
      ) : null}

      <div className="flex items-center justify-between">
        <p className="text-2xs text-faint">
          {workspaceId
            ? "The repo is registered immediately; index it to make it searchable."
            : "Select a workspace first."}
        </p>
        <Button
          type="submit"
          variant="primary"
          size="sm"
          loading={submitting}
          disabled={!valid || submitting}
        >
          <Plus className="h-3.5 w-3.5" />
          Connect
        </Button>
      </div>
    </form>
  );
}

const inputCls =
  "w-full rounded-md border border-border-strong bg-surface-2 px-2.5 py-1.5 text-sm text-fg placeholder:text-faint outline-none focus-visible:ring-2 focus-visible:ring-accent/60 mono";

function Field({
  label,
  icon: Icon,
  hint,
  children,
}: {
  label: string;
  icon?: typeof GitBranch;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block space-y-1">
      <span className="flex items-center gap-1.5 text-2xs font-medium uppercase tracking-wider text-faint">
        {Icon ? <Icon className="h-3 w-3" /> : null}
        {label}
      </span>
      {children}
      {hint ? <span className="block text-2xs text-faint">{hint}</span> : null}
    </label>
  );
}
