#!/usr/bin/env python3
"""Seed TracePilot with the bundled demo repository and run a sample query.

This script drives the *running* TracePilot API over HTTP (it does not import any
TracePilot package), so it works the same whether the API runs natively or in
Docker. End to end it:

  1. waits for the API to be healthy,
  2. creates (or reuses) a ``Demo`` workspace,
  3. connects ``scripts/demo_repo`` by local path,
  4. triggers indexing and polls ``/repositories/{id}/status`` to completion,
  5. runs one grounded ``/chat/query`` and prints the answer + citations.

It is idempotent: re-running reuses the existing workspace/repository. Every step
prints a clear status line; any failure prints a diagnostic and exits non-zero so
``make seed`` fails loudly in CI.

Configuration (env)
-------------------
  TRACEPILOT_API_BASE   API base URL (default ``http://localhost:8000``).
  DEMO_REPO_PATH        Path to the demo repo *as the API sees it*. Defaults to
                        the host-absolute path of ``scripts/demo_repo``. When the
                        API runs in Docker with the repos mount, set this to the
                        in-container path, e.g. ``/repos/demo_repo``.
  SEED_QUESTION         Override the sample question.
  SEED_INDEX_TIMEOUT_S  Max seconds to wait for indexing (default 300).
  SEED_API_TIMEOUT_S    Max seconds to wait for the API to come up (default 60).
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any, NoReturn

try:
    import httpx
except ImportError:  # pragma: no cover - dependency hint
    sys.stderr.write(
        "error: httpx is required to run the seed script.\n"
        "       Install it with `pip install httpx` (or `make install`).\n"
    )
    sys.exit(2)


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
API_BASE = os.environ.get("TRACEPILOT_API_BASE", "http://localhost:8000").rstrip("/")
DEMO_REPO_LOCAL = (Path(__file__).resolve().parent / "demo_repo").resolve()
DEMO_REPO_PATH = os.environ.get("DEMO_REPO_PATH", str(DEMO_REPO_LOCAL))
WORKSPACE_NAME = "Demo"
REPO_NAME = "ledger-demo"
QUESTION = os.environ.get(
    "SEED_QUESTION",
    "How does a transfer work in this service, and what invariants must it preserve?",
)
API_TIMEOUT_S = int(os.environ.get("SEED_API_TIMEOUT_S", "60"))
INDEX_TIMEOUT_S = int(os.environ.get("SEED_INDEX_TIMEOUT_S", "300"))
HTTP_TIMEOUT_S = 180.0  # a chat query can be slow on a cold local model


# --------------------------------------------------------------------------- #
# Output helpers
# --------------------------------------------------------------------------- #
def info(msg: str) -> None:
    print(f"  {msg}", flush=True)


def step(msg: str) -> None:
    print(f"\n==> {msg}", flush=True)


def ok(msg: str) -> None:
    print(f"  ✓ {msg}", flush=True)


def die(msg: str, detail: str = "") -> NoReturn:
    sys.stderr.write(f"\nERROR: {msg}\n")
    if detail:
        sys.stderr.write(f"       {detail}\n")
    sys.exit(1)


# --------------------------------------------------------------------------- #
# API helpers
# --------------------------------------------------------------------------- #
def _request(client: httpx.Client, method: str, path: str, **kwargs: Any) -> httpx.Response:
    url = f"{API_BASE}{path}"
    try:
        resp = client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        die(f"{method} {path} failed to connect", str(exc))
    if resp.status_code >= 400:
        body = _safe_body(resp)
        die(f"{method} {path} -> HTTP {resp.status_code}", body)
    return resp


def _safe_body(resp: httpx.Response) -> str:
    try:
        data = resp.json()
        # The API's error envelope is {"error": {"type", "message"}}.
        if isinstance(data, dict) and "error" in data:
            err = data["error"]
            if isinstance(err, dict):
                return f"{err.get('type', 'error')}: {err.get('message', '')}"
        return str(data)[:500]
    except Exception:
        return resp.text[:500]


def wait_for_api(client: httpx.Client) -> None:
    step(f"Waiting for the API at {API_BASE} (up to {API_TIMEOUT_S}s)")
    deadline = time.monotonic() + API_TIMEOUT_S
    last_err = ""
    while time.monotonic() < deadline:
        try:
            resp = client.get(f"{API_BASE}/health", timeout=5.0)
            if resp.status_code < 500:
                body = resp.json()
                services = body.get("services", {})
                ok(f"API is up (status={body.get('status')})")
                degraded = [k for k, v in services.items() if v not in ("ok", "unavailable")]
                if degraded:
                    info(f"note: degraded services: {', '.join(sorted(degraded))}")
                return
            last_err = f"HTTP {resp.status_code}"
        except httpx.HTTPError as exc:
            last_err = str(exc)
        time.sleep(2.0)
    die(
        f"API at {API_BASE} did not become healthy in {API_TIMEOUT_S}s",
        f"last error: {last_err}. Is the stack up? Try `make up` (or `make api`).",
    )


def find_workspace(client: httpx.Client, name: str) -> dict | None:
    resp = _request(client, "GET", "/workspaces")
    for ws in resp.json():
        if ws.get("name") == name:
            return ws
    return None


def ensure_workspace(client: httpx.Client) -> dict:
    step(f"Ensuring workspace {WORKSPACE_NAME!r}")
    existing = find_workspace(client, WORKSPACE_NAME)
    if existing:
        ok(f"reusing workspace {existing['id']}")
        return existing
    resp = _request(
        client,
        "POST",
        "/workspaces",
        json={"name": WORKSPACE_NAME, "description": "TracePilot demo workspace (seeded)"},
    )
    ws = resp.json()
    ok(f"created workspace {ws['id']}")
    return ws


def find_repository(client: httpx.Client, workspace_id: str, name: str) -> dict | None:
    resp = _request(client, "GET", f"/workspaces/{workspace_id}/repositories")
    for repo in resp.json():
        if repo.get("name") == name:
            return repo
    return None


def ensure_repository(client: httpx.Client, workspace_id: str) -> dict:
    step(f"Connecting demo repository from {DEMO_REPO_PATH}")
    if DEMO_REPO_PATH == str(DEMO_REPO_LOCAL) and not DEMO_REPO_LOCAL.is_dir():
        die(
            "the bundled demo repo is missing",
            f"expected a directory at {DEMO_REPO_LOCAL}",
        )

    existing = find_repository(client, workspace_id, REPO_NAME)
    if existing:
        ok(f"reusing repository {existing['id']} (status={existing.get('status')})")
        return existing

    resp = _request(
        client,
        "POST",
        "/repositories/connect",
        json={
            "workspace_id": workspace_id,
            "name": REPO_NAME,
            "local_path": DEMO_REPO_PATH,
            "branch": "main",
        },
    )
    repo = resp.json()
    if repo.get("error"):
        die("repository connected with an error", repo["error"])
    ok(f"connected repository {repo['id']}")
    return repo


def trigger_index(client: httpx.Client, repository_id: str) -> dict:
    step("Triggering indexing")
    resp = _request(
        client,
        "POST",
        f"/repositories/{repository_id}/index",
        json={"incremental": False},
    )
    job = resp.json()
    ok(f"index job {job['id']} started (status={job.get('status')})")
    return job


def poll_until_indexed(client: httpx.Client, repository_id: str) -> dict:
    step(f"Polling index status (up to {INDEX_TIMEOUT_S}s)")
    deadline = time.monotonic() + INDEX_TIMEOUT_S
    last_progress = -1.0
    while time.monotonic() < deadline:
        resp = _request(client, "GET", f"/repositories/{repository_id}/status")
        body = resp.json()
        repo = body.get("repository", {})
        job = body.get("job") or {}
        status = repo.get("status")
        job_status = job.get("status")
        progress = float(job.get("progress", 0.0) or 0.0)

        if progress != last_progress or job.get("message"):
            pct = int(progress * 100)
            info(f"[{pct:3d}%] repo={status} job={job_status} :: {job.get('message', '')}")
            last_progress = progress

        if status == "indexed" or job_status == "succeeded":
            stats = repo.get("stats") or job.get("stats") or {}
            ok(
                f"indexing complete: {stats.get('num_files', '?')} files, "
                f"{stats.get('num_chunks', '?')} chunks, "
                f"languages={stats.get('languages', {})}"
            )
            return repo
        if status == "error" or job_status == "failed":
            die("indexing failed", repo.get("error") or job.get("error") or "(no error detail)")
        time.sleep(2.0)
    die(
        f"indexing did not complete within {INDEX_TIMEOUT_S}s",
        "check the API logs (`make logs`); embedding the first time can download a model.",
    )


def run_sample_query(client: httpx.Client, workspace_id: str, repository_id: str) -> dict:
    step("Running a sample grounded chat query")
    info(f"question: {QUESTION}")
    resp = _request(
        client,
        "POST",
        "/chat/query",
        json={
            "workspace_id": workspace_id,
            "repository_ids": [repository_id],
            "mode": "ask",
            "message": QUESTION,
            "top_k": 8,
        },
        timeout=HTTP_TIMEOUT_S,
    )
    return resp.json()


# --------------------------------------------------------------------------- #
# Pretty-print the answer
# --------------------------------------------------------------------------- #
def print_answer(resp: dict) -> None:
    print("\n" + "=" * 72)
    print("GROUNDED ANSWER")
    print("=" * 72)
    print((resp.get("answer") or "(no answer)").strip())

    print("\n" + "-" * 72)
    meta = (
        f"confidence={resp.get('confidence')}  "
        f"intent={resp.get('intent')}  "
        f"latency={resp.get('latency_ms')}ms  "
        f"trace={resp.get('trace_id')}"
    )
    print(meta)

    citations = resp.get("citations") or []
    print(f"\nCITATIONS ({len(citations)})")
    if not citations:
        print("  (none — the model answered without grounding; check that indexing ran)")
    for c in citations:
        loc = f"{c.get('file_path')}:{c.get('start_line')}-{c.get('end_line')}"
        print(f"  [{c.get('index')}] {c.get('repository')} · {loc}  (score={c.get('score')})")

    warnings = resp.get("warnings") or []
    if warnings:
        print("\nWARNINGS")
        for w in warnings:
            print(f"  - {w}")
    print("=" * 72)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    print("TracePilot demo seed")
    print(f"  API:       {API_BASE}")
    print(f"  Demo repo: {DEMO_REPO_PATH}")

    with httpx.Client(timeout=HTTP_TIMEOUT_S) as client:
        wait_for_api(client)
        ws = ensure_workspace(client)
        repo = ensure_repository(client, ws["id"])

        # Skip re-indexing if the repo is already indexed and has chunks.
        already = repo.get("status") == "indexed" and (repo.get("stats") or {}).get("num_chunks")
        if already:
            ok(f"repository already indexed ({repo['stats']['num_chunks']} chunks); skipping reindex")
        else:
            trigger_index(client, repo["id"])
            repo = poll_until_indexed(client, repo["id"])

        answer = run_sample_query(client, ws["id"], repo["id"])

    print_answer(answer)

    # Treat an answer with zero citations as a soft failure: the whole point of
    # the demo is grounded retrieval. (A degraded model still cites evidence.)
    if not (answer.get("citations") or []):
        die(
            "the sample answer had no citations",
            "retrieval returned no evidence — verify Qdrant is up and indexing succeeded.",
        )

    print("\nDemo seed complete. Open the UI at http://localhost:3000/chat to explore.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:  # pragma: no cover
        sys.stderr.write("\ninterrupted\n")
        sys.exit(130)
