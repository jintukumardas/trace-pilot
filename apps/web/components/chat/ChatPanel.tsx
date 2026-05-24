"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { CornerDownLeft, Eraser, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import {
  MessageBubble,
  type ChatTurn,
} from "@/components/chat/MessageBubble";
import { EvidenceDrawer } from "@/components/EvidenceDrawer";
import { TraceDrawer } from "@/components/TraceDrawer";
import {
  ApiError,
  chatQuery,
  debugInvestigate,
  reviewDiff,
} from "@/lib/api";
import type {
  ChatMessage,
  ChatMode,
  ChatResponse,
  Citation,
  DebugResponse,
  DiffReviewResponse,
  Evidence,
  NextAction,
} from "@/lib/types";

let _seq = 0;
function nextId(prefix: string): string {
  _seq += 1;
  return `${prefix}-${Date.now()}-${_seq}`;
}

const SUGGESTIONS: Record<ChatMode, string[]> = {
  ask: [
    "How does request authentication work?",
    "Where is the retrieval pipeline configured?",
  ],
  onboard: [
    "Give me a tour of this codebase's architecture.",
    "What are the main entry points and how do they connect?",
  ],
  debug: [
    "Users get a 500 on /chat/query intermittently — what could cause it?",
    "NullPointer in the indexing worker, paste a stack trace below.",
  ],
  change_review: [
    "Review the impact of the diff between main and my feature branch.",
    "What's the blast radius of changing the embedding dimension?",
  ],
  fix_plan: [
    "Draft a fix plan for the flaky retrieval timeout.",
    "Plan the migration to incremental indexing.",
  ],
};

export interface ChatPanelProps {
  workspaceId: string | null;
  repositoryIds: string[];
  mode: ChatMode;
  branch?: string | null;
  topK?: number;
  /** Show the evidence drawer toggle in the parent (controlled by callbacks). */
  className?: string;
}

/** Map a chat-mode response into the normalized transcript turn. */
function fromChatResponse(id: string, resp: ChatResponse): ChatTurn {
  return {
    id,
    role: "assistant",
    content: resp.answer || "(no answer returned)",
    confidence: resp.confidence,
    evidence: resp.evidence,
    citations: resp.citations,
    nextActions: resp.next_actions,
    toolsUsed: resp.tools_used,
    warnings: resp.warnings,
    traceId: resp.trace_id ?? null,
    latencyMs: resp.latency_ms,
  };
}

function fromDebugResponse(id: string, resp: DebugResponse): ChatTurn {
  const lines: string[] = [];
  if (resp.summary) lines.push(resp.summary);
  if (resp.root_cause_candidates.length > 0) {
    lines.push("\nRoot-cause candidates:");
    resp.root_cause_candidates.forEach((rc, i) => {
      const refs = rc.evidence_indices
        .map((n) => `[${n + 1}]`)
        .join(" ");
      lines.push(
        `${i + 1}. (${rc.confidence}) ${rc.hypothesis}${refs ? ` ${refs}` : ""}`,
      );
      if (rc.reasoning) lines.push(`   ${rc.reasoning}`);
    });
  }
  if (resp.diagnostic_steps.length > 0) {
    lines.push("\nDiagnostic steps:");
    resp.diagnostic_steps.forEach((s, i) => lines.push(`${i + 1}. ${s}`));
  }
  if (resp.impacted_files.length > 0) {
    lines.push(`\nImpacted files: ${resp.impacted_files.join(", ")}`);
  }

  const nextActions: NextAction[] = [];
  if (resp.fix_plan) {
    resp.fix_plan.steps.forEach((step, i) =>
      nextActions.push({
        title: `Fix step ${i + 1}`,
        detail: step,
        rationale: "",
      }),
    );
    if (resp.fix_plan.test_strategy.length > 0) {
      nextActions.push({
        title: "Test strategy",
        detail: resp.fix_plan.test_strategy.join("; "),
        rationale: "",
      });
    }
    if (resp.fix_plan.rollback) {
      nextActions.push({
        title: "Rollback",
        detail: resp.fix_plan.rollback,
        rationale: "",
      });
    }
  }

  return {
    id,
    role: "assistant",
    content: lines.join("\n"),
    confidence: resp.confidence,
    confidenceLabel: "Confidence",
    evidence: resp.evidence,
    citations: resp.citations,
    nextActions,
    toolsUsed: resp.tools_used,
    traceId: resp.trace_id ?? null,
    latencyMs: resp.latency_ms,
  };
}

