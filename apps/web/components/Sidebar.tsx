"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  ChevronsLeft,
  ChevronsRight,
  Database,
  FlaskConical,
  GitBranch,
  LayoutDashboard,
  MessageSquare,
  Settings,
  type LucideIcon,
} from "lucide-react";

import { useWorkspace } from "@/components/WorkspaceProvider";
import { Spinner } from "@/components/ui/Spinner";
import { cn } from "@/lib/format";
import type { RepoStatus, Repository } from "@/lib/types";

interface NavLink {
  href: string;
  label: string;
  icon: LucideIcon;
}

const NAV: NavLink[] = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/chat", label: "Copilot", icon: MessageSquare },
  { href: "/ingestion", label: "Ingestion", icon: Database },
  { href: "/evaluations", label: "Evaluations", icon: FlaskConical },
  { href: "/settings", label: "Settings", icon: Settings },
];

const STATUS_DOT: Record<RepoStatus, { color: string; title: string }> = {
  indexed: { color: "bg-ok", title: "Indexed" },
  indexing: { color: "bg-warn animate-pulse", title: "Indexing" },
  registered: { color: "bg-faint", title: "Registered (not indexed)" },
  error: { color: "bg-danger", title: "Error" },
};

function isActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function Sidebar() {
  const pathname = usePathname();
  const {
    workspaces,
    workspace,
    repositories,
    loading,
    reposLoading,
    selectWorkspace,
  } = useWorkspace();
  const [collapsed, setCollapsed] = useState(false);

  return (
    <aside
      className={cn(
        "flex h-screen shrink-0 flex-col border-r border-border bg-surface transition-[width] duration-150",
        collapsed ? "w-14" : "w-64",
      )}
    >
      {/* Brand */}
      <div className="flex h-12 items-center gap-2 border-b border-border px-3">
        <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded bg-accent/20 text-accent">
          <GitBranch className="h-3.5 w-3.5" />
        </div>
        {!collapsed && (
          <span className="truncate text-sm font-semibold tracking-tight text-fg">
            TracePilot
          </span>
        )}
      </div>

      {/* Workspace selector */}
      {!collapsed && (
        <div className="border-b border-border px-3 py-3">
          <label
            htmlFor="workspace-select"
            className="mb-1 block text-2xs font-medium uppercase tracking-wider text-faint"
          >
            Workspace
          </label>
          {loading ? (
            <Spinner size="sm" label="Loading…" />
          ) : workspaces.length === 0 ? (
            <p className="text-xs text-muted">No workspaces</p>
          ) : (
            <select
              id="workspace-select"
              value={workspace?.id ?? ""}
              onChange={(e) => selectWorkspace(e.target.value)}
              className="w-full rounded-md border border-border-strong bg-surface-2 px-2 py-1.5 text-sm text-fg outline-none focus-visible:ring-2 focus-visible:ring-accent/70"
            >
              {workspaces.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name}
                </option>
              ))}
            </select>
          )}
        </div>
      )}

      {/* Primary nav */}
      <nav className="flex flex-col gap-0.5 px-2 py-3">
        {NAV.map((item) => {
          const active = isActive(pathname, item.href);
          const Icon = item.icon;
          return (
            <Link
              key={item.href}
              href={item.href}
              title={collapsed ? item.label : undefined}
              className={cn(
                "group flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm font-medium transition-colors",
                collapsed && "justify-center px-0",
                active
                  ? "bg-accent/15 text-accent"
                  : "text-muted hover:bg-surface-2 hover:text-fg",
              )}
            >
              <Icon className="h-4 w-4 shrink-0" />
              {!collapsed && <span className="truncate">{item.label}</span>}
            </Link>
          );
        })}
      </nav>

      {/* Repository list */}
      {!collapsed && (
        <div className="flex min-h-0 flex-1 flex-col border-t border-border px-2 py-3">
          <div className="flex items-center justify-between px-1.5 pb-2">
            <span className="text-2xs font-medium uppercase tracking-wider text-faint">
              Repositories
            </span>
            {reposLoading && <Spinner size="sm" />}
          </div>
          <div className="min-h-0 flex-1 space-y-0.5 overflow-y-auto">
            {repositories.length === 0 && !reposLoading ? (
              <p className="px-1.5 py-2 text-xs text-faint">
                No repositories connected.
              </p>
            ) : (
              repositories.map((repo) => (
                <RepoRow key={repo.id} repo={repo} pathname={pathname} />
              ))
            )}
          </div>
        </div>
      )}

      {/* Collapse toggle */}
      <div className="mt-auto border-t border-border p-2">
        <button
          type="button"
          onClick={() => setCollapsed((c) => !c)}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          className={cn(
            "flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-xs font-medium text-muted transition-colors hover:bg-surface-2 hover:text-fg",
            collapsed && "justify-center px-0",
          )}
        >
          {collapsed ? (
            <ChevronsRight className="h-4 w-4" />
          ) : (
            <>
              <ChevronsLeft className="h-4 w-4" />
              <span>Collapse</span>
            </>
          )}
        </button>
      </div>
    </aside>
  );
}

function RepoRow({ repo, pathname }: { repo: Repository; pathname: string }) {
  const href = `/repositories/${repo.id}`;
  const active = isActive(pathname, href);
  const dot = STATUS_DOT[repo.status] ?? STATUS_DOT.registered;
  return (
    <Link
      href={href}
      className={cn(
        "flex items-center gap-2 rounded-md px-1.5 py-1.5 text-sm transition-colors",
        active
          ? "bg-surface-2 text-fg"
          : "text-muted hover:bg-surface-2 hover:text-fg",
      )}
    >
      <span
        className={cn("h-2 w-2 shrink-0 rounded-full", dot.color)}
        title={dot.title}
        aria-label={dot.title}
      />
      <span className="mono truncate text-xs">{repo.name}</span>
    </Link>
  );
}
