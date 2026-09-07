from __future__ import annotations

import json
import asyncio
import uuid
from contextlib import aclosing, asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import Body, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent_workbench.config import AppConfig
from agent_workbench.core.conversation import ContextLimitError
from agent_workbench.core.security import sanitize_error_message
from agent_workbench.runtime import ApplicationRuntime
from agent_workbench.providers.reasoning import reasoning_capabilities, normalize_config
from agent_workbench.router.settings import GuidanceConfig
from agent_workbench.knowledge.paper_analysis import MetadataEdit


class WorkspaceRequest(BaseModel):
    path: str


class ModelSelectionRequest(BaseModel):
    model: str = Field(min_length=1, max_length=512)


class PermissionRequest(BaseModel):
    mode: str


class EstimateRequest(BaseModel):
    message: str = Field(default='', max_length=1000000)
    attachment_ids: list[str] = Field(default_factory=list,max_length=6)


class ChatRequest(BaseModel):
    message: str = Field(default='',max_length=1000000)
    request_id: str | None = None
    attachment_ids: list[str] = Field(default_factory=list,max_length=6)
    paper_ids: list[str] = Field(default_factory=list, max_length=12)


class CardDraftRequest(BaseModel):
    title: str
    content: str
    tags: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)


class SettingsRequest(AppConfig):
    api_key: str | None = None


class ProviderDiagnosticRequest(SettingsRequest):
    confirmed: bool = False


class TopicRenameRequest(BaseModel):
    source: str = Field(max_length=60)
    target: str = Field(min_length=1, max_length=60)


