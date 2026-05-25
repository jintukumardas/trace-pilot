# Security

TracePilot runs sandboxed, **read-only** tools against your code and serves local
models on your own infrastructure. This document describes the tool sandbox model,
data locality, configuration hardening, and the threat model with its limitations.

The sandbox is implemented in `packages/tooling/tracepilot_tooling/sandbox.py` and
is the *only* place tools touch the filesystem or spawn a process. It is designed
to be adversarially robust, not advisory.

---

## The tool sandbox model

Every tool runs inside a `ToolContext` that names exactly one `workspace_root` and
carries the runtime budget. Tools never receive raw user paths or build shell
strings; they go through two guarded primitives: `safe_path` (filesystem) and
`run_subprocess` (process execution).

### 1. Workspace allowlist
A tool may only read under its `workspace_root` — the absolute path the
`RepoLocator` resolved for the target repository — plus any explicit
`extra_allowlist` prefixes (from `TOOL_ALLOWLIST`). Everything else is rejected.
The agent's `action_planner`/`tool_executor` won't even *plan* tools unless the repo
resolves to a real on-disk path.

### 2. Path containment (`safe_path`)
`safe_path(ctx, rel)` resolves a (relative or absolute) path with `strict=False`
and confirms the **resolved** location falls within an allowed root. This defeats:

- **`..` traversal** — resolution normalizes the path before the containment check.
- **Absolute escapes** — an absolute path outside the workspace is rejected.
- **Symlink escapes** — for existing links the *real* target (`os.path.realpath`) is
  re-validated, so a link inside the workspace can't be used to read an outside file.

A breach raises `SandboxError`, which the executor records as a hard guardrail
violation rather than silently ignoring.

### 3. Command denylist & binary allowlist (`run_subprocess`)
Process execution is locked down before anything is spawned (`_check_command`):

- **Binary allowlist** — only these may run, by basename: `rg`, `grep`, `git`,
  `pytest`, `ruff`, `python`, `python3`. Anything else → `SandboxError`.
