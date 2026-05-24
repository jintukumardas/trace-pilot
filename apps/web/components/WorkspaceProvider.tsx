"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import { ApiError, listRepositories, listWorkspaces } from "@/lib/api";
import type { Repository, Workspace } from "@/lib/types";

const SELECTED_WORKSPACE_KEY = "tracepilot.selectedWorkspaceId";

export interface WorkspaceContextValue {
  /** All workspaces returned by the backend (empty if none/unreachable). */
  workspaces: Workspace[];
  /** The currently selected workspace, if any. */
  workspace: Workspace | null;
  /** Repositories belonging to the selected workspace. */
  repositories: Repository[];
  /** True during the initial fetch. */
  loading: boolean;
  /** True while re-fetching repositories after a workspace switch. */
  reposLoading: boolean;
  /** Non-fatal warning when the backend is empty or unreachable. */
  error: string | null;
  /** True when the API could not be reached at all. */
  offline: boolean;
  selectWorkspace: (id: string) => void;
  /** Re-fetch workspaces + repositories from the API. */
  refresh: () => Promise<void>;
  /** Re-fetch only the repositories for the active workspace. */
  refreshRepositories: () => Promise<void>;
}

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

function readStoredWorkspaceId(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(SELECTED_WORKSPACE_KEY);
  } catch {
    return null;
  }
}

function persistWorkspaceId(id: string | null): void {
  if (typeof window === "undefined") return;
  try {
    if (id) window.localStorage.setItem(SELECTED_WORKSPACE_KEY, id);
    else window.localStorage.removeItem(SELECTED_WORKSPACE_KEY);
  } catch {
    /* storage may be unavailable (private mode); ignore */
  }
}

export interface WorkspaceProviderProps {
  children: React.ReactNode;
  /** Optionally seed from a server-rendered fetch for a faster first paint. */
  initialWorkspaces?: Workspace[];
}

/**
 * Holds the selected workspace + its repositories and exposes them via
 * `useWorkspace()`. Tolerant of an empty or unreachable backend: failures
 * surface as a soft `error`/`offline` flag rather than throwing.
 */
export function WorkspaceProvider({
  children,
  initialWorkspaces = [],
}: WorkspaceProviderProps) {
  const [workspaces, setWorkspaces] = useState<Workspace[]>(initialWorkspaces);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [repositories, setRepositories] = useState<Repository[]>([]);
  const [loading, setLoading] = useState(initialWorkspaces.length === 0);
  const [reposLoading, setReposLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [offline, setOffline] = useState(false);

  const resolveSelectedId = useCallback(
    (list: Workspace[]): string | null => {
      if (list.length === 0) return null;
      const stored = readStoredWorkspaceId();
      if (stored && list.some((w) => w.id === stored)) return stored;
      return list[0]?.id ?? null;
    },
    [],
  );

  const loadWorkspaces = useCallback(async () => {
    setLoading(true);
    try {
      const ws = await listWorkspaces();
      setWorkspaces(ws);
      setOffline(false);
      setError(ws.length === 0 ? "No workspaces yet. Create one to begin." : null);
      setSelectedId((current) => {
        if (current && ws.some((w) => w.id === current)) return current;
        return resolveSelectedId(ws);
      });
    } catch (err) {
      const apiErr = err instanceof ApiError ? err : null;
      setWorkspaces([]);
      setOffline(apiErr?.isNetworkError ?? true);
      setError(
        apiErr?.isNetworkError
          ? "Backend unreachable — start the API to load workspaces."
          : apiErr?.message ?? "Failed to load workspaces.",
      );
    } finally {
      setLoading(false);
    }
  }, [resolveSelectedId]);

  const loadRepositories = useCallback(async (workspaceId: string | null) => {
    if (!workspaceId) {
      setRepositories([]);
      return;
    }
    setReposLoading(true);
    try {
      const repos = await listRepositories(workspaceId);
      setRepositories(repos);
    } catch {
      // Repo fetch failures are non-fatal; keep the shell usable.
      setRepositories([]);
    } finally {
      setReposLoading(false);
    }
  }, []);

  // Initial workspace load on mount.
  useEffect(() => {
    void loadWorkspaces();
  }, [loadWorkspaces]);

  // Re-fetch repositories whenever the selected workspace changes.
  useEffect(() => {
    void loadRepositories(selectedId);
  }, [selectedId, loadRepositories]);

  const selectWorkspace = useCallback((id: string) => {
    setSelectedId(id);
    persistWorkspaceId(id);
  }, []);

  const refresh = useCallback(async () => {
    await loadWorkspaces();
    await loadRepositories(selectedId);
  }, [loadWorkspaces, loadRepositories, selectedId]);

  const refreshRepositories = useCallback(
    () => loadRepositories(selectedId),
    [loadRepositories, selectedId],
  );

  const workspace = useMemo(
    () => workspaces.find((w) => w.id === selectedId) ?? null,
    [workspaces, selectedId],
  );

  const value = useMemo<WorkspaceContextValue>(
    () => ({
      workspaces,
      workspace,
      repositories,
      loading,
      reposLoading,
      error,
      offline,
      selectWorkspace,
      refresh,
      refreshRepositories,
    }),
    [
      workspaces,
      workspace,
      repositories,
      loading,
      reposLoading,
      error,
      offline,
      selectWorkspace,
      refresh,
      refreshRepositories,
    ],
  );

  return (
    <WorkspaceContext.Provider value={value}>
      {children}
    </WorkspaceContext.Provider>
  );
}

/** Access the workspace context. Throws if used outside the provider. */
export function useWorkspace(): WorkspaceContextValue {
  const ctx = useContext(WorkspaceContext);
  if (!ctx) {
    throw new Error("useWorkspace must be used within a <WorkspaceProvider>");
  }
  return ctx;
}
