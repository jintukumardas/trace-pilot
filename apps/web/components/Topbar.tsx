"use client";

import Link from "next/link";
import { useSelectedLayoutSegments } from "next/navigation";
import { useMemo } from "react";
import { ExternalLink, Settings, WifiOff } from "lucide-react";

import { useWorkspace } from "@/components/WorkspaceProvider";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { cn } from "@/lib/format";
import type { RepoStatus } from "@/lib/types";

const LANGFUSE_URL =
  process.env.NEXT_PUBLIC_LANGFUSE_URL ?? "http://localhost:3001";

const STATUS_TONE: Record<RepoStatus, { tone: BadgeTone; label: string }> = {
  indexed: { tone: "ok", label: "Indexed" },
  indexing: { tone: "warn", label: "Indexing" },
  registered: { tone: "neutral", label: "Not indexed" },
  error: { tone: "danger", label: "Index error" },
};

/** Derive a human page label from the active route segments. */
function usePageLabel(): string {
  const segments = useSelectedLayoutSegments();
  return useMemo(() => {
    const first = segments[0];
    switch (first) {
      case undefined:
        return "Dashboard";
      case "chat":
        return "Copilot";
      case "ingestion":
        return "Ingestion";
      case "evaluations":
        return "Evaluations";
      case "settings":
        return "Settings";
      case "repositories":
        return "Repository";
      default:
        return first.charAt(0).toUpperCase() + first.slice(1);
    }
  }, [segments]);
}

export function Topbar() {
  const { workspace, repositories, offline } = useWorkspace();
  const pageLabel = usePageLabel();

  // Aggregate ingestion status across the workspace's repositories.
  const ingestion = useMemo(() => {
    if (repositories.length === 0) return null;
    if (repositories.some((r) => r.status === "error")) return "error" as const;
    if (repositories.some((r) => r.status === "indexing"))
      return "indexing" as const;
    if (repositories.every((r) => r.status === "indexed"))
      return "indexed" as const;
    return "registered" as const;
  }, [repositories]);

  const indexedCount = repositories.filter(
    (r) => r.status === "indexed",
  ).length;

  return (
    <header className="flex h-12 shrink-0 items-center justify-between gap-4 border-b border-border bg-surface/80 px-4 backdrop-blur">
      {/* Breadcrumb / context */}
      <div className="flex min-w-0 items-center gap-2 text-sm">
        <span className="truncate font-medium text-fg">{pageLabel}</span>
        {workspace && (
          <>
            <span className="text-faint">/</span>
            <span className="mono truncate text-xs text-muted">
              {workspace.slug}
            </span>
          </>
        )}
      </div>

      {/* Right cluster */}
      <div className="flex items-center gap-2">
        {offline ? (
          <Badge tone="danger" dot>
            <WifiOff className="h-3 w-3" />
            API offline
          </Badge>
        ) : ingestion ? (
          <Badge tone={STATUS_TONE[ingestion].tone} dot>
            {STATUS_TONE[ingestion].label}
            <span className="text-faint">
              · {indexedCount}/{repositories.length}
            </span>
          </Badge>
        ) : (
          <Badge tone="neutral">No repos</Badge>
        )}

        <span className="mx-1 h-5 w-px bg-border" aria-hidden />

        <a
          href={LANGFUSE_URL}
          target="_blank"
          rel="noreferrer noopener"
          className={cn(
            "inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium text-muted transition-colors",
            "hover:bg-surface-2 hover:text-fg",
          )}
          title="Open Langfuse traces"
        >
          Langfuse
          <ExternalLink className="h-3 w-3" />
        </a>

        <Link
          href="/settings"
          aria-label="Settings"
          className="inline-flex h-8 w-8 items-center justify-center rounded-md text-muted transition-colors hover:bg-surface-2 hover:text-fg"
        >
          <Settings className="h-4 w-4" />
        </Link>
      </div>
    </header>
  );
}