- **Destructive-token denylist** — any argument matching `rm`, `rmdir`, `mv`, `dd`,
  `chmod`/`chown`/`chgrp`, `mkfs`, `kill`/`killall`, `shutdown`/`reboot`, `sudo`/`su`,
  `eval`/`exec`/`source`, **network fetchers** (`curl`, `wget`), or shell
  metacharacters (`>`, `>>`, `<`, `|`, `&`, `;`, `&&`, `||`, `` ` ``, `$(`, `:>`) is
  rejected — even when unspaced inside a single argument.
- **`git` is read-only & local-only** — mutating/network sub-commands are blocked:
  `push`, `commit`, `merge`, `rebase`, `reset`, `clean`, `checkout`/`switch`, `pull`,
  `fetch`, `clone`, `remote`, `gc`, `rm`, `mv`, `apply`, `am`, `cherry-pick`,
  `revert`, `tag`, `branch`, `stash`, `config`. Only inspection (`diff`, `log`,
  `show`, …) is permitted.
- **No `pip` network ops** — `python -m pip install/uninstall/download` is blocked,
  so a tool can never install a package at runtime.

### 4. No shell, sanitized environment
Commands run with `shell=False` and an **explicit argv** (never a shell string), so
there is no word-splitting, globbing, or interpolation to exploit. The environment is
minimal and non-interactive: a fixed `PATH`/`HOME`/`LANG`, `GIT_TERMINAL_PROMPT=0`,
`GIT_ASKPASS=/bin/false` (git can't prompt for or fetch credentials),
`PYTHONDONTWRITEBYTECODE=1`, `PIP_NO_INPUT=1`, `NO_COLOR=1`. A host `VIRTUAL_ENV` is
forwarded (so `pytest`/`ruff` resolve the right interpreter) without leaking other
secrets. `cwd` is confined to the (validated) workspace.

### 5. Timeouts
Every subprocess gets a wall-clock `timeout` (`TOOL_TIMEOUT_SECONDS`, default 30s).
On expiry the process is killed and the partial output is returned as a non-`ok`
result with exit code `124` — a runaway test/lint can't hang a request.

### 6. Output truncation
Combined stdout+stderr is captured and clamped to `TOOL_MAX_OUTPUT_BYTES` (default
64 KB), with a `"[truncated, N more bytes]"` marker. This bounds memory and prevents
a tool from flooding the model's context window.

### 7. No destructive ops, ever
The union of the binary allowlist, the destructive-token denylist, the git/pip
sub-command blocks, and the no-shell rule means **no tool can write, move, delete,
install, or reach the network.** The seven shipped tools (`repo_search`,
`read_file`, `dep_tree`, `run_tests`, `run_lint`, `git_diff`, `static_analysis`) are
all read-only by construction, and their specs carry `destructive: false`.

### 8. Invocation logging & fail-soft results
`run_subprocess` logs each sanctioned exec (command, cwd, timeout) at debug level.
Every tool returns a fully-populated `ToolResult` (`ok`, `output`, `exit_code`,
`truncated`, `duration_ms`, `error`, `meta`); tools **fail soft** (embed the error in
the result) rather than raising, while genuine *policy* breaches raise `SandboxError`
so they're visibly recorded. Each tool run is also wrapped in a Langfuse `tool` span,
giving a complete, auditable trail of what ran against the repo.

---

## Data locality

TracePilot is **self-hosted and local-first**:

- **Models** run on your Ollama instance (or host). Embeddings run **in-process**
  via `fastembed` by default — no external embedding API.
- **Vectors** live in your Qdrant; **metadata** in local SQLite; **traces/jobs** in
  your Redis; **observability** in your self-hosted Langfuse v2 (+ Postgres).
- **Source code** is only read from repositories you connect (local path or a git
  URL you provide). Tools cannot reach the network, so code can't be exfiltrated by
  a tool call.
- Nothing is sent to a third-party SaaS by default. The only outbound traffic is
  what *you* configure: git clones from the URL you supply, and (first run) model
  downloads from Ollama's / fastembed's model registries.

---

## Configuration hardening

For anything beyond a laptop:

- **Rotate the seeded secrets.** `docker-compose.yml` ships dev defaults:
  `NEXTAUTH_SECRET`, `SALT`, `ENCRYPTION_KEY`, the Langfuse admin password
  (`tracepilot123`), Postgres credentials, and the deterministic Langfuse API keys.
  Replace all of them before exposing the stack.
- **Don't expose the ports publicly.** The API has no authentication layer — keep
  `:8000`/`:3000`/`:6333`/`:3001`/`:6379` behind a VPN, reverse proxy with auth, or
  bound to localhost. Set `CORS_ORIGINS` to the exact web origin.
- **Scope `TOOL_ALLOWLIST` narrowly.** Only add path prefixes you intend tools to
  read; the default is empty (workspace-only).
- **Tighten tool budgets** (`TOOL_TIMEOUT_SECONDS`, `TOOL_MAX_OUTPUT_BYTES`) for
  untrusted or very large repositories.
- **Connect trusted repositories.** Indexing reads file contents; treat a connected
  repo as code you trust to read (see limitations below).
- **Set a `QDRANT_API_KEY`** if Qdrant is reachable beyond localhost.
- **Pin model sources.** In locked-down networks, pre-pull Ollama models and the
  fastembed model so no runtime downloads are attempted.

---

## Threat model

**In scope (defended):**

- A tool (or a model-planned tool call) attempting to read outside the workspace via
  traversal, absolute paths, or symlinks → blocked by `safe_path`.
- A model-planned command trying to mutate the repo, install packages, reach the
  network, or smuggle shell side-effects → blocked by the binary allowlist + token
  denylist + no-shell execution.
- A runaway or output-flooding tool → bounded by timeouts and output truncation.
- A flaky telemetry/infra backend taking down a request → all infra calls are
  guarded and fail soft.

**Out of scope / explicit limitations:**

- **No authentication/authorization** on the API or web UI. Anyone who can reach the
  ports can use the system. Put it behind your own auth.
- **No multi-tenant isolation.** Workspaces are organizational, not a security
  boundary; all data shares one Qdrant collection and one SQLite DB.
- **Connected code is trusted input.** The indexer reads file *contents*; a malicious
  repository can attempt prompt-injection through its source/docs. Retrieval is
  grounding, not execution, and tools are read-only — but treat answers from
  untrusted repos with the same skepticism you'd apply to the repo itself.
- **`run_tests` executes repository test code** (`pytest`) inside the sandbox. The
  sandbox constrains the *process* (no network, timeout, confined cwd, sanitized
  env, no shell) but the test code itself runs. Only run tests for repositories you
  trust, and rely on the timeout/allowlist as defense-in-depth, not as a
  general-purpose code jail.
- **Local clones use your git credentials/network.** Connecting a git URL performs a
  real clone via GitPython using the host's git configuration.
- **Secrets in code are read like any other text.** TracePilot does not scan for or
  redact secrets in indexed files; they can appear in retrieved evidence.

When in doubt: keep the stack private, connect only repositories you trust, and
rotate the shipped development secrets.
