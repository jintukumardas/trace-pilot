"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Check, ChevronDown, Layers, MessageSquare } from "lucide-react";

import { useWorkspace } from "@/components/WorkspaceProvider";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { ModeSelector } from "@/components/chat/ModeSelector";
import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { cn } from "@/lib/format";
import type { ChatMode, Repository } from "@/lib/types";

const VALID_MODES: ChatMode[] = [
  "ask",
  "onboard",
  "debug",
  "change_review",
  "fix_plan",
];

export default function ChatPage() {
  return (
    <Suspense fallback={null}>
      <ChatPageBody />
    </Suspense>
  );
}

function ChatPageBody() {
  const { workspace, repositories, offline } = useWorkspace();
  const searchParams = useSearchParams();

  const [mode, setMode] = useState<ChatMode>("ask");
  const [selectedRepos, setSelectedRepos] = useState<string[]>([]);

  // Seed mode + repo scope from the URL (deep links from the dashboard).
  useEffect(() => {
    const m = searchParams.get("mode");
    if (m && (VALID_MODES as string[]).includes(m)) {
      setMode(m as ChatMode);
    }
  }, [searchParams]);

  useEffect(() => {
    const repoParam = searchParams.get("repo");
    if (repoParam && repositories.some((r) => r.id === repoParam)) {
      setSelectedRepos([repoParam]);
    }
  }, [searchParams, repositories]);

  // Drop selections that no longer exist after a workspace switch.
  useEffect(() => {
    setSelectedRepos((prev) =>
      prev.filter((id) => repositories.some((r) => r.id === id)),
    );
  }, [repositories]);

  // change_review needs exactly one repo — collapse the scope when entering it.
  useEffect(() => {
    if (mode === "change_review" && selectedRepos.length > 1) {
      setSelectedRepos((prev) => (prev[0] ? [prev[0]] : []));
    }
  }, [mode, selectedRepos.length]);

  const workspaceId = workspace?.id ?? null;

  if (!workspaceId) {
    return (
      <div className="mx-auto max-w-3xl px-6 py-10">
        <EmptyState
          icon={MessageSquare}
          title="No workspace selected"
          description={
            offline
              ? "Start the TracePilot API and select a workspace to begin."
              : "Select or create a workspace to start an investigation."
          }
        />
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Controls bar */}
      <div className="shrink-0 border-b border-border bg-surface/60 px-4 py-3 backdrop-blur">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-3">
          <ModeSelector value={mode} onChange={setMode} />
          <RepoScopeSelect
            repositories={repositories}
            selected={selectedRepos}
            onChange={setSelectedRepos}
            single={mode === "change_review"}
          />
        </div>
      </div>

      {/* Conversation surface */}
      <ChatPanel
        key={`${workspaceId}-${mode}`}
        workspaceId={workspaceId}
        repositoryIds={selectedRepos}
        mode={mode}
        className="min-h-0 flex-1"
      />
    </div>
  );
}

/**
 * Repository scope control. Multi-select by default; collapses to single-select
 * semantics for change-review mode (which targets exactly one repo). An empty
 * selection means "search all repositories in the workspace".
 */
function RepoScopeSelect({
  repositories,
  selected,
  onChange,
  single,
}: {
  repositories: Repository[];
  selected: string[];
  onChange: (ids: string[]) => void;
  single: boolean;
}) {
  const [open, setOpen] = useState(false);

  const label = useMemo(() => {
    if (selected.length === 0) return single ? "Pick a repository" : "All repositories";
    if (selected.length === 1) {
      return repositories.find((r) => r.id === selected[0])?.name ?? "1 repo";
    }
    return `${selected.length} repositories`;
  }, [selected, repositories, single]);

  const toggle = (id: string) => {
    if (single) {
      onChange(selected[0] === id ? [] : [id]);
      setOpen(false);
      return;
    }
    onChange(
      selected.includes(id)
        ? selected.filter((s) => s !== id)
        : [...selected, id],
    );
  };

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        disabled={repositories.length === 0}
        className={cn(
          "inline-flex items-center gap-2 rounded-md border border-border-strong bg-surface-2 px-2.5 py-1.5 text-xs font-medium text-fg transition-colors",
          "hover:bg-elevated disabled:cursor-not-allowed disabled:opacity-50",
        )}
      >
        <Layers className="h-3.5 w-3.5 text-faint" />
        <span className="max-w-[12rem] truncate">{label}</span>
        {!single && selected.length > 0 ? (
          <Badge tone="accent">{selected.length}</Badge>
        ) : null}
        <ChevronDown className="h-3.5 w-3.5 text-faint" />
      </button>

      {open ? (
        <>
          <button
            type="button"
            aria-label="Close"
            className="fixed inset-0 z-10 cursor-default"
            onClick={() => setOpen(false)}
          />
          <div className="absolute right-0 z-20 mt-1 max-h-72 w-64 overflow-y-auto rounded-md border border-border-strong bg-surface shadow-drawer">
            <div className="border-b border-border px-3 py-2 text-2xs uppercase tracking-wider text-faint">
              {single ? "Select one repository" : "Scope · multi-select"}
            </div>
            {repositories.length === 0 ? (
              <p className="px-3 py-3 text-xs text-muted">
                No repositories connected.
              </p>
            ) : (
              <ul className="py-1">
                {!single ? (
                  <li>
                    <button
                      type="button"
                      onClick={() => onChange([])}
                      className="flex w-full items-center justify-between px-3 py-1.5 text-left text-xs text-muted hover:bg-surface-2 hover:text-fg"
                    >
                      All repositories
                      {selected.length === 0 ? (
                        <Check className="h-3.5 w-3.5 text-accent" />
                      ) : null}
                    </button>
                  </li>
                ) : null}
                {repositories.map((repo) => {
                  const checked = selected.includes(repo.id);
                  return (
                    <li key={repo.id}>
                      <button
                        type="button"
                        onClick={() => toggle(repo.id)}
                        className="flex w-full items-center justify-between gap-2 px-3 py-1.5 text-left text-xs hover:bg-surface-2"
                      >
                        <span className="mono min-w-0 truncate text-fg">
                          {repo.name}
                        </span>
                        {checked ? (
                          <Check className="h-3.5 w-3.5 shrink-0 text-accent" />
                        ) : null}
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </>
      ) : null}
    </div>
  );
}
