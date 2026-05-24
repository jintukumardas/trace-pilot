"use client";

import { Fragment, useMemo } from "react";
import {
  Activity,
  ArrowRight,
  Bot,
  CheckCircle2,
  User,
  Wrench,
  XCircle,
} from "lucide-react";

import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { cn, confidenceColor, formatDuration } from "@/lib/format";
import type {
  Citation,
  Confidence,
  Evidence,
  NextAction,
  ToolResult,
} from "@/lib/types";

/**
 * A normalized assistant or user turn rendered in the transcript. The
 * ChatPanel maps every response type (chat / debug / review) into this shape.
 */
export interface ChatTurn {
  id: string;
  role: "user" | "assistant";
  /** Rendered answer/summary text (may contain inline [n] citation markers). */
  content: string;
  confidence?: Confidence;
  /** Label shown next to the confidence dot, e.g. "Risk" for review mode. */
  confidenceLabel?: string;
  evidence?: Evidence[];
  citations?: Citation[];
  nextActions?: NextAction[];
  toolsUsed?: ToolResult[];
  warnings?: string[];
  traceId?: string | null;
  latencyMs?: number;
  /** True while the request is in flight (renders a typing indicator). */
  pending?: boolean;
  /** Set when the request failed; renders an error bubble. */
  error?: string | null;
}

export interface MessageBubbleProps {
  turn: ChatTurn;
  /** Open the evidence drawer, optionally focused on a citation index. */
  onOpenEvidence: (focusIndex?: number) => void;
  /** Open the diagnostics/trace drawer for this turn's trace_id. */
  onOpenTrace: (traceId: string) => void;
}

const CONFIDENCE_TONE: Record<Confidence, BadgeTone> = {
  high: "ok",
  medium: "warn",
  low: "danger",
};

export function MessageBubble({
  turn,
  onOpenEvidence,
  onOpenTrace,
}: MessageBubbleProps) {
  if (turn.role === "user") {
    return (
      <div className="flex justify-end gap-2.5">
        <div className="max-w-[80%] rounded-lg rounded-tr-sm border border-accent/30 bg-accent/10 px-3.5 py-2.5 text-sm text-fg">
          <p className="whitespace-pre-wrap break-words">{turn.content}</p>
        </div>
        <Avatar role="user" />
      </div>
    );
  }

  return (
    <div className="flex gap-2.5">
      <Avatar role="assistant" />
      <div className="min-w-0 max-w-[85%] flex-1 space-y-2.5">
        <div
          className={cn(
            "rounded-lg rounded-tl-sm border bg-surface px-3.5 py-3",
            turn.error ? "border-danger/40" : "border-border",
          )}
        >
          {turn.pending ? (
            <TypingIndicator />
          ) : turn.error ? (
            <p className="text-sm text-danger">{turn.error}</p>
          ) : (
            <AnswerBody
              content={turn.content}
              citationCount={
                (turn.evidence?.length ?? 0) || (turn.citations?.length ?? 0)
              }
              onCite={onOpenEvidence}
            />
          )}

          {/* Warnings */}
          {turn.warnings && turn.warnings.length > 0 ? (
            <ul className="mt-2.5 space-y-1 border-t border-border pt-2.5">
              {turn.warnings.map((w, i) => (
                <li key={i} className="text-2xs text-warn">
                  ⚠ {w}
                </li>
              ))}
            </ul>
          ) : null}
        </div>

        {/* Meta row: confidence, latency, evidence + diagnostics */}
        {!turn.pending && !turn.error ? (
          <MetaRow
            turn={turn}
            onOpenEvidence={onOpenEvidence}
            onOpenTrace={onOpenTrace}
          />
        ) : null}

        {/* Tools used */}
        {turn.toolsUsed && turn.toolsUsed.length > 0 ? (
          <ToolsUsed tools={turn.toolsUsed} />
        ) : null}

        {/* Next actions */}
        {turn.nextActions && turn.nextActions.length > 0 ? (
          <NextActions actions={turn.nextActions} />
        ) : null}
      </div>
    </div>
  );
}

function Avatar({ role }: { role: "user" | "assistant" }) {
  return (
    <span
      className={cn(
        "mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md border",
        role === "assistant"
          ? "border-accent/40 bg-accent/15 text-accent"
          : "border-border-strong bg-surface-2 text-muted",
      )}
    >
      {role === "assistant" ? (
        <Bot className="h-4 w-4" />
      ) : (
        <User className="h-4 w-4" />
      )}
    </span>
  );
}

/**
 * Renders answer text, replacing inline `[n]` markers with clickable citation
 * chips that open the evidence drawer at that index. Falls back to plain text
 * when no citations are available.
 */
function AnswerBody({
  content,
  citationCount,
  onCite,
}: {
  content: string;
  citationCount: number;
  onCite: (focusIndex?: number) => void;
}) {
  const parts = useMemo(
    () => splitCitations(content, citationCount),
    [content, citationCount],
  );

  return (
    <div className="space-y-2 text-sm leading-relaxed text-fg">
      {parts.paragraphs.map((para, pi) => (
        <p key={pi} className="whitespace-pre-wrap break-words">
          {para.map((seg, si) =>
            seg.type === "text" ? (
              <Fragment key={si}>{seg.value}</Fragment>
            ) : (
              <button
                key={si}
                type="button"
                onClick={() => onCite(seg.index)}
                className="mx-0.5 inline-flex h-4 min-w-4 items-center justify-center rounded bg-accent/15 px-1 align-baseline text-2xs font-semibold text-accent transition-colors hover:bg-accent/25"
                title={`Open evidence [${seg.index}]`}
              >
                {seg.index}
              </button>
            ),
          )}
        </p>
      ))}
    </div>
  );
}