def _sse(event_type: str, payload: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def create_app(runtime: ApplicationRuntime) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        semantic_router = getattr(runtime.router, "semantic_router", None)
        if semantic_router is not None:
            await semantic_router.warmup()
        yield
        await runtime.shutdown()

    app = FastAPI(title="AgentWorkbench", version="0.4.13", lifespan=lifespan)
    app.state.runtime = runtime

    @app.exception_handler(ValueError)
    async def invalid_state(request, exc):
        return JSONResponse(status_code=409, content={"detail": sanitize_error_message(exc)})

    @app.exception_handler(PermissionError)
    async def forbidden_path(request, exc):
        return JSONResponse(status_code=403, content={"detail": "Path is outside the allowed workspace or inaccessible"})

    @app.exception_handler(FileNotFoundError)
    @app.exception_handler(NotADirectoryError)
    async def missing_path(request, exc):
        return JSONResponse(status_code=404, content={"detail": "File or directory not found"})
    web_dir = Path(__file__).resolve().parents[1] / "assets" / "web"
    app.mount("/assets", StaticFiles(directory=web_dir), name="assets")

    @app.get("/", include_in_schema=False)
    async def root() -> FileResponse:
        return FileResponse(web_dir / "index.html")

    @app.get("/health/live")
    async def health_live() -> dict[str, str]:
        return {"status": "ok", "product": "AgentWorkbench"}

    @app.get("/api/settings")
    async def get_settings() -> dict[str, object]:
        return runtime.get_settings()

    @app.get("/api/conversation")
    async def conversation() -> dict[str, object]:
        return runtime.conversation_snapshot()

    @app.get('/api/sessions')
    async def sessions():
        return {'sessions': runtime.list_sessions(), 'active': runtime.history_store.session_id}

    @app.post('/api/data/migrate')
    async def migrate_data(request: WorkspaceRequest):
        return await runtime.migrate_data(request.path)

    @app.post('/api/sessions')
    async def new_session():
        return runtime.new_session()

    @app.post('/api/sessions/{session_id}/select')
    async def select_session(session_id: str):
        return runtime.select_session(session_id)

    @app.delete('/api/sessions/{session_id}')
    async def delete_session(session_id: str):
        return runtime.delete_session(session_id)

    @app.post('/api/context/estimate')
    async def context_estimate(request: EstimateRequest):
        return runtime.context_estimate(request.message,request.attachment_ids)

    @app.post('/api/attachments')
    async def upload_attachment(request: Request, filename: str):
        import asyncio
        from agent_workbench.attachments import MAX_FILE, validate_name
        runtime.require_idle()
        if request.headers.get('content-type','').split(';')[0] != 'application/octet-stream':
            raise HTTPException(415,'Attachment upload requires application/octet-stream')
        session_id=runtime.history_store.session_id
        runtime.attachment_uploads += 1
        try:
            validate_name(filename)
            content=bytearray()
            async for chunk in request.stream():
                content.extend(chunk)
                if len(content)>MAX_FILE:
                    raise HTTPException(413,'每个附件最多 10 MiB')
            task=asyncio.create_task(asyncio.to_thread(runtime.attachments.add,filename,bytes(content),session_id))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                # The parser thread cannot be interrupted: retain the mutation guard until
                # it finishes, so session switches and data migration cannot race its write.
                await task
                raise
        except ValueError as exc:
            raise HTTPException(400,str(exc)) from exc
        finally:
            runtime.attachment_uploads -= 1

    @app.get('/api/attachments')
    async def pending_attachments():
        return {'attachments':runtime.attachments.pending(),'session_id':runtime.history_store.session_id}

    @app.get('/api/attachments/{identifier}')
    async def attachment_detail(identifier: str):
        return runtime.attachments.get(identifier,detail=True)

    @app.get('/api/attachments/{identifier}/preview')
    async def attachment_preview(identifier: str):
        row=runtime.attachments.row(identifier)
        if row['preview'] is None:
            raise HTTPException(404,'No image preview')
        return Response(row['preview'],media_type='image/png',headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

    @app.get('/api/attachments/{identifier}/content')
    async def attachment_content(identifier: str):
        from urllib.parse import quote
        row=runtime.attachments.row(identifier)
        return Response(row['data'],media_type='application/octet-stream',headers={
            'Content-Disposition':"attachment; filename*=UTF-8''"+quote(row['name']), 'X-Content-Type-Options':'nosniff','Cache-Control':'no-store'})

    @app.delete('/api/attachments/{identifier}')
    async def delete_attachment(identifier: str):
        runtime.require_idle()
        runtime.attachments.delete(identifier)
        return {'deleted':True}

    @app.get('/api/context/summary')
    async def context_summary():
        return runtime.context_summary()

    @app.post('/api/context/compact')
    async def compact_context():
        try:
            return await runtime.compact_context()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=sanitize_error_message(exc)) from exc

    @app.post('/api/context/compact/cancel')
    async def cancel_compaction():
        return {'cancelled': runtime.cancel_compaction()}

    @app.get('/api/workspace/permissions')
    async def permission():
        return {'mode': runtime.permission_mode}

    @app.put('/api/workspace/permissions')
    async def set_permission(request: PermissionRequest):
        return runtime.set_permission_mode(request.mode)

    @app.get('/api/workspace/audit')
    async def workspace_audit():
        return {'entries': runtime.workspace_audit()}

    @app.get('/api/kb/drafts')
    async def card_drafts():
        return {'drafts': runtime.card_drafts.list()}

    @app.put('/api/kb/drafts/{draft_id}')
    async def edit_draft(draft_id: str, request: CardDraftRequest):
        runtime.require_idle()
        return runtime.card_drafts.edit(draft_id, **request.model_dump())

    @app.post('/api/kb/drafts/{draft_id}/confirm')
    async def confirm_draft(draft_id: str):
        runtime.require_idle()
        return await runtime.confirm_card_draft(draft_id)

    @app.delete('/api/kb/drafts/{draft_id}')
    async def discard_draft(draft_id: str):
        runtime.require_idle()
        runtime.card_drafts.discard(draft_id)
        return {'status': 'discarded'}

    @app.get('/api/router/defaults')
    async def router_defaults() -> dict:
        return GuidanceConfig().model_dump()

    @app.post('/api/provider/capabilities')
    async def provider_capabilities(request: AppConfig) -> dict:
        return reasoning_capabilities(normalize_config(request))

    @app.delete("/api/conversation")
    async def clear_conversation() -> dict[str, object]:
        return runtime.clear_conversation()

    @app.put("/api/settings")
    async def update_settings(request: SettingsRequest) -> dict[str, object]:
        try:
            config = AppConfig.model_validate(request.model_dump(exclude={"api_key"}))
            return runtime.update_settings(config, request.api_key)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/provider/test")
    async def test_provider(request: SettingsRequest | None = Body(default=None)) -> dict[str, object]:
        try:
            return await runtime.provider_test(request, request.api_key if request else None)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=sanitize_error_message(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=sanitize_error_message(exc)) from exc

    @app.post('/api/provider/diagnose')
    async def diagnose_provider(request: ProviderDiagnosticRequest) -> dict:
        config = AppConfig.model_validate(request.model_dump(exclude={'api_key', 'confirmed'}))
        return await runtime.diagnose_provider(config, request.api_key, confirmed=request.confirmed)

    @app.post('/api/provider/model')
    async def select_provider_model(request: ModelSelectionRequest) -> dict[str, object]:
        model = request.model.strip()
        if not model:
            raise HTTPException(status_code=422, detail='Model ID cannot be empty')
        try:
            return runtime.update_settings(runtime.config.model_copy(update={'model': model}))
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/api/provider/models")
    async def provider_models() -> dict[str, object]:
        try:
            return {"models": await runtime.provider_models()}
        except Exception as exc:
            raise HTTPException(status_code=502, detail=sanitize_error_message(exc)) from exc

    @app.post("/api/provider/models")
    async def draft_provider_models(request: SettingsRequest) -> dict[str, object]:
        try:
            return {"models": await runtime.provider_models(request, request.api_key)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=sanitize_error_message(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=sanitize_error_message(exc)) from exc

    @app.post("/api/workspace/set")
    async def set_workspace(request: WorkspaceRequest) -> dict[str, str]:
        path = Path(request.path)
        if not path.is_dir():
            raise HTTPException(status_code=400, detail="workspace directory does not exist")
        workspace = runtime.set_workspace(path)
        return {"status": "ready", "path": str(workspace.root)}

    @app.get("/api/workspace/tree")
    async def workspace_tree(directory: str | None = None) -> dict[str, object]:
        if runtime.workspace is None:
            return {"workspace": None, "files": [], "entries": []}
        if directory is not None:
            entries = runtime.workspace.list_directory(directory)
            return {"workspace": str(runtime.workspace.root), "entries": entries,
                    "files": [entry["path"] for entry in entries if entry["type"] == "file"]}
        return {"workspace": str(runtime.workspace.root), "files": runtime.workspace.list_files()}

    @app.get("/api/workspace/file")
    async def workspace_file(path: str, start_line: int = 1, max_lines: int = 1000) -> dict[str, object]:
        if runtime.workspace is None:
            raise HTTPException(status_code=409, detail="no workspace selected")
        target = runtime.workspace.resolve_path(path)
        full_text = runtime.workspace.read_file(path, 1, 5000)
        content = runtime.workspace.read_file(path, start_line, max_lines)
        line_count = len(full_text.splitlines())
        return {
            "path": path,
            "content": content,
            "size_bytes": target.stat().st_size,
            "line_count": line_count,
            "truncated": start_line > 1 or start_line - 1 + max_lines < line_count,
        }

    @app.post("/api/workspace/open")
    async def open_workspace() -> dict[str, str]:
        try:
            return {"path": runtime.open_workspace(), "status": "opened"}
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/router/reload")
    async def reload_router() -> dict[str, object]:
        return await runtime.reload_local_model()

    @app.post("/api/chat/stream")
    async def chat_stream(request: ChatRequest) -> StreamingResponse:
        runtime.require_idle()
        try:
            runtime.attachments.validate(request.attachment_ids)
            runtime.paper_sources(request.paper_ids)
            if not request.message.strip() and not request.attachment_ids:
                raise ValueError('请输入消息或添加附件')
        except (ValueError,FileNotFoundError) as exc:
            raise HTTPException(400,'附件不可用或消息为空：'+str(exc)) from exc
        request_id = request.request_id or f"req-{uuid.uuid4().hex}"

        async def generate():
            sequence = 0
            try:
                async with aclosing(runtime.chat_stream(request_id, request.message, request.attachment_ids, request.paper_ids)) as stream:
                    async for event in stream:
                        sequence = event.sequence
                        yield _sse(event.type, event.model_dump())
            except Exception as exc:
                payload = {
                    "request_id": request_id,
                    "sequence": sequence + 1,
                    "type": "error",
                    "data": {"code": "context_limit" if isinstance(exc, ContextLimitError) else "chat_failed",
                             "message": sanitize_error_message(exc), "recoverable": True},
                }
                yield _sse("error", payload)
                yield _sse("done", {"request_id": request_id, "sequence": sequence + 2,
                                   "type": "done", "data": {"finish_reason": "error"}})

        return StreamingResponse(generate(), media_type="text/event-stream")

    @app.post("/api/chat/{request_id}/cancel")
    async def cancel_chat(request_id: str) -> dict[str, object]:
        return {"request_id": request_id, "cancelled": runtime.cancel(request_id)}

    @app.post("/api/approvals/{approval_id}/approve")
    async def approve(approval_id: str) -> dict[str, object]:
        try:
            return await runtime.approve_and_index(approval_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="approval not found") from exc

    @app.post("/api/approvals/{approval_id}/reject")
    async def reject(approval_id: str) -> dict[str, object]:
        try:
            return runtime.reject(approval_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="approval not found") from exc

    @app.post("/api/kb/cards/drafts")
    async def create_card_draft(request: CardDraftRequest) -> dict[str, object]:
        runtime.require_no_maintenance()
        return asdict(runtime.cards.propose(request.title, request.content, request.tags, request.sources))

    @app.get('/api/kb/audit')
    async def audit_knowledge() -> dict:
        return await runtime.audit_knowledge()

    @app.post('/api/kb/repair')
    async def repair_knowledge() -> dict:
        return await runtime.repair_knowledge()

    @app.get("/api/kb/cards")
    async def list_cards() -> dict[str, object]:
        return {"cards": [asdict(card) for card in runtime.cards.list_cards()]}

    @app.put("/api/kb/cards/{card_id}")
    async def update_card(card_id: str, request: CardDraftRequest) -> dict[str, object]:
        try:
            return await runtime.update_card(card_id, **request.model_dump())
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="card not found") from exc

    @app.delete("/api/kb/cards/{card_id}")
    async def delete_card(card_id: str) -> dict[str, object]:
        try:
            return await runtime.delete_card(card_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="card not found") from exc

    @app.post("/api/kb/papers")
    async def upload_paper(
        content: bytes = Body(..., media_type="application/pdf"),
        x_filename: str = Header(default="paper.pdf"),
        background: bool = False,
        filename: str | None = None,
    ):
        try:
            if background:
                return JSONResponse(runtime.start_pdf_import(filename or x_filename, content), status_code=202)
            return await runtime.import_pdf(filename or x_filename, content)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/kb/papers")
    async def list_papers() -> dict[str, object]:
        return {"papers": runtime.list_papers()}

    @app.post('/api/kb/topics/rename')
    async def rename_topic(request: TopicRenameRequest):
        runtime.require_no_maintenance()
        return runtime.papers.rename_topic(request.source, request.target)

    def paper_record(paper_id):
        try:
            return runtime.papers.get(paper_id)
        except KeyError as exc:
            raise HTTPException(404, 'Paper not found') from exc

    @app.get('/api/kb/papers/{paper_id}')
    async def paper_detail(paper_id: str):
        return asdict(paper_record(paper_id))

    @app.put('/api/kb/papers/{paper_id}/metadata')
    async def edit_paper_metadata(paper_id: str, request: MetadataEdit):
        runtime.require_no_maintenance()
        paper_record(paper_id)
        return asdict(runtime.papers.edit_metadata(paper_id, request.metadata.model_dump(mode='json'), request.revision))

    @app.post('/api/kb/papers/{paper_id}/classify')
    async def classify_paper(paper_id: str, confirmed: bool = False):
        paper_record(paper_id)
        return JSONResponse(runtime.start_paper_classification(paper_id, confirmed=confirmed), status_code=202)

    @app.post('/api/kb/papers/{paper_id}/classification/cancel')
    async def cancel_classification(paper_id: str):
        paper_record(paper_id)
        return await runtime.cancel_paper_classification(paper_id)

    @app.get('/api/kb/papers/{paper_id}/content')
    async def paper_content(paper_id: str):
        record = paper_record(paper_id)
        return FileResponse(runtime.paper_path(paper_id), media_type='application/pdf', filename=record.filename,
                            headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

    @app.get('/api/kb/papers/{paper_id}/pages/{page}')
    async def paper_page(paper_id: str, page: int, image: bool = False):
        paper_record(paper_id)
        if image:
            data = await asyncio.to_thread(runtime.paper_page_image, paper_id, page)
            return Response(data, media_type='image/png', headers={'Cache-Control':'no-store'})
        return await asyncio.to_thread(runtime.read_paper, paper_id, page)

    @app.post("/api/kb/papers/{paper_id}/reindex")
    async def reindex_paper(paper_id: str) -> dict[str, object]:
        try:
            return await runtime.reindex_paper(paper_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="paper not found") from exc

    @app.delete("/api/kb/papers/{paper_id}")
    async def delete_paper(paper_id: str) -> dict[str, object]:
        try:
            return await runtime.delete_paper(paper_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="paper not found") from exc

    @app.get("/api/kb/search")
    async def search_knowledge(query: str, top_k: int = 5) -> dict[str, object]:
        results = await runtime.knowledge.search(query, top_k=max(1, min(top_k, 20)))
        return {"results": [item.model_dump() for item in results]}

    return app
