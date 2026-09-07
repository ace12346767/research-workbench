from __future__ import annotations

from pathlib import Path

import fitz
from fastapi.testclient import TestClient

from agent_workbench.core.models import RouteDecision
from agent_workbench.core.key_store import KeyStore
from agent_workbench.providers.mock import MockProvider
from agent_workbench.runtime import ApplicationRuntime
from agent_workbench.server.api import create_app


class StubRouter:
    async def route(self, text: str) -> RouteDecision:
        return RouteDecision(effort="standard", source="semantic", score=0.8, margin=0.1, reason="semantic_match")


async def keyword_embed(texts: list[str]) -> list[list[float]]:
    return [[1.0, 0.0] for _ in texts]


def make_client(tmp_path: Path) -> TestClient:
    class MemoryBackend:
        def __init__(self) -> None:
            self.value = None

        def set_password(self, service, username, password) -> None:
            self.value = password

        def get_password(self, service, username):
            return self.value

        def delete_password(self, service, username) -> None:
            self.value = None

    runtime = ApplicationRuntime(
        data_dir=tmp_path / "data",
        router=StubRouter(),
        provider=MockProvider(),
        embed=keyword_embed,
        key_store=KeyStore(backend=MemoryBackend()),
    )
    return TestClient(create_app(runtime))


def test_health_and_workspace_tree(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / "note.txt").write_text("hello", encoding="utf-8")
    client = make_client(tmp_path)

    assert client.get("/health/live").json()["status"] == "ok"
    response = client.post("/api/workspace/set", json={"path": str(workspace)})
    assert response.status_code == 200
    assert client.get("/api/workspace/tree").json()["files"] == ["note.txt"]


def test_composer_model_selection_preserves_other_settings(tmp_path):
    client = make_client(tmp_path)
    client.put('/api/settings', json={'provider_mode':'mock', 'model':'old',
                                     'context_window':64000, 'auto_classify_papers':True})
    before = client.get('/api/settings').json()
    client.post('/api/chat/stream', json={'message':'preserve this conversation'})
    history = client.get('/api/conversation').json()['turns']
    selected = client.post('/api/provider/model', json={'model':'new-model'})
    assert selected.status_code == 200
    after = client.get('/api/settings').json()
    assert after['model'] == 'new-model'
    assert client.get('/api/conversation').json()['turns'] == history
    for key in ('provider_mode','base_url','context_window','guidance','auto_classify_papers'):
        assert after[key] == before[key]
    assert client.post('/api/provider/model', json={'model':'   '}).status_code == 422


