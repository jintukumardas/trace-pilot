"""TestClient-driven API tests.

The ``api_client`` fixture wires ``app.state`` to in-memory fakes (a real
SQLite ``MetadataStore`` plus a ``FakeOrchestrator`` / fake retriever), so these
exercise the real routers, services and error envelope without any live backend.
"""

from __future__ import annotations

from pathlib import Path


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #
def test_health_ok(api_client):
    resp = api_client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"  # store is live
    assert "services" in body
    assert body["services"]["store"] == "ok"
    # Wired (fake) collaborators report available.
    assert body["services"]["orchestrator"] == "ok"
    assert body["services"]["retriever"] == "ok"


# --------------------------------------------------------------------------- #
# Workspaces
# --------------------------------------------------------------------------- #
def test_create_and_get_workspace(api_client):
    resp = api_client.post("/workspaces", json={"name": "Platform Core", "description": "core team"})
    assert resp.status_code == 200, resp.text
    ws = resp.json()
    assert ws["name"] == "Platform Core"
    assert ws["slug"] == "platform-core"
    assert ws["id"].startswith("ws_")

    got = api_client.get(f"/workspaces/{ws['id']}")
    assert got.status_code == 200
    assert got.json()["id"] == ws["id"]

    listed = api_client.get("/workspaces")
    assert listed.status_code == 200
    assert any(w["id"] == ws["id"] for w in listed.json())


def test_get_missing_workspace_returns_error_envelope(api_client):
    resp = api_client.get("/workspaces/ws_does_not_exist")
    assert resp.status_code == 404
    body = resp.json()
    assert "error" in body
    assert body["error"]["type"] == "NotFound"
    assert "message" in body["error"]


def test_create_workspace_validation_error(api_client):
    # Empty name violates min_length=1 → 422 with the uniform envelope.
    resp = api_client.post("/workspaces", json={"name": ""})
    assert resp.status_code == 422
    assert resp.json()["error"]["type"] == "ValidationError"


# --------------------------------------------------------------------------- #
# Repositories — connect by local path
# --------------------------------------------------------------------------- #
def test_connect_repository_by_local_path(api_client, sample_repo: Path):
    ws = api_client.post("/workspaces", json={"name": "Repos"}).json()
    resp = api_client.post(
        "/repositories/connect",
        json={
            "workspace_id": ws["id"],
            "local_path": str(sample_repo),
            "branch": "main",
        },
    )
    assert resp.status_code == 200, resp.text
    repo = resp.json()
    assert repo["workspace_id"] == ws["id"]
    assert repo["name"] == sample_repo.name
    assert repo["local_path"] == str(sample_repo)
    assert repo["status"] == "registered"

    # It shows up in the workspace listing and can be fetched directly.
    listed = api_client.get(f"/workspaces/{ws['id']}/repositories")
    assert listed.status_code == 200
    assert any(r["id"] == repo["id"] for r in listed.json())

    got = api_client.get(f"/repositories/{repo['id']}")
    assert got.status_code == 200
    assert got.json()["id"] == repo["id"]


def test_connect_repository_requires_one_source(api_client):
    ws = api_client.post("/workspaces", json={"name": "X"}).json()
    # Neither local_path nor git_url → bad request.
    resp = api_client.post("/repositories/connect", json={"workspace_id": ws["id"]})
    assert resp.status_code == 400
    assert resp.json()["error"]["type"] == "BadRequest"


def test_connect_repository_unknown_workspace(api_client, sample_repo: Path):
    resp = api_client.post(
        "/repositories/connect",
        json={
            "workspace_id": "ws_missing",
            "local_path": str(sample_repo),
        },
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["type"] == "NotFound"


def test_repository_status_endpoint(api_client, sample_repo: Path):
    ws = api_client.post("/workspaces", json={"name": "Y"}).json()
    repo = api_client.post(
        "/repositories/connect",
        json={
            "workspace_id": ws["id"],
            "local_path": str(sample_repo),
        },
    ).json()
    resp = api_client.get(f"/repositories/{repo['id']}/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["repository"]["id"] == repo["id"]
    assert body["job"] is None  # no indexing run yet


# --------------------------------------------------------------------------- #
# Chat / query (mocked orchestrator)
# --------------------------------------------------------------------------- #
def test_chat_query_returns_chat_response(api_client):
    ws = api_client.post("/workspaces", json={"name": "Chat WS"}).json()
    resp = api_client.post(
        "/chat/query",
        json={
            "workspace_id": ws["id"],
            "message": "How is configuration loaded?",
            "mode": "ask",
            "repository_ids": ["repo_1"],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Required ChatResponse fields.
    for field in (
        "answer",
        "confidence",
        "intent",
        "mode",
        "evidence",
        "citations",
        "next_actions",
        "tools_used",
        "trace_id",
        "latency_ms",
        "warnings",
    ):
        assert field in body
    assert body["answer"]
    assert body["confidence"] in {"low", "medium", "high"}
    assert body["citations"] and body["citations"][0]["index"] == 1
    assert body["trace_id"]


def test_chat_query_message_required(api_client):
    ws = api_client.post("/workspaces", json={"name": "Z"}).json()
    resp = api_client.post("/chat/query", json={"workspace_id": ws["id"], "message": ""})
    assert resp.status_code == 422
    assert resp.json()["error"]["type"] == "ValidationError"


# --------------------------------------------------------------------------- #
# Investigate / debug + review (mocked orchestrator)
# --------------------------------------------------------------------------- #
def test_debug_endpoint(api_client):
    ws = api_client.post("/workspaces", json={"name": "Dbg"}).json()
    resp = api_client.post(
        "/investigate/debug",
        json={
            "workspace_id": ws["id"],
            "bug_report": "ValueError on empty payload",
            "repository_ids": ["repo_1"],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["summary"]
    assert body["confidence"] in {"low", "medium", "high"}
    assert "root_cause_candidates" in body


def test_review_endpoint(api_client):
    ws = api_client.post("/workspaces", json={"name": "Rev"}).json()
    resp = api_client.post(
        "/review/diff",
        json={
            "workspace_id": ws["id"],
            "repository_id": "repo_1",
            "diff": "--- a/config.py\n+++ b/config.py\n@@\n-x=1\n+x=2\n",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["summary"]
    assert body["risk_level"] in {"low", "medium", "high"}


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
def test_tools_endpoint_lists_specs(api_client):
    resp = api_client.get("/tools")
    assert resp.status_code == 200
    specs = resp.json()
    assert isinstance(specs, list)
    names = {s["name"] for s in specs}
    # Every allowlisted tool is surfaced with a description.
    assert {"repo_search", "read_file", "git_diff"} <= names
    assert all(s["description"] for s in specs)
    assert all("destructive" in s for s in specs)
