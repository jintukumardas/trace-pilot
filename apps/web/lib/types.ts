/**
 * TypeScript mirrors of the TracePilot shared Pydantic models.
 *
 * Field names are kept in **snake_case** to match the JSON the API emits
 * verbatim (the Pydantic models in `tracepilot_shared.models` are not aliased).
 * Do not camelCase these — `lib/api.ts` returns them unmodified.
 *
 * Source of truth: docs/INTERNAL_CONTRACTS.md +
 * packages/shared/tracepilot_shared/models/*.
 */

// --- Enums (string unions mirror the Python StrEnum values) ------------------

export type Confidence = "low" | "medium" | "high";

export type ChatMode = "ask" | "onboard" | "debug" | "change_review" | "fix_plan";

export type IntentType =
  | "question"
  | "onboarding"
  | "debugging"
  | "change_review"
  | "fix_plan"
  | "smalltalk";

export type ChunkType =
  | "code"
  | "markdown"
  | "doc"
  | "config"
  | "issue"
  | "pr"
  | "unknown";

export type RepoStatus = "registered" | "indexing" | "indexed" | "error";

export type JobStatus = "pending" | "running" | "succeeded" | "failed";

export type RetrievalStrategy = "dense" | "sparse" | "hybrid";

export type ToolName =
  | "repo_search"
  | "read_file"
  | "dep_tree"
  | "run_tests"
  | "run_lint"
  | "git_diff"
  | "static_analysis";

export type EvalMetric =
  | "grounding"
  | "relevance"
  | "completeness"
  | "tool_success"
  | "retrieval_quality";

// --- Workspace + repository --------------------------------------------------

export interface Workspace {
  id: string;
  name: string;
  slug: string;
  description?: string | null;
  repository_count: number;
  created_at: string;
}

export interface WorkspaceCreate {
  name: string;
  description?: string | null;
}

export interface RepositoryStats {
  num_files: number;
  num_chunks: number;
  num_skipped: number;
  languages: Record<string, number>;
  bytes_indexed: number;
}

export interface Repository {
  id: string;
  workspace_id: string;
  name: string;
  local_path?: string | null;
  git_url?: string | null;
  branch: string;
  status: RepoStatus;
  head_commit?: string | null;
  last_indexed_at?: string | null;
  stats: RepositoryStats;
  error?: string | null;
  created_at: string;
}

export interface RepositoryConnectRequest {
  workspace_id: string;
  name?: string | null;
  local_path?: string | null;
  git_url?: string | null;
  branch?: string;
}

export interface IndexRequest {
  incremental?: boolean;
  paths?: string[] | null;
}

export interface IndexJob {
  id: string;
  repository_id: string;
  status: JobStatus;
  progress: number;
  message: string;
  stats: RepositoryStats;
  error?: string | null;
  created_at: string;
  updated_at: string;
}

/** Response shape of `GET /repositories/{id}/status`. */
export interface RepositoryStatus {
  repository: Repository;
  job: IndexJob | null;
}

// --- Retrieval / evidence ----------------------------------------------------

export interface ChunkMetadata {
  repository_id: string;
  repo_name: string;
  branch: string;
  file_path: string;
  language?: string | null;
  chunk_type: ChunkType;
  symbol?: string | null;
  start_line: number;
  end_line: number;
  commit_hash?: string | null;
}

export interface Evidence {
  id: string;
  text: string;
  score: number;
  metadata: ChunkMetadata;
  rank: number;
  retriever: RetrievalStrategy;
}

export interface Citation {
  index: number;
  repository: string;
  file_path: string;
  start_line: number;
  end_line: number;
  snippet: string;
  score: number;
}

// --- Tools -------------------------------------------------------------------

export interface ToolSpec {
  name: ToolName;
  description: string;
  args_schema: Record<string, unknown>;
  destructive: boolean;
}

export interface ToolCall {
  id: string;
  tool: ToolName;
  args: Record<string, unknown>;
  reason: string;
}

export interface ToolResult {
  id: string;
  tool: ToolName;
  ok: boolean;
  output: string;
  truncated: boolean;
  exit_code?: number | null;
  duration_ms: number;
  error?: string | null;
  meta: Record<string, unknown>;
}

