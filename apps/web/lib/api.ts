/**
 * Typed fetch client for the TracePilot API.
 *
 * - Base URL comes from `NEXT_PUBLIC_API_BASE_URL` (default http://localhost:8000).
 * - Every call returns typed data and throws an `ApiError` on a non-2xx
 *   response (the global handler in the API returns `{ error: {type, message} }`).
 * - All helpers are isomorphic: they work from React Server Components and from
 *   client components alike. Server reads use `{ cache: "no-store" }` so the
 *   dashboard always reflects live backend state.
 */
import type {
  ChatRequest,
  ChatResponse,
  DebugRequest,
  DebugResponse,
  DiffReviewRequest,
  DiffReviewResponse,
  EvalRunSummary,
  EvalsOverview,
  HealthResponse,
  IndexJob,
  IndexRequest,
  Repository,
  RepositoryConnectRequest,
  RepositoryStatus,
  ToolSpec,
  TraceRecord,
  TraceSummary,
  Workspace,
  WorkspaceCreate,
} from "@/lib/types";

export const API_BASE_URL = (
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000"
).replace(/\/+$/, "");

/** Shape of the error envelope the API returns on failure. */
interface ApiErrorBody {
  error?: { type?: string; message?: string };
  detail?: unknown;
}

/** Typed error thrown for any non-2xx response or transport failure. */
export class ApiError extends Error {
  readonly status: number;
  readonly type: string;
  readonly url: string;
  readonly body?: unknown;

  constructor(args: {
    message: string;
    status: number;
    type?: string;
    url: string;
    body?: unknown;
  }) {
    super(args.message);
    this.name = "ApiError";
    this.status = args.status;
    this.type = args.type ?? "api_error";
    this.url = args.url;
    this.body = args.body;
  }

  /** True for network/transport failures (backend unreachable). */
  get isNetworkError(): boolean {
    return this.status === 0;
  }
}

interface RequestOptions {
  /** Override the request body (objects are JSON-encoded automatically). */
  body?: unknown;
  /** Query params; null/undefined values are dropped. */
  query?: Record<string, string | number | boolean | null | undefined>;
  /** Next.js fetch cache strategy. Defaults to "no-store" for live data. */
  cache?: RequestCache;
  /** Optional ISR-style revalidation (Next.js extension). */
  revalidate?: number;
  signal?: AbortSignal;
}

function buildUrl(
  path: string,
  query?: RequestOptions["query"],
): string {
  const url = `${API_BASE_URL}${path.startsWith("/") ? path : `/${path}`}`;
  if (!query) return url;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === null || value === undefined) continue;
    params.set(key, String(value));
  }
  const qs = params.toString();
  return qs ? `${url}?${qs}` : url;
}

async function request<T>(
  method: string,
  path: string,
  opts: RequestOptions = {},
): Promise<T> {
  const url = buildUrl(path, opts.query);
  const hasBody = opts.body !== undefined;

  const init: RequestInit & { next?: { revalidate?: number } } = {
    method,
    headers: {
      Accept: "application/json",
      ...(hasBody ? { "Content-Type": "application/json" } : {}),
    },
    cache: opts.cache ?? "no-store",
    signal: opts.signal,
  };
  if (hasBody) init.body = JSON.stringify(opts.body);
  if (opts.revalidate !== undefined) init.next = { revalidate: opts.revalidate };

  let res: Response;
  try {
    res = await fetch(url, init);
  } catch (err) {
    // Transport-level failure: backend down, DNS, CORS preflight, abort, etc.
    throw new ApiError({
      message:
        err instanceof Error ? err.message : "Network request failed",
      status: 0,
      type: "network_error",
      url,
    });
  }

  if (res.status === 204) {
    return undefined as T;
  }

  const text = await res.text();
  let parsed: unknown = undefined;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = text;
    }
  }

  if (!res.ok) {
    const bodyObj = (parsed ?? {}) as ApiErrorBody;
    const message =
      bodyObj.error?.message ??
      (typeof bodyObj.detail === "string" ? bodyObj.detail : undefined) ??
      (typeof parsed === "string" && parsed ? parsed : undefined) ??
      `Request failed with status ${res.status}`;
    throw new ApiError({
      message,
      status: res.status,
      type: bodyObj.error?.type,
      url,
      body: parsed,
    });
  }

  return parsed as T;
}