def test_post_chat_stream_emits_sse_in_order(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    response = client.post("/api/chat/stream", json={"message": "hello"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert body.index("event: route") < body.index("event: reasoning")
    assert body.index("event: reasoning") < body.index("event: answer")
    assert body.index("event: answer") < body.index("event: done")


def test_mock_chat_can_read_selected_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / "note.txt").write_text("workspace hello", encoding="utf-8")
    client = make_client(tmp_path)
    client.post("/api/workspace/set", json={"path": str(workspace)})

    response = client.post("/api/chat/stream", json={"message": "/read note.txt"})

    assert "event: tool_call" in response.text
    assert "workspace hello" in response.text
    assert "event: done" in response.text


def test_runtime_card_approval_is_serializable_and_idempotent(tmp_path: Path) -> None:
    runtime = ApplicationRuntime(
        data_dir=tmp_path / "data",
        router=StubRouter(),
        provider=MockProvider(),
        embed=keyword_embed,
    )
    draft = runtime.cards.propose("Router note", "Use abstain", ["router"])

    first = runtime.approve(draft.approval_id)
    second = runtime.approve(draft.approval_id)

    assert first["status"] == "applied"
    assert second["path"] == first["path"]


def test_root_serves_offline_workbench_ui(tmp_path: Path) -> None:
    response = make_client(tmp_path).get("/")

    assert response.status_code == 200
    assert "AgentWorkbench" in response.text
    assert "/assets/app.js" in response.text


def test_card_api_requires_approval_then_indexes_card(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    draft_response = client.post(
        "/api/kb/cards/drafts",
        json={"title": "Router note", "content": "Use abstain for ambiguous routing.", "tags": ["router"]},
    )

    assert draft_response.status_code == 200
    draft = draft_response.json()
    assert draft["status"] == "pending"
    assert client.get("/api/kb/cards").json()["cards"] == []

    approved = client.post(f"/api/approvals/{draft['approval_id']}/approve")
    assert approved.status_code == 200
    cards = client.get("/api/kb/cards").json()["cards"]
    assert cards[0]["title"] == "Router note"

    results = client.get("/api/kb/search", params={"query": "router abstain"}).json()["results"]
    assert results[0]["source_type"] == "card"


def test_pdf_upload_indexes_page_metadata(tmp_path: Path) -> None:
    pdf_path = tmp_path / "source.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Routing should abstain when evidence is ambiguous.")
    document.save(pdf_path)
    document.close()

    client = make_client(tmp_path)
    response = client.post(
        "/api/kb/papers",
        content=pdf_path.read_bytes(),
        headers={"content-type": "application/pdf", "x-filename": "routing-paper.pdf"},
    )

    assert response.status_code == 200
    assert response.json()["page_count"] == 1
    papers = client.get("/api/kb/papers").json()["papers"]
    assert papers[0]["filename"] == "routing-paper.pdf"
    results = client.get("/api/kb/search", params={"query": "ambiguous routing"}).json()["results"]
    assert results[0]["page_number"] == 1


def test_settings_api_persists_provider_without_returning_key(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    response = client.put(
        "/api/settings",
        json={
            "provider_mode": "openai-compatible",
            "base_url": "https://provider.example/v1",
            "model": "general-reasoning-model",
            "api_key": "top-secret",
        },
    )

    assert response.status_code == 200
    assert "api_key" not in response.json()
    assert response.json()["api_key_configured"] is True
    loaded = client.get("/api/settings").json()
    assert loaded["model"] == "general-reasoning-model"
    assert "top-secret" not in (tmp_path / "data" / "config.json").read_text(encoding="utf-8")


def test_provider_diagnostics_and_model_catalog_endpoints(tmp_path: Path) -> None:
    client = make_client(tmp_path)

    test_response = client.post("/api/provider/test")
    models_response = client.get("/api/provider/models")

    assert test_response.json()["ok"] is True
    assert models_response.json()["models"] == ["mock-model"]


def test_pdf_duplicate_delete_and_reindex_lifecycle(tmp_path: Path) -> None:
    pdf_path = tmp_path / "source.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Persistent routing evidence.")
    document.save(pdf_path)
    document.close()
    client = make_client(tmp_path)
    headers = {"content-type": "application/pdf", "x-filename": "routing-paper.pdf"}

    first = client.post("/api/kb/papers", content=pdf_path.read_bytes(), headers=headers)
    second = client.post("/api/kb/papers", content=pdf_path.read_bytes(), headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    paper_id = first.json()["paper_id"]

    reindexed = client.post(f"/api/kb/papers/{paper_id}/reindex")
    assert reindexed.status_code == 200
    deleted = client.delete(f"/api/kb/papers/{paper_id}")
    assert deleted.status_code == 200
    assert client.get("/api/kb/papers").json()["papers"] == []
    assert client.get("/api/kb/search", params={"query": "routing"}).json()["results"] == []


def test_card_update_and_delete_api_keeps_index_in_sync(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    draft = client.post(
        "/api/kb/cards/drafts",
        json={"title": "Old title", "content": "router old content", "tags": ["router"], "sources": []},
    ).json()
    approved = client.post(f"/api/approvals/{draft['approval_id']}/approve").json()
    card_id = approved["card_id"]

    updated = client.put(
        f"/api/kb/cards/{card_id}",
        json={"title": "New title", "content": "workspace new content", "tags": ["workspace"], "sources": []},
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "New title"

    deleted = client.delete(f"/api/kb/cards/{card_id}")
    assert deleted.status_code == 200
    assert client.get("/api/kb/cards").json()["cards"] == []


def test_ui_exposes_advanced_demo_controls(tmp_path: Path) -> None:
    html = make_client(tmp_path).get("/").text

    for control_id in (
        "routeDetails",
        "providerTest",
        "loadModels",
        "contextWindow",
        "maxOutputTokens",
        "newCard",
        "clearConversation",
        "toggleSidebar",
    ):
        assert f'id="{control_id}"' in html


def test_workspace_preview_metadata_open_and_router_reload_endpoints(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / "note.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    client = make_client(tmp_path)
    client.post("/api/workspace/set", json={"path": str(workspace)})
    opened = []
    monkeypatch.setattr(client.app.state.runtime, "open_workspace", lambda: opened.append(True) or str(workspace))

    preview = client.get(
        "/api/workspace/file",
        params={"path": "note.txt", "start_line": 2, "max_lines": 1},
    ).json()
    opened_response = client.post("/api/workspace/open")
    reload_response = client.post("/api/router/reload")

    assert preview["content"] == "two"
    assert preview["line_count"] == 3
    assert preview["size_bytes"] > 0
    assert preview["truncated"] is True
    assert opened_response.status_code == 200
    assert opened == [True]
    assert reload_response.status_code == 200
    assert "state" in reload_response.json()


def test_background_pdf_import_reaches_terminal_status(tmp_path):
    import time
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), 'Background import evidence')
    content = document.tobytes()
    document.close()
    with make_client(tmp_path) as client:
        response = client.post('/api/kb/papers?background=true', content=content,
                               headers={'content-type': 'application/pdf', 'x-filename': 'background.pdf'})
        assert response.status_code == 202
        assert response.json()['status'] == 'importing'
        for _ in range(100):
            papers = client.get('/api/kb/papers').json()['papers']
            if papers and papers[0]['status'] != 'importing':
                break
            time.sleep(.01)
        assert papers[0]['status'] == 'ready'


def test_invalid_settings_do_not_replace_saved_configuration(tmp_path):
    client = make_client(tmp_path)
    before = client.get("/api/settings").json()
    response = client.put("/api/settings", json={
        "provider_mode": "openai-compatible", "base_url": "", "model": "draft"})
    assert response.status_code == 400
    assert client.get("/api/settings").json() == before
    assert not (tmp_path / "data" / "config.json").exists()


def test_provider_diagnostics_use_unsaved_draft_without_persisting(tmp_path, monkeypatch):
    import httpx
    from agent_workbench.providers.openai_compatible import OpenAICompatibleProvider
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"data": [{"id": "draft-model"}]})
    def client_for_provider(self):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(OpenAICompatibleProvider, "_client", client_for_provider)
    client = make_client(tmp_path)
    before = client.get("/api/settings").json()
    draft = {"provider_mode": "openai-compatible", "base_url": "https://draft.invalid/v1",
             "model": "draft-model", "api_key": "draft-secret"}
    result = client.post("/api/provider/test", json=draft)
    models = client.post("/api/provider/models", json=draft)
    assert result.status_code == models.status_code == 200
    assert result.json()["model_visible"] is True
    assert models.json()["models"] == ["draft-model"]
    assert len(requests) == 2
    assert all(str(request.url) == "https://draft.invalid/v1/models" for request in requests)
    assert all(request.headers["authorization"] == "Bearer draft-secret" for request in requests)
    assert client.get("/api/settings").json() == before
    assert not (tmp_path / "data" / "config.json").exists()


def test_tree_endpoint_lists_only_requested_directory(tmp_path):
    client = make_client(tmp_path)
    workspace = tmp_path / "project"
    (workspace / "src").mkdir(parents=True)
    (workspace / "src" / "main.py").write_text("main")
    client.post("/api/workspace/set", json={"path": str(workspace)})
    root = client.get("/api/workspace/tree", params={"directory": "."}).json()
    assert root["entries"] == [{"name": "src", "path": "src", "type": "directory"}]
    child = client.get("/api/workspace/tree", params={"directory": "src"}).json()
    assert child["files"] == ["src/main.py"]


def test_knowledge_audit_and_repair_endpoints_preserve_card_file(tmp_path):
    client = make_client(tmp_path)
    runtime = client.app.state.runtime
    draft = runtime.cards.propose('Repair evidence', 'Keep original source')
    card = runtime.cards.approve(draft.approval_id)
    source = Path(card.path).read_bytes()
    assert client.get('/api/kb/audit').json()['repairable_count'] == 1
    repaired = client.post('/api/kb/repair')
    assert repaired.status_code == 200
    assert repaired.json()['after']['status'] == 'healthy'
    assert Path(card.path).read_bytes() == source
    assert client.get('/api/kb/search', params={'query': 'evidence'}).json()['results']


def test_provider_capabilities_endpoint_separates_mock_checks_and_requires_remote_consent(tmp_path):
    client = make_client(tmp_path)
    result = client.post('/api/provider/diagnose', json={'provider_mode': 'mock'})
    assert result.status_code == 200
    assert [item['capability'] for item in result.json()['checks']] == ['models', 'generation', 'tool_call']
    rejected = client.post('/api/provider/diagnose', json={
        'provider_mode': 'openai-compatible', 'base_url': 'https://diagnostic.invalid/v1',
        'model': 'draft', 'api_key': 'never-sent'})
    assert rejected.status_code == 409
    assert 'confirm' in rejected.json()['detail'].lower()
    assert not (tmp_path / 'data' / 'config.json').exists()