// --- Chat --------------------------------------------------------------------

export interface ChatMessage {
  role: "user" | "assistant" | "system";
  content: string;
}

export interface ChatRequest {
  workspace_id: string;
  repository_ids?: string[];
  mode?: ChatMode;
  message: string;
  history?: ChatMessage[];
  top_k?: number;
  branch?: string | null;
}

export interface NextAction {
  title: string;
  detail: string;
  rationale: string;
}

export interface ChatResponse {
  answer: string;
  confidence: Confidence;
  intent: IntentType;
  mode: ChatMode;
  evidence: Evidence[];
  citations: Citation[];
  next_actions: NextAction[];
  tools_used: ToolResult[];
  trace_id?: string | null;
  latency_ms: number;
  warnings: string[];
}

// --- Debug mode --------------------------------------------------------------

export interface DebugRequest {
  workspace_id: string;
  repository_ids?: string[];
  bug_report: string;
  stack_trace?: string | null;
  reproduction?: string | null;
  branch?: string | null;
}

export interface RootCauseCandidate {
  hypothesis: string;
  confidence: Confidence;
  impacted_files: string[];
  reasoning: string;
  evidence_indices: number[];
}

export interface FixPlan {
  steps: string[];
  risks: string[];
  test_strategy: string[];
  rollback?: string | null;
}

export interface DebugResponse {
  summary: string;
  root_cause_candidates: RootCauseCandidate[];
  impacted_files: string[];
  diagnostic_steps: string[];
  fix_plan?: FixPlan | null;
  evidence: Evidence[];
  citations: Citation[];
  tools_used: ToolResult[];
  confidence: Confidence;
  trace_id?: string | null;
  latency_ms: number;
}

// --- Change review mode ------------------------------------------------------

export interface DiffReviewRequest {
  workspace_id: string;
  repository_id: string;
  diff?: string | null;
  base_ref?: string | null;
  head_ref?: string | null;
  title?: string | null;
}

export interface DiffReviewResponse {
  summary: string;
  impact: string;
  risk_level: Confidence;
  affected_areas: string[];
  suggested_tests: string[];
  citations: Citation[];
  evidence: Evidence[];
  trace_id?: string | null;
  latency_ms: number;
}

// --- Traces (telemetry) ------------------------------------------------------

export interface TraceSummary {
  id: string;
  name: string;
  workflow: string;
  status: string;
  latency_ms: number;
  created_at: string;
  input_preview: string;
  output_preview: string;
  total_tokens?: number | null;
  scores: Record<string, number>;
  metadata: Record<string, unknown>;
}

export interface SpanRecord {
  id: string;
  name: string;
  type: string; // "span" | "generation" | "tool" | "retrieval"
  input: unknown;
  output: unknown;
  metadata: Record<string, unknown>;
  start_ms: number;
  end_ms: number;
  status: string;
  error?: string | null;
}

export interface TraceRecord {
  id: string;
  name: string;
  workflow: string;
  status: string;
  input: unknown;
  output: unknown;
  created_at: string;
  latency_ms: number;
  tags: string[];
  spans: SpanRecord[];
  scores: Record<string, number>;
  metadata: Record<string, unknown>;
}

// --- Evals -------------------------------------------------------------------

export interface EvalScore {
  metric: EvalMetric;
  score: number;
  passed: boolean;
  rationale: string;
}

export interface EvalResult {
  trace_id?: string | null;
  workflow: string;
  scores: EvalScore[];
  overall: number;
  created_at: string;
}

export interface EvalExample {
  id: string;
  question: string;
  mode: ChatMode;
  repository_id?: string | null;
  expected_files: string[];
  expected_keywords: string[];
  notes: string;
}

export interface EvalRunSummary {
  dataset: string;
  n: number;
  metric_averages: Record<string, number>;
  pass_rate: number;
  results: EvalResult[];
}

/** Response shape of `GET /evals`. */
export interface EvalsOverview {
  recent: EvalResult[];
  summary: Record<string, unknown>;
}

// --- Health ------------------------------------------------------------------

export interface HealthResponse {
  status: string;
  services: Record<string, unknown>;
}
