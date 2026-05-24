"use client";

import { useMemo } from "react";
import { Check, Copy, FileCode2, Hash } from "lucide-react";

import { Drawer } from "@/components/ui/Drawer";
import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { useCopy } from "@/lib/useCopy";
import { cn, formatScore } from "@/lib/format";
import type { Citation, Evidence } from "@/lib/types";

export interface EvidenceDrawerProps {
  open: boolean;
  onClose: () => void;
  /** Full evidence list backing the answer (preferred — richer metadata). */
  evidence?: Evidence[];
  /** Citation list (used when evidence is unavailable). */
  citations?: Citation[];
  /** 1-based citation index to scroll/highlight on open. */
  focusIndex?: number | null;
}

/** A normalized, drawer-ready view of a single source. */
interface SourceItem {
  index: number;
  repository: string;
  filePath: string;
  startLine: number;
  endLine: number;
  snippet: string;
  score: number;
  language?: string | null;
  symbol?: string | null;
}

function toSources(
  evidence: Evidence[],
  citations: Citation[],
): SourceItem[] {
  if (evidence.length > 0) {
    return evidence.map((ev, i) => ({
      index: i + 1,
      repository: ev.metadata.repo_name,
      filePath: ev.metadata.file_path,
      startLine: ev.metadata.start_line,
      endLine: ev.metadata.end_line,
      snippet: ev.text,
      score: ev.score,
      language: ev.metadata.language,
      symbol: ev.metadata.symbol,
    }));
  }
  return citations.map((c) => ({
    index: c.index,
    repository: c.repository,
    filePath: c.file_path,
    startLine: c.start_line,
    endLine: c.end_line,
    snippet: c.snippet,
    score: c.score,
  }));
}

/**
 * Slide-over inspector listing the grounded sources behind an answer. Each
 * entry shows a `file_path:line` header, a relevance score, the symbol/language
 * when known, and a monospace snippet with line numbers. The chip the user
 * clicked is highlighted via `focusIndex`.
 */
export function EvidenceDrawer({
  open,
  onClose,
  evidence = [],
  citations = [],
  focusIndex = null,
}: EvidenceDrawerProps) {
  const sources = useMemo(
    () => toSources(evidence, citations),
    [evidence, citations],
  );

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title="Evidence"
      description={
        sources.length > 0
          ? `${sources.length} grounded source${sources.length === 1 ? "" : "s"}`
          : "No evidence attached"
      }
      widthClassName="w-full max-w-2xl"
    >
      <div className="p-4">
        {sources.length === 0 ? (
          <EmptyState
            icon={FileCode2}
            title="No evidence to show"
            description="This response was not grounded in indexed code. Try indexing the repository first, or widen the repo scope."
            compact
          />
        ) : (
          <ul className="space-y-3">
            {sources.map((src) => (
              <EvidenceItem
                key={`${src.index}-${src.filePath}-${src.startLine}`}
                src={src}
                focused={focusIndex === src.index}
              />
            ))}
          </ul>
        )}
      </div>
    </Drawer>
  );
}

function EvidenceItem({
  src,
  focused,
}: {
  src: SourceItem;
  focused: boolean;
}) {
  const { copied, copy } = useCopy();
  const locator = `${src.filePath}:${src.startLine}`;

  return (
    <li
      id={`evidence-${src.index}`}
      className={cn(
        "overflow-hidden rounded-lg border bg-surface-2 transition-colors",
        focused ? "border-accent/60 ring-1 ring-accent/40" : "border-border",
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between gap-2 border-b border-border bg-surface px-3 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-accent/15 text-2xs font-semibold text-accent">
            {src.index}
          </span>
          <span
            className="mono truncate text-xs text-fg"
            title={`${src.repository} · ${locator}`}
          >
            {src.filePath}
            <span className="text-faint">:{src.startLine}</span>
            {src.endLine > src.startLine ? (
              <span className="text-faint">-{src.endLine}</span>
            ) : null}
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <Badge tone="cyan" mono title="Relevance score">
            {formatScore(src.score)}
          </Badge>
          <button
            type="button"
            onClick={() => void copy(locator)}
            className="inline-flex items-center gap-1 rounded px-1.5 py-1 text-2xs text-muted transition-colors hover:bg-surface-2 hover:text-fg"
            title="Copy file path"
          >
            {copied ? (
              <Check className="h-3 w-3 text-ok" />
            ) : (
              <Copy className="h-3 w-3" />
            )}
            {copied ? "Copied" : "Path"}
          </button>
        </div>
      </div>

      {/* Meta row */}
      {(src.repository || src.symbol || src.language) && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-border px-3 py-1.5 text-2xs text-faint">
          {src.repository ? (
            <span className="inline-flex items-center gap-1">
              <Hash className="h-3 w-3" />
              <span className="mono text-muted">{src.repository}</span>
            </span>
          ) : null}
          {src.symbol ? (
            <span className="mono text-cyan">{src.symbol}()</span>
          ) : null}
          {src.language ? (
            <span className="uppercase tracking-wide">{src.language}</span>
          ) : null}
        </div>
      )}

      {/* Snippet */}
      <CodeSnippet text={src.snippet} startLine={src.startLine} />
    </li>
  );
}

function CodeSnippet({
  text,
  startLine,
}: {
  text: string;
  startLine: number;
}) {
  const lines = text.replace(/\n+$/, "").split("\n");
  return (
    <pre className="mono max-h-72 overflow-auto bg-bg/40 px-0 py-2 text-2xs leading-relaxed">
      <code className="block">
        {lines.map((line, i) => (
          <span key={i} className="flex">
            <span className="sticky left-0 w-10 shrink-0 select-none border-r border-border bg-surface px-2 text-right text-faint">
              {startLine + i}
            </span>
            <span className="whitespace-pre px-3 text-fg">{line || " "}</span>
          </span>
        ))}
      </code>
    </pre>
  );
}