// --- Health ------------------------------------------------------------------

export function health(opts?: RequestOptions): Promise<HealthResponse> {
  return request<HealthResponse>("GET", "/health", opts);
}

// --- Workspaces --------------------------------------------------------------

export function listWorkspaces(opts?: RequestOptions): Promise<Workspace[]> {
  return request<Workspace[]>("GET", "/workspaces", opts);
}

export function getWorkspace(
  id: string,
  opts?: RequestOptions,
): Promise<Workspace> {
  return request<Workspace>("GET", `/workspaces/${id}`, opts);
}

export function createWorkspace(
  body: WorkspaceCreate,
  opts?: RequestOptions,
): Promise<Workspace> {
  return request<Workspace>("POST", "/workspaces", { ...opts, body });
}

// --- Repositories ------------------------------------------------------------

export function listRepositories(
  workspaceId: string,
  opts?: RequestOptions,
): Promise<Repository[]> {
  return request<Repository[]>(
    "GET",
    `/workspaces/${workspaceId}/repositories`,
    opts,
  );
}

export function connectRepository(
  body: RepositoryConnectRequest,
  opts?: RequestOptions,
): Promise<Repository> {
  return request<Repository>("POST", "/repositories/connect", { ...opts, body });
}

export function getRepository(
  id: string,
  opts?: RequestOptions,
): Promise<Repository> {
  return request<Repository>("GET", `/repositories/${id}`, opts);
}

export function indexRepository(
  id: string,
  body: IndexRequest = {},
  opts?: RequestOptions,
): Promise<IndexJob> {
  return request<IndexJob>("POST", `/repositories/${id}/index`, {
    ...opts,
    body,
  });
}

export function getRepositoryStatus(
  id: string,
  opts?: RequestOptions,
): Promise<RepositoryStatus> {
  return request<RepositoryStatus>("GET", `/repositories/${id}/status`, opts);
}

// --- Chat / investigate / review ---------------------------------------------

export function chatQuery(
  body: ChatRequest,
  opts?: RequestOptions,
): Promise<ChatResponse> {
  return request<ChatResponse>("POST", "/chat/query", { ...opts, body });
}

export function debugInvestigate(
  body: DebugRequest,
  opts?: RequestOptions,
): Promise<DebugResponse> {
  return request<DebugResponse>("POST", "/investigate/debug", { ...opts, body });
}

export function reviewDiff(
  body: DiffReviewRequest,
  opts?: RequestOptions,
): Promise<DiffReviewResponse> {
  return request<DiffReviewResponse>("POST", "/review/diff", { ...opts, body });
}

// --- Traces ------------------------------------------------------------------

export function listTraces(
  params: { limit?: number; workflow?: string } = {},
  opts?: RequestOptions,
): Promise<TraceSummary[]> {
  return request<TraceSummary[]>("GET", "/traces", {
    ...opts,
    query: { limit: params.limit, workflow: params.workflow },
  });
}

export function getTrace(
  id: string,
  opts?: RequestOptions,
): Promise<TraceRecord> {
  return request<TraceRecord>("GET", `/traces/${id}`, opts);
}

// --- Evals -------------------------------------------------------------------

export function getEvals(opts?: RequestOptions): Promise<EvalsOverview> {
  return request<EvalsOverview>("GET", "/evals", opts);
}

export function runEvals(
  body: { dataset?: string } = {},
  opts?: RequestOptions,
): Promise<EvalRunSummary> {
  return request<EvalRunSummary>("POST", "/evals/run", { ...opts, body });
}

// --- Tools -------------------------------------------------------------------

export function listTools(opts?: RequestOptions): Promise<ToolSpec[]> {
  return request<ToolSpec[]>("GET", "/tools", opts);
}

/**
 * Convenience namespace so consumers can `import { api } from "@/lib/api"`
 * and call `api.listWorkspaces()` without a wide import list.
 */
export const api = {
  health,
  listWorkspaces,
  getWorkspace,
  createWorkspace,
  listRepositories,
  connectRepository,
  getRepository,
  indexRepository,
  getRepositoryStatus,
  chatQuery,
  debugInvestigate,
  reviewDiff,
  listTraces,
  getTrace,
  getEvals,
  runEvals,
  listTools,
} as const;