function fromReviewResponse(id: string, resp: DiffReviewResponse): ChatTurn {
  const lines: string[] = [];
  if (resp.summary) lines.push(resp.summary);
  if (resp.impact) lines.push(`\nImpact: ${resp.impact}`);
  if (resp.affected_areas.length > 0) {
    lines.push(`\nAffected areas: ${resp.affected_areas.join(", ")}`);
  }
  const nextActions: NextAction[] = resp.suggested_tests.map((t) => ({
    title: "Suggested test",
    detail: t,
    rationale: "",
  }));
  return {
    id,
    role: "assistant",
    content: lines.join("\n"),
    confidence: resp.risk_level,
    confidenceLabel: "Risk",
    evidence: resp.evidence,
    citations: resp.citations,
    nextActions,
    traceId: resp.trace_id ?? null,
    latencyMs: resp.latency_ms,
  };
}

/**
 * The core investigation surface. Owns the transcript + input, dispatches to
 * the correct backend endpoint based on the active mode, and renders the
 * Evidence and Trace drawers. Conversation history (user/assistant text) is
 * threaded back into chat-mode requests so follow-ups stay contextual.
 */
export function ChatPanel({
  workspaceId,
  repositoryIds,
  mode,
  branch,
  topK = 8,
  className,
}: ChatPanelProps) {
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);

  // Drawer state.
  const [evidenceTurnId, setEvidenceTurnId] = useState<string | null>(null);
  const [evidenceFocus, setEvidenceFocus] = useState<number | null>(null);
  const [traceId, setTraceId] = useState<string | null>(null);

  const scrollRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-scroll to the latest turn.
  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [turns]);

  const evidenceTurn = useMemo(
    () => turns.find((t) => t.id === evidenceTurnId) ?? null,
    [turns, evidenceTurnId],
  );

  const send = useCallback(
    async (raw: string) => {
      const message = raw.trim();
      if (!message || busy) return;
      if (!workspaceId) return;

      const userTurn: ChatTurn = {
        id: nextId("user"),
        role: "user",
        content: message,
      };
      const pendingId = nextId("assistant");
      const pendingTurn: ChatTurn = {
        id: pendingId,
        role: "assistant",
        content: "",
        pending: true,
      };

      // Build chat history from prior text turns (excludes the new ones).
      const history: ChatMessage[] = turns
        .filter((t) => !t.pending && !t.error && t.content)
        .map((t) => ({
          role: t.role === "assistant" ? "assistant" : "user",
          content: t.content,
        }));

      setTurns((prev) => [...prev, userTurn, pendingTurn]);
      setInput("");
      setBusy(true);

      try {
        let resolved: ChatTurn;
        if (mode === "debug") {
          const resp = await debugInvestigate({
            workspace_id: workspaceId,
            repository_ids: repositoryIds,
            bug_report: message,
            branch: branch ?? null,
          });
          resolved = fromDebugResponse(pendingId, resp);
        } else if (mode === "change_review") {
          const repoId = repositoryIds[0];
          if (!repoId) {
            throw new ApiError({
              message:
                "Change review needs exactly one repository selected in the scope.",
              status: 400,
              url: "/review/diff",
            });
          }
          const resp = await reviewDiff({
            workspace_id: workspaceId,
            repository_id: repoId,
            title: message,
            diff: looksLikeDiff(message) ? message : null,
          });
          resolved = fromReviewResponse(pendingId, resp);
        } else {
          const resp = await chatQuery({
            workspace_id: workspaceId,
            repository_ids: repositoryIds,
            mode,
            message,
            history,
            top_k: topK,
            branch: branch ?? null,
          });
          resolved = fromChatResponse(pendingId, resp);
        }
        setTurns((prev) =>
          prev.map((t) => (t.id === pendingId ? resolved : t)),
        );
      } catch (err) {
        const apiErr = err instanceof ApiError ? err : null;
        const errorTurn: ChatTurn = {
          id: pendingId,
          role: "assistant",
          content: "",
          error: apiErr?.isNetworkError
            ? "Backend unreachable — start the API and try again."
            : apiErr?.message ?? "The request failed. Please try again.",
        };
        setTurns((prev) =>
          prev.map((t) => (t.id === pendingId ? errorTurn : t)),
        );
      } finally {
        setBusy(false);
        textareaRef.current?.focus();
      }
    },
    [busy, workspaceId, repositoryIds, mode, branch, topK, turns],
  );

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        void send(input);
      }
    },
    [send, input],
  );

  const openEvidence = useCallback(
    (turnId: string, focusIndex?: number) => {
      setEvidenceTurnId(turnId);
      setEvidenceFocus(focusIndex ?? null);
    },
    [],
  );

  const disabled = !workspaceId || busy;

  return (
    <div className={className}>
      <div className="flex h-full flex-col">
        {/* Transcript */}
        <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
          {turns.length === 0 ? (
            <div className="mx-auto max-w-2xl pt-6">
              <EmptyState
                icon={Sparkles}
                title="Ask anything about your code"
                description={
                  workspaceId
                    ? "Responses are grounded in indexed evidence with inline citations and a full diagnostics trace."
                    : "Select or create a workspace to start an investigation."
                }
                compact
              />
              {workspaceId ? (
                <div className="mt-4 flex flex-wrap justify-center gap-2">
                  {SUGGESTIONS[mode].map((s) => (
                    <button
                      key={s}
                      type="button"
                      onClick={() => void send(s)}
                      className="rounded-md border border-border bg-surface-2 px-3 py-1.5 text-left text-xs text-muted transition-colors hover:border-border-strong hover:text-fg"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          ) : (
            <div className="mx-auto max-w-3xl space-y-5">
              {turns.map((turn) => (
                <MessageBubble
                  key={turn.id}
                  turn={turn}
                  onOpenEvidence={(focusIndex) =>
                    openEvidence(turn.id, focusIndex)
                  }
                  onOpenTrace={(tid) => setTraceId(tid)}
                />
              ))}
            </div>
          )}
        </div>

        {/* Composer */}
        <div className="border-t border-border bg-surface/60 px-4 py-3 backdrop-blur">
          <div className="mx-auto max-w-3xl">
            <div className="flex items-end gap-2 rounded-lg border border-border-strong bg-surface-2 px-3 py-2 focus-within:ring-2 focus-within:ring-accent/60">
              <textarea
                ref={textareaRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={onKeyDown}
                disabled={disabled}
                rows={1}
                placeholder={
                  workspaceId
                    ? mode === "change_review"
                      ? "Describe the change, or paste a unified diff…"
                      : "Ask a question or describe what you're investigating…"
                    : "Select a workspace to begin…"
                }
                className="max-h-40 min-h-[2rem] flex-1 resize-none bg-transparent text-sm text-fg placeholder:text-faint focus:outline-none disabled:opacity-50"
              />
              <div className="flex shrink-0 items-center gap-1.5 pb-0.5">
                {turns.length > 0 ? (
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    title="Clear conversation"
                    disabled={busy}
                    onClick={() => {
                      setTurns([]);
                      setEvidenceTurnId(null);
                      setTraceId(null);
                    }}
                  >
                    <Eraser className="h-4 w-4" />
                  </Button>
                ) : null}
                <Button
                  type="button"
                  variant="primary"
                  size="sm"
                  loading={busy}
                  disabled={disabled || input.trim().length === 0}
                  onClick={() => void send(input)}
                >
                  Send
                  <CornerDownLeft className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>
            <p className="mt-1.5 px-1 text-2xs text-faint">
              {repositoryIds.length === 0
                ? "Searching all repositories in the workspace."
                : `Scoped to ${repositoryIds.length} repositor${
                    repositoryIds.length === 1 ? "y" : "ies"
                  }.`}{" "}
              Enter to send · Shift+Enter for newline.
            </p>
          </div>
        </div>
      </div>

      {/* Drawers */}
      <EvidenceDrawer
        open={evidenceTurn !== null}
        onClose={() => setEvidenceTurnId(null)}
        evidence={evidenceTurn?.evidence}
        citations={evidenceTurn?.citations}
        focusIndex={evidenceFocus}
      />
      <TraceDrawer
        open={traceId !== null}
        onClose={() => setTraceId(null)}
        traceId={traceId}
      />
    </div>
  );
}

/** Cheap heuristic: does the text look like a unified diff payload? */
function looksLikeDiff(text: string): boolean {
  return (
    /^diff --git /m.test(text) ||
    (/^@@ /m.test(text) && /^[+-]/m.test(text)) ||
    /^--- a\//m.test(text)
  );
}

// Re-export the evidence/citation types consumers may want alongside the panel.
export type { Evidence, Citation };