type Segment =
  | { type: "text"; value: string }
  | { type: "cite"; index: number };

function splitCitations(
  content: string,
  citationCount: number,
): { paragraphs: Segment[][] } {
  const paragraphs = content.split(/\n{2,}/);
  const out: Segment[][] = paragraphs.map((para) => {
    const segments: Segment[] = [];
    const re = /\[(\d{1,3})\]/g;
    let last = 0;
    let m: RegExpExecArray | null;
    while ((m = re.exec(para)) !== null) {
      const idx = Number(m[1]);
      // Only linkify markers that actually reference an available source.
      if (citationCount === 0 || idx < 1 || idx > citationCount) continue;
      if (m.index > last) {
        segments.push({ type: "text", value: para.slice(last, m.index) });
      }
      segments.push({ type: "cite", index: idx });
      last = m.index + m[0].length;
    }
    if (last < para.length) {
      segments.push({ type: "text", value: para.slice(last) });
    }
    return segments.length > 0 ? segments : [{ type: "text", value: para }];
  });
  return { paragraphs: out };
}

function MetaRow({
  turn,
  onOpenEvidence,
  onOpenTrace,
}: {
  turn: ChatTurn;
  onOpenEvidence: (focusIndex?: number) => void;
  onOpenTrace: (traceId: string) => void;
}) {
  const evidenceCount =
    (turn.evidence?.length ?? 0) || (turn.citations?.length ?? 0);
  const conf = turn.confidence;
  const confMeta = conf ? confidenceColor(conf) : null;

  return (
    <div className="flex flex-wrap items-center gap-2 pl-0.5">
      {conf && confMeta ? (
        <Badge tone={CONFIDENCE_TONE[conf]} dot title="Model confidence">
          {turn.confidenceLabel ?? "Confidence"}: {confMeta.label}
        </Badge>
      ) : null}

      {turn.latencyMs ? (
        <span className="mono text-2xs text-faint">
          {formatDuration(turn.latencyMs)}
        </span>
      ) : null}

      {evidenceCount > 0 ? (
        <button
          type="button"
          onClick={() => onOpenEvidence()}
          className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-2xs font-medium text-cyan transition-colors hover:bg-cyan/10"
        >
          {evidenceCount} source{evidenceCount === 1 ? "" : "s"}
        </button>
      ) : null}

      {turn.traceId ? (
        <button
          type="button"
          onClick={() => onOpenTrace(turn.traceId!)}
          className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-2xs font-medium text-muted transition-colors hover:bg-surface-2 hover:text-fg"
          title="Open response trace"
        >
          <Activity className="h-3 w-3" />
          Diagnostics
        </button>
      ) : null}
    </div>
  );
}

function ToolsUsed({ tools }: { tools: ToolResult[] }) {
  return (
    <div className="rounded-md border border-border bg-surface-2 px-3 py-2">
      <div className="mb-1.5 flex items-center gap-1.5 text-2xs font-medium uppercase tracking-wider text-faint">
        <Wrench className="h-3 w-3" />
        Tools used
      </div>
      <ul className="space-y-1">
        {tools.map((tool) => (
          <li
            key={tool.id}
            className="flex items-center gap-2 text-2xs text-muted"
          >
            {tool.ok ? (
              <CheckCircle2 className="h-3 w-3 shrink-0 text-ok" />
            ) : (
              <XCircle className="h-3 w-3 shrink-0 text-danger" />
            )}
            <span className="mono text-fg">{tool.tool}</span>
            {tool.duration_ms ? (
              <span className="text-faint">
                {formatDuration(tool.duration_ms)}
              </span>
            ) : null}
            {tool.error ? (
              <span className="truncate text-danger" title={tool.error}>
                {tool.error}
              </span>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

function NextActions({ actions }: { actions: NextAction[] }) {
  return (
    <div className="rounded-md border border-border bg-surface-2 px-3 py-2.5">
      <div className="mb-2 text-2xs font-medium uppercase tracking-wider text-faint">
        Suggested next steps
      </div>
      <ul className="space-y-1.5">
        {actions.map((action, i) => (
          <li key={i} className="flex gap-2">
            <ArrowRight className="mt-0.5 h-3.5 w-3.5 shrink-0 text-accent" />
            <div className="min-w-0">
              <p className="text-xs font-medium text-fg">{action.title}</p>
              {action.detail ? (
                <p className="text-2xs leading-relaxed text-muted">
                  {action.detail}
                </p>
              ) : null}
              {action.rationale ? (
                <p className="mt-0.5 text-2xs italic text-faint">
                  {action.rationale}
                </p>
              ) : null}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function TypingIndicator() {
  return (
    <div className="flex items-center gap-2 text-sm text-muted">
      <span className="flex gap-1">
        {[0, 1, 2].map((i) => (
          <span
            key={i}
            className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent"
            style={{ animationDelay: `${i * 150}ms` }}
          />
        ))}
      </span>
      <span className="text-xs text-faint">Investigating…</span>
    </div>
  );
}
