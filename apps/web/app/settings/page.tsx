"use client";

import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  Boxes,
  Brain,
  Check,
  Copy,
  Database,
  RefreshCw,
  Server,
  Settings as SettingsIcon,
  Terminal,
} from "lucide-react";

import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Spinner } from "@/components/ui/Spinner";
import { API_BASE_URL, ApiError, health } from "@/lib/api";
import { useCopy } from "@/lib/useCopy";
import { cn } from "@/lib/format";
import type { HealthResponse } from "@/lib/types";

const LANGFUSE_URL =
  process.env.NEXT_PUBLIC_LANGFUSE_URL ?? "http://localhost:3001";

export default function SettingsPage() {
  const [healthData, setHealthData] = useState<HealthResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setHealthData(await health());
    } catch (err) {
      const apiErr = err instanceof ApiError ? err : null;
      setError(
        apiErr?.isNetworkError
          ? "Backend unreachable — start the API to read live service config."
          : apiErr?.message ?? "Failed to load /health.",
      );
      setHealthData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const services = (healthData?.services ?? {}) as Record<string, unknown>;

  return (
    <div className="mx-auto max-w-4xl px-6 py-6">
      {/* Header */}
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-lg font-semibold tracking-tight text-fg">
            <SettingsIcon className="h-5 w-5 text-accent" />
            Settings
          </h1>
          <p className="mt-0.5 text-sm text-muted">
            Read-only view of the running service configuration. Change values
            via environment variables and restart.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <ServiceStatusBadge status={healthData?.status} loading={loading} error={error} />
          <Button
            variant="ghost"
            size="icon"
            title="Refresh"
            onClick={() => void load()}
            disabled={loading}
          >
            <RefreshCw className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {error ? (
        <Card className="mb-5 border-warn/30">
          <CardBody className="flex items-start gap-2.5">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warn" />
            <div>
              <p className="text-sm text-fg">{error}</p>
              <p className="mt-0.5 text-xs text-muted">
                Showing build-time defaults from{" "}
                <span className="mono">NEXT_PUBLIC_*</span> where available.
              </p>
            </div>
          </CardBody>
        </Card>
      ) : null}

      {loading && !healthData ? (
        <div className="flex justify-center py-16">
          <Spinner size="lg" label="Reading /health…" />
        </div>
      ) : (
        <div className="space-y-5">
          {/* Models */}
          <ConfigSection
            icon={Brain}
            title="Models"
            rows={[
              kv(services, ["gen_model", "ollama_gen_model"], "Generation model"),
              kv(
                services,
                ["reasoning_model", "ollama_reasoning_model"],
                "Reasoning model",
              ),
              kv(
                services,
                ["embed_model", "ollama_embed_model", "embedding_model"],
                "Embedding model",
              ),
              kv(services, ["embedding_provider"], "Embedding provider"),
              kv(services, ["embedding_dim"], "Embedding dim"),
              kv(services, ["model_temperature", "temperature"], "Temperature"),
              kv(services, ["model_num_ctx", "num_ctx"], "Context window"),
            ]}
          />

          {/* Retrieval */}
          <ConfigSection
            icon={Boxes}
            title="Retrieval"
            rows={[
              kv(services, ["retrieval_top_k", "top_k"], "Top-K"),
              kv(services, ["hybrid_alpha"], "Hybrid alpha"),
              kv(services, ["rerank_enabled"], "Reranking"),
              kv(services, ["max_context_chars"], "Max context chars"),
              kv(services, ["qdrant_collection"], "Qdrant collection"),
            ]}
          />

          {/* Services */}
          <ConfigSection
            icon={Server}
            title="Services"
            rows={[
              { label: "API base URL", value: API_BASE_URL },
              kv(services, ["qdrant_url", "qdrant"], "Qdrant"),
              kv(services, ["ollama_base_url", "ollama"], "Ollama"),
              kv(services, ["redis_url", "redis"], "Redis"),
              kv(services, ["langfuse_host", "langfuse"], "Langfuse"),
              { label: "Langfuse UI", value: LANGFUSE_URL },
              kv(services, ["app_env", "env"], "Environment"),
            ]}
          />

          {/* Raw services payload */}
          {healthData ? (
            <RawHealthCard data={services} />
          ) : null}

          {/* How to change models */}
          <Card>
            <CardHeader>
              <CardTitle>Changing the configuration</CardTitle>
              <Terminal className="h-4 w-4 text-faint" />
            </CardHeader>
            <CardBody className="space-y-3 text-sm text-muted">
              <p>
                All model and retrieval settings are sourced from environment
                variables read at startup. Edit your{" "}
                <span className="mono text-fg">.env</span> (or{" "}
                <span className="mono text-fg">apps/api/.env</span> for local
                runs) and restart the API to apply changes.
              </p>
              <EnvExample
                lines={[
                  "# Swap the generation / reasoning models",
                  "OLLAMA_GEN_MODEL=llama3.1:8b",
                  "OLLAMA_REASONING_MODEL=qwen2.5-coder:7b",
                  "",
                  "# Embeddings (set EMBEDDING_DIM to match the model)",
                  "EMBEDDING_PROVIDER=fastembed",
                  "EMBEDDING_MODEL=BAAI/bge-small-en-v1.5",
                  "EMBEDDING_DIM=384",
                  "",
                  "# Retrieval tuning",
                  "RETRIEVAL_TOP_K=8",
                  "HYBRID_ALPHA=0.6",
                  "RERANK_ENABLED=false",
                ]}
              />
              <p className="text-xs text-faint">
                Pull new Ollama models before selecting them, e.g.{" "}
                <span className="mono">ollama pull qwen2.5-coder:7b</span>.
                Changing the embedding model or dimension requires re-indexing
                every repository.
              </p>
            </CardBody>
          </Card>
        </div>
      )}
    </div>
  );
}

interface Row {
  label: string;
  value: string;
}

/** Pull the first present key from the services payload into a labeled row. */
function kv(
  services: Record<string, unknown>,
  keys: string[],
  label: string,
): Row {
  for (const k of keys) {
    if (k in services && services[k] !== null && services[k] !== undefined) {
      return { label, value: stringify(services[k]) };
    }
  }
  return { label, value: "—" };
}

function stringify(value: unknown): string {
  if (typeof value === "boolean") return value ? "enabled" : "disabled";
  if (typeof value === "object") {
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
  return String(value);
}

function ConfigSection({
  icon: Icon,
  title,
  rows,
}: {
  icon: typeof Brain;
  title: string;
  rows: Row[];
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Icon className="h-4 w-4 text-accent" />
          {title}
        </CardTitle>
      </CardHeader>
      <CardBody className="p-0">
        <dl className="divide-y divide-border">
          {rows.map((row) => (
            <ConfigRow key={row.label} label={row.label} value={row.value} />
          ))}
        </dl>
      </CardBody>
    </Card>
  );
}

function ConfigRow({ label, value }: { label: string; value: string }) {
  const { copied, copy } = useCopy();
  const empty = value === "—";
  return (
    <div className="flex items-center justify-between gap-3 px-4 py-2.5">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="flex min-w-0 items-center gap-2">
        <span
          className={cn(
            "mono truncate text-xs",
            empty ? "text-faint" : "text-fg",
          )}
          title={value}
        >
          {value}
        </span>
        {!empty ? (
          <button
            type="button"
            onClick={() => void copy(value)}
            className="shrink-0 rounded p-1 text-faint transition-colors hover:bg-surface-2 hover:text-fg"
            title="Copy value"
            aria-label={`Copy ${label}`}
          >
            {copied ? (
              <Check className="h-3 w-3 text-ok" />
            ) : (
              <Copy className="h-3 w-3" />
            )}
          </button>
        ) : null}
      </dd>
    </div>
  );
}

function RawHealthCard({ data }: { data: Record<string, unknown> }) {
  const json = JSON.stringify(data, null, 2);
  const { copied, copy } = useCopy();
  if (Object.keys(data).length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Database className="h-4 w-4 text-faint" />
          Raw /health payload
        </CardTitle>
        <button
          type="button"
          onClick={() => void copy(json)}
          className="inline-flex items-center gap-1 rounded px-1.5 py-1 text-2xs text-muted transition-colors hover:bg-surface-2 hover:text-fg"
        >
          {copied ? (
            <Check className="h-3 w-3 text-ok" />
          ) : (
            <Copy className="h-3 w-3" />
          )}
          {copied ? "Copied" : "Copy"}
        </button>
      </CardHeader>
      <CardBody>
        <pre className="mono max-h-72 overflow-auto rounded-md border border-border bg-bg/40 p-3 text-2xs leading-relaxed text-muted">
          {json}
        </pre>
      </CardBody>
    </Card>
  );
}

function EnvExample({ lines }: { lines: string[] }) {
  const text = lines.join("\n");
  const { copied, copy } = useCopy();
  return (
    <div className="relative">
      <pre className="mono overflow-auto rounded-md border border-border bg-bg/40 p-3 pr-12 text-2xs leading-relaxed">
        {lines.map((line, i) => (
          <span
            key={i}
            className={cn(
              "block",
              line.startsWith("#") ? "text-faint" : "text-fg",
              line === "" && "h-3",
            )}
          >
            {line}
          </span>
        ))}
      </pre>
      <button
        type="button"
        onClick={() => void copy(text)}
        className="absolute right-2 top-2 rounded p-1 text-faint transition-colors hover:bg-surface-2 hover:text-fg"
        title="Copy example"
        aria-label="Copy example env"
      >
        {copied ? (
          <Check className="h-3.5 w-3.5 text-ok" />
        ) : (
          <Copy className="h-3.5 w-3.5" />
        )}
      </button>
    </div>
  );
}

function ServiceStatusBadge({
  status,
  loading,
  error,
}: {
  status?: string;
  loading: boolean;
  error: string | null;
}) {
  if (loading) return <Badge tone="neutral">Checking…</Badge>;
  if (error) return <Badge tone="danger" dot>Offline</Badge>;
  const tone: BadgeTone =
    status === "ok" || status === "healthy" ? "ok" : status ? "warn" : "neutral";
  return (
    <Badge tone={tone} dot>
      {status ?? "unknown"}
    </Badge>
  );
}
