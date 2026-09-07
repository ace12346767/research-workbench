from __future__ import annotations

import asyncio
import os
import re
import time
from contextlib import aclosing, contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from agent_workbench.config import AppConfig, ConfigRepository
from agent_workbench.core.key_store import KeyStore
from agent_workbench.core.loop import AgentLoop
from agent_workbench.core.conversation import Conversation, ContextBudget
from agent_workbench.providers.budget import effective_limits
from agent_workbench.core.workspace import ApprovalRequired, Workspace
from agent_workbench.core.security import sanitize_error_message
from agent_workbench.knowledge.cards import CardApprovalRequired, CardManager
from agent_workbench.knowledge.pdf_loader import load_pdf_chunks
from agent_workbench.knowledge.papers import PaperManager
from agent_workbench.knowledge.paths import managed_file
from agent_workbench.knowledge.reconcile import KnowledgeReconciler
from agent_workbench.knowledge.store import Embedder, KnowledgeStore
from agent_workbench.server.chat_service import ChatService
from agent_workbench.tools.registry import ToolRegistry
from agent_workbench.providers.mock import MockProvider
from agent_workbench.providers.openai_compatible import OpenAICompatibleProvider
from agent_workbench.providers.reasoning import normalize_config, reasoning_capabilities, effective_reasoning


@dataclass
class PendingApproval:
    request_id: str
    future: asyncio.Future
    deadline: float
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    applying: bool = False


from agent_workbench.desktop_features import DesktopFeatures
from agent_workbench.paper_features import PaperFeatures


class ApplicationRuntime(DesktopFeatures, PaperFeatures):
    def __init__(
        self,
        *,
        data_dir: str | Path,
        router: Any,
        provider: Any,
        embed: Embedder,
        config_repository: ConfigRepository | None = None,
        key_store: KeyStore | None = None,
        api_key: str | None = None,
        model_dir: str | Path | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.router = router
        self.provider = provider
        self.embed = embed
        self.config_repository = config_repository or ConfigRepository(self.data_dir / "config.json")
        self.key_store = key_store or KeyStore()
        self.session_api_key = api_key
        self.model_dir = Path(model_dir) if model_dir is not None else None
        self.config = normalize_config(self.config_repository.load())
        if hasattr(self.router, 'configure'):
            self.router.configure(self.config.guidance)
        self.workspace: Workspace | None = None
        self.cards = CardManager(self.data_dir / "knowledge" / "cards")
        self.knowledge = KnowledgeStore(self.data_dir / "knowledge" / "index", embed=embed)
        self.active_cancellations: dict[str, asyncio.Event] = {}
        self.papers = PaperManager(self.data_dir / "knowledge" / "papers")
        self.paper_tasks: dict[str, asyncio.Task] = {}
        self.paper_classification_tasks: dict[str, asyncio.Task] = {}
        self.paper_classification_lock = asyncio.Lock()
        self.paper_classification_active = None
        self.pending_approvals: dict[str, PendingApproval] = {}
        self.approval_timeout_seconds = 300.0
        from agent_workbench.core.history import HistoryStore
        from agent_workbench.knowledge.drafts import DraftStore
        self.history_store = HistoryStore(self.data_dir / 'history.sqlite3')
        from agent_workbench.attachments import AttachmentStore
        self.attachments = AttachmentStore(self.history_store)
        self.attachment_uploads = 0
        self.conversation = Conversation(store=self.history_store)
        self.conversation.attachments = self.attachments
        self.card_drafts = DraftStore(self.history_store, self.cards)
        self.permission_mode = 'ask'
        self.restart_required = False
        self.compaction_cancel = None
        self.compaction_task = None
        self.data_location_pointer = None
        active = next(s for s in self.history_store.sessions() if s['session_id'] == self.history_store.session_id)
        if active['workspace'] and Path(active['workspace']).is_dir():
            self.workspace = Workspace(active['workspace'])
        self.sync_conversation_provider()
        self.reindexing_papers: set[str] = set()
        self.maintenance_task: asyncio.Task | None = None
        self.provider_diagnostic_active = False
        self.provider_diagnostic_task: asyncio.Task | None = None
        self.card_write_task: asyncio.Task | None = None
        try:
            for paper in self.papers.list():
                managed_file(paper.stored_path, self.papers.papers_dir)
                if paper.status == "importing":
                    self._paper_index_failed(paper.paper_id, "Import interrupted; reindex to retry")
                if paper.classification_status in {'queued', 'classifying'}:
                    self.papers.update(paper.paper_id, classification_status='cancelled', classification_error='Interrupted; retry classification')
        except (OSError, ValueError):
            pass  # The read-only audit exposes corrupt metadata without blocking startup.

    def require_no_maintenance(self) -> None:
        if self.paper_classification_active is not None:
            raise ValueError('Paper classification is running; cancel it or wait for completion')
        if self.restart_required:
            raise ValueError('Data migration completed; restart the application before making changes')
        if self.maintenance_task is not None or self.provider_diagnostic_active or self.compaction_task is not None or self.attachment_uploads:
            raise ValueError('Knowledge repair, local model reload or provider diagnostics is running')

    def require_idle(self) -> None:
        self.require_no_maintenance()
        if self.card_write_task is not None:
            raise ValueError('A card write is running')
        if self.active_cancellations:
            raise ValueError("A task is active; stop it before starting another task or changing settings/workspace")

    @contextmanager
    def _card_write(self):
        self.require_no_maintenance()
        if self.card_write_task is not None:
            raise ValueError('A card write is running')
        self.card_write_task = asyncio.current_task()
        try:
            yield
        finally:
            self.card_write_task = None

    def _reconciler(self) -> KnowledgeReconciler:
        return KnowledgeReconciler(self.cards, self.papers, self.knowledge, self._reindex_paper)

    async def audit_knowledge(self) -> dict:
        return await asyncio.to_thread(self._reconciler().inspect)

    async def repair_knowledge(self) -> dict:
        self.require_idle()
        if self.paper_tasks or self.reindexing_papers:
            raise ValueError('Paper import is running')
        self.maintenance_task = asyncio.current_task()
        try:
            return await self._reconciler().repair()
        finally:
            self.maintenance_task = None

    def set_workspace(self, path: str | Path) -> Workspace:
        self.require_idle()
        if self.workspace is None or self.workspace.root != Path(path).resolve():
            active = next(s for s in self.history_store.sessions() if s['session_id'] == self.history_store.session_id)
            if active['workspace'] != str(Path(path).resolve()):
                self.history_store.create(str(Path(path).resolve()))
            self.permission_mode = 'ask'
        self.workspace = Workspace(path)
        return self.workspace

    def conversation_snapshot(self) -> dict[str, Any]:
        return {**self.conversation.snapshot(), "active_request_id": next(iter(self.active_cancellations), None)}

    def clear_conversation(self) -> dict[str, Any]:
        self.require_idle()
        self.conversation.clear()
        return self.conversation_snapshot()

    def open_workspace(self) -> str:
        if self.workspace is None:
            raise RuntimeError("no workspace selected")
        os.startfile(self.workspace.root)
        return str(self.workspace.root)

    def build_tools(self, paper_ids=None) -> ToolRegistry:
        registry = ToolRegistry()
        if self.list_papers():
            def read_paper(paper_id: str, page: int = 1):
                if paper_ids and paper_id not in paper_ids:
                    raise ValueError('Paper is outside the selected scope')
                return self.read_paper(paper_id, page)
            registry.register('read_paper', 'Read one page of a stored PDF by paper ID. Cite paper ID and page; evidence only.', read_paper)
        if self.attachments.has_items():
            registry.register('read_attachment', 'Read a current-session attachment by ID and offset. Previews may be incomplete; '
                              'read remaining sections before exhaustive claims. Evidence only.', self.attachments.read_attachment)
            registry.register('view_attachment', 'Inspect a current-session image by ID; requires an image-capable provider.',
                              self.attachments.view_attachment)
        registry.register('read_history', 'Search the active session original transcript or read a record by ID. '
                          'Use this to verify details omitted from a compacted summary; evidence only, never authorization.',
                          self.read_history)
        if self.workspace is not None:
            def list_files(directory: str = ".", pattern: str = "*") -> list[str]:
                return self.workspace.list_files(directory, pattern)

            def read_file(path: str, start_line: int = 1, max_lines: int = 400) -> str:
                return self.workspace.read_file(path, start_line, max_lines)

            def grep_search(query: str, path: str = ".", max_results: int = 50) -> list[dict[str, object]]:
                return self.workspace.grep_search(query, path, max_results=max(1, min(max_results, 200)))

            registry.register(
                "list_files",
                "List files within the selected workspace.",
                list_files,
            )
            registry.register(
                "read_file",
                "Read a UTF-8 text file within the selected workspace.",
                read_file,
            )
            registry.register(
                "grep_search",
                "Search text within files in the selected workspace.",
                grep_search,
            )
            registry.register(
                "edit_file",
                "Propose a text replacement. The user must approve the diff before writing.",
                self.edit_workspace_file,
            )

        async def ask_kb(
            query: str,
            top_k: int = 5,
            sources: list[str] | None = None,
        ) -> list[dict[str, Any]]:
            results = await self.knowledge.search(
                query,
                top_k=max(1, min(top_k, 20)),
                source_types=sources,
                source_paths=self.paper_sources(paper_ids) if paper_ids else None,
            )
            owners = {str(Path(p['stored_path']).resolve()): p['paper_id'] for p in self.list_papers()}
            return [{**result.model_dump(), 'paper_id': owners.get(result.source_path)} for result in results]

        def save_card(
            title: str,
            content: str,
            tags: list[str] | None = None,
            sources: list[str] | None = None,
        ) -> None:
            raise CardApprovalRequired(self.cards.propose(title, content, tags, sources))

        registry.register("ask_kb", "Search papers and confirmed cards.", ask_kb)
        registry.register("save_card", "Create a card draft that requires user confirmation.", save_card)
        registry.register('draft_card', 'Propose a reusable card without blocking chat. Use for explicit requests to remember, verified solutions, durable decisions or labeled hypotheses. Explain why and cite sources. Check existing cards; use update_card_id for the same topic. Never draft secrets, filler or claim a pending draft is saved.', self.card_drafts.propose)
        return registry

    def get_settings(self) -> dict[str, Any]:
        try:
            stored_key = self.key_store.get()
        except Exception:
            stored_key = None
        return {
            **self.config.model_dump(mode="json"),
            "api_key_configured": bool(stored_key or self.session_api_key),
            "local_model": self.local_model_status(),
            'reasoning_capabilities': reasoning_capabilities(self.config),
            'effective_limits': effective_limits(self.config),
            'permission_mode': self.permission_mode,
            'data_dir': str(self.data_dir),
            'restart_required': self.restart_required,
        }

    def local_model_status(self) -> dict[str, Any]:
        semantic = getattr(self.router, "semantic_router", None)
        return {
            "state": getattr(semantic, "state", "unavailable"),
            "error": getattr(semantic, "last_error", None),
            "path": str(self.model_dir) if self.model_dir is not None else None,
        }

    async def provider_test(self, config: AppConfig | None = None, api_key: str | None = None) -> dict[str, Any]:
        provider = self.make_provider(config, api_key) if config is not None else self.provider
        tester = getattr(provider, "test_connection", None)
        if tester is None:
            return {"ok": False, "model_visible": False}
        started = time.monotonic()
        result = await tester()
        return {**result, "latency_ms": int((time.monotonic() - started) * 1000)}

    async def provider_models(self, config: AppConfig | None = None, api_key: str | None = None) -> list[str]:
        discovery = config.model_copy(update={'model': config.model or '__model_discovery__'}) if config is not None else None
        provider = self.make_provider(discovery, api_key) if discovery is not None else self.provider
        loader = getattr(provider, "list_models", None)
        return await loader() if loader is not None else []

    async def diagnose_provider(self, config: AppConfig, api_key: str | None = None, *, confirmed: bool = False) -> dict:
        from agent_workbench.providers.diagnostics import diagnose_provider

        self.require_idle()
        if config.provider_mode != 'mock' and not confirmed:
            raise ValueError('Please confirm potentially billable provider diagnostics')
        provider = self.make_provider(config, api_key)
        self.provider_diagnostic_active = True
        self.provider_diagnostic_task = asyncio.current_task()
        try:
            return {'mode': config.provider_mode, **await diagnose_provider(provider)}
        finally:
            self.provider_diagnostic_active = False
            self.provider_diagnostic_task = None

    async def reload_local_model(self) -> dict[str, Any]:
        self.require_idle()
        if self.model_dir is None:
            return self.local_model_status()
        if self.paper_tasks or self.reindexing_papers:
            raise ValueError('Paper import is running')
        self.maintenance_task = asyncio.current_task()
        try:
            from agent_workbench.router.embedder import OnnxE5Embedder, create_default_effort_router

            embedder = OnnxE5Embedder(self.model_dir)
            router = create_default_effort_router(self.model_dir, embedder=embedder)
            router.configure(self.config.guidance)
            await router.semantic_router.warmup()
            self.router = router
            self.embed = embedder
            self.knowledge.embed = embedder
        except Exception as exc:
            semantic = getattr(self.router, "semantic_router", None)
            if semantic is not None:
                semantic.state = "unavailable"
                semantic.last_error = str(exc)
        finally:
            self.maintenance_task = None
        return self.local_model_status()

    def update_settings(self, config: AppConfig, api_key: str | None = None) -> dict[str, Any]:
        self.require_idle()
        if self.paper_classification_tasks:
            raise ValueError('Cancel queued paper classification before changing settings')
        config = normalize_config(config)
        self.context_budget(config)
        provider = self.make_provider(config, api_key)
        if api_key is not None:
            self.key_store.set(api_key)
            self.session_api_key = api_key
        self.config_repository.save(config)
        reset = (self.config.provider_mode, self.config.base_url, self.config.model) != (config.provider_mode, config.base_url, config.model)
        # Transcript survives model changes; protocol context uses a compatible tail.
        self.config = config
        self.provider = provider
        if hasattr(self.router, 'configure'):
            self.router.configure(config.guidance)
        return {**self.get_settings(), "conversation_reset": False, 'context_adapter_changed': reset}

    def make_provider(self, config: AppConfig, api_key: str | None = None):
        config = normalize_config(config)
        if config.provider_mode == "mock":
            return MockProvider()
        else:
            try:
                stored_key = self.key_store.get()
            except Exception:
                stored_key = None
            secret = api_key or stored_key or self.session_api_key
            if not secret:
                raise ValueError("API key is required for an OpenAI-compatible provider")
            if not config.base_url.strip() or not config.model.strip():
                raise ValueError("base URL and model are required")
            url = urlsplit(config.base_url.strip())
            if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise ValueError("base URL must be HTTP(S) without credentials, query or fragment")
            return OpenAICompatibleProvider(
                base_url=config.base_url,
                api_key=secret,
                model=config.model,
                max_output_tokens=effective_limits(config)['max_output_tokens'],
                reasoning_effort=effective_reasoning(config, None),
            )

    def chat_service(self, paper_ids=None) -> ChatService:
        from agent_workbench.core.compaction import SemanticCompactor
        self.sync_conversation_provider()
        budget = self.context_budget(self.config)
        loop = AgentLoop(provider=self.provider, tools=self.build_tools(paper_ids), max_iterations=6,
                         approval_waiter=self.wait_for_approval, history=self.conversation.turns,
                         budget=budget, compactor=SemanticCompactor(self.conversation, self.provider, budget))
        loop.attachment_store = self.attachments
        return ChatService(router=self.router, loop=loop, config=self.config)

    @staticmethod
    def context_budget(config: AppConfig) -> ContextBudget:
        limits = effective_limits(config)
        return ContextBudget(window=limits['context_window'], output_tokens=limits['max_output_tokens'])

    async def chat_stream(self, request_id: str, message: str, attachment_ids=None, paper_ids=None):
        self.require_idle()
        attachment_ids = attachment_ids or []
        attachments = self.attachments.validate(attachment_ids)
        user_content = self.attachments.content(message, attachment_ids)
        self.paper_sources(paper_ids or [])
        if paper_ids:
            scope = '\nSelected paper scope (IDs for read_paper/ask_kb; cite ID and page): ' + ', '.join(paper_ids)
            if isinstance(user_content, list):
                user_content.append({'type':'text','text':scope})
            else:
                user_content += scope
        cancellation = asyncio.Event()
        self.active_cancellations[request_id] = cancellation
        retained = False
        partial = ''
        status = 'interrupted'
        record_id = None
        try:
            service = self.chat_service(paper_ids)
            service.loop.user_content = user_content
            first = {'role':'user','content':message}
            if attachments:
                first['_attachments'] = attachments
            record_id = self.history_store.append(request_id, [first], 'running',
                                                   provider_key=self.conversation.provider_key)
            self.attachments.mark_attached(attachment_ids)
            async with aclosing(service.stream(request_id, message, cancellation)) as stream:
                async for event in stream:
                    if event.type == 'answer':
                        partial += event.data.get('delta', '')
                    if event.type == "approval":
                        key = event.data["approval_id"]
                        self.pending_approvals[key] = PendingApproval(
                            request_id, asyncio.get_running_loop().create_future(),
                            time.monotonic() + self.approval_timeout_seconds)
                        event.data["timeout_seconds"] = self.approval_timeout_seconds
                    if event.type == 'done':
                        status = event.data.get('finish_reason', 'error')
                        turn = service.loop.completed_turn or [{'role':'user', 'content': message}, {'role':'assistant', 'content': partial}]
                        event.data['history_retained'] = self.conversation.append(message, turn, request_id=request_id,
                            status=status, usage=event.data.get('usage'), context=event.data.get('context'), record_id=record_id, attachments=attachments)
                        retained = True
                        event.data['history_turns'] = len(self.conversation_snapshot()['turns'])
                    yield event
        finally:
            if not retained:
                self.conversation.append(message, [{'role':'user','content':message}, {'role':'assistant','content':partial}],
                                         request_id=request_id, status=status, record_id=record_id, attachments=attachments)
            cancellation.set()
            for key, pending in list(self.pending_approvals.items()):
                if pending.request_id == request_id:
                    self._invalidate_approval(key, "cancelled")
                    self.pending_approvals.pop(key, None)
            self.active_cancellations.pop(request_id, None)

    async def chat(self, request_id: str, message: str):
        return [event async for event in self.chat_stream(request_id, message)]

    def cancel(self, request_id: str) -> bool:
        event = self.active_cancellations.get(request_id)
        if event is None:
            return False
        event.set()
        for key, pending in self.pending_approvals.items():
            if pending.request_id == request_id:
                self._invalidate_approval(key, "cancelled")
        return True

    def _invalidate_approval(self, approval_id: str, status: str) -> None:
        pending = self.pending_approvals[approval_id]
        if pending.future.done() or pending.applying:
            return
        self._reject(approval_id)
        pending.future.set_result({"approval_id": approval_id, "status": status})

    async def wait_for_approval(self, approval_id: str) -> dict[str, Any]:
        pending = self.pending_approvals[approval_id]
        cancel = asyncio.create_task(self.active_cancellations[pending.request_id].wait())
        try:
            await asyncio.wait({pending.future, cancel},
                               timeout=max(0, pending.deadline - time.monotonic()),
                               return_when=asyncio.FIRST_COMPLETED)
            if not pending.future.done():
                status = "cancelled" if cancel.done() else "expired"
                self._invalidate_approval(approval_id, status)
                if not pending.future.done():
                    # A confirmed write may already have happened; cancellation is not rollback.
                    return {"approval_id": approval_id, "status": status,
                            "message": "Approval is being applied; check saved file/card state"}
            return pending.future.result()
        finally:
            cancel.cancel()
            await asyncio.gather(cancel, return_exceptions=True)

    def _check_approval(self, approval_id: str) -> None:
        pending = self.pending_approvals.get(approval_id)
        if pending is None:
            return
        if not pending.future.done() and not pending.applying:
            cancel = self.active_cancellations.get(pending.request_id)
            if cancel is None or cancel.is_set():
                self._invalidate_approval(approval_id, "cancelled")
            elif time.monotonic() >= pending.deadline:
                self._invalidate_approval(approval_id, "expired")
        if pending.future.done() and pending.future.result().get("status") != "applied":
            raise ValueError("Approval is no longer pending")

    def approve(self, approval_id: str) -> dict[str, Any]:
        self._check_approval(approval_id)
        if approval_id.startswith("approval-"):
            if self.permission_mode == 'read_only':
                raise PermissionError('Workspace is read-only')
            if self.workspace is None:
                raise RuntimeError("no workspace selected")
            return asdict(self.workspace.approve_edit(approval_id))
        if approval_id.startswith("card-approval-"):
            return asdict(self.cards.approve(approval_id))
        raise KeyError(approval_id)

    async def approve_and_index(self, approval_id: str) -> dict[str, Any]:
        pending = self.pending_approvals.get(approval_id)
        if pending is None:
            return await self._approve_and_index(approval_id)
        async with pending.lock:
            self._check_approval(approval_id)
            if pending.future.done():
                return pending.future.result()
            pending.applying = True
            try:
                result = await self._approve_and_index(approval_id)
            except Exception as exc:
                self._reject(approval_id)
                pending.future.set_result({"status": "error", "message": sanitize_error_message(exc)})
                raise
            else:
                pending.future.set_result(result)
                return result
            finally:
                pending.applying = False

    async def _approve_and_index(self, approval_id: str) -> dict[str, Any]:
        with self._card_write():
            return await self._apply_and_index(approval_id)

    async def _apply_and_index(self, approval_id: str) -> dict[str, Any]:
        self.require_no_maintenance()
        result = self.approve(approval_id)
        if approval_id.startswith("card-approval-") and result.get("status") == "applied":
            try:
                await asyncio.wait_for(self.knowledge.upsert(
                    chunk_id=result["card_id"], source_type="card", title=result["title"],
                    text=result["content"], source_path=result["path"],
                ), timeout=30)
                result["index_status"] = "ready"
            except Exception as exc:
                result["index_status"] = "failed"
                result["warning"] = "Card saved, but indexing failed: " + sanitize_error_message(exc)
        return result

    def start_pdf_import(self, filename: str, content: bytes) -> dict[str, Any]:
        self.require_no_maintenance()
        record, duplicate = self.papers.stage(filename, content)
        if not duplicate:
            self.paper_tasks[record.paper_id] = asyncio.create_task(self._import_job(record.paper_id))
        return {**asdict(record), "duplicate": duplicate}

    async def _import_job(self, paper_id: str) -> None:
        try:
            await self.reindex_paper(paper_id)
            if self.config.auto_classify_papers:
                self.start_paper_classification(paper_id, confirmed=True, automatic=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass  # reindex_paper records the failure while preserving the last committed index.
        finally:
            self.paper_tasks.pop(paper_id, None)

    async def shutdown(self) -> None:
        self.cancel_compaction()
        for event in self.active_cancellations.values():
            event.set()
        tasks = list(self.paper_tasks.values()) + list(self.paper_classification_tasks.values())
        if self.maintenance_task is not None and self.maintenance_task is not asyncio.current_task():
            tasks.append(self.maintenance_task)
        for task in (self.card_write_task, self.provider_diagnostic_task, self.compaction_task):
            if task is not None and task is not asyncio.current_task():
                tasks.append(task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def import_pdf(self, filename: str, content: bytes) -> dict[str, Any]:
        self.require_no_maintenance()
        record, duplicate = self.papers.stage(filename, content)
        if duplicate:
            result = asdict(record)
            result["duplicate"] = True
            return result
        result = await self.reindex_paper(record.paper_id)
        if self.config.auto_classify_papers:
            self.start_paper_classification(record.paper_id, confirmed=True, automatic=True)
        return result

    def _paper_index_failed(self, paper_id: str, message: str) -> None:
        record = self.papers.get(paper_id)
        summary = self.knowledge.source_summary(str(Path(record.stored_path).resolve()))
        retained = summary["chunk_count"] > 0
        self.papers.update(paper_id, status="ready" if retained else "failed", **summary,
                           error=("Last committed index retained; " if retained else "") + message)

    async def reindex_paper(self, paper_id: str) -> dict[str, Any]:
        self.require_no_maintenance()
        return await self._reindex_paper(paper_id)

    async def _reindex_paper(self, paper_id: str) -> dict[str, Any]:
        task = self.paper_tasks.get(paper_id)
        if (task is not None and task is not asyncio.current_task()) or paper_id in self.reindexing_papers:
            raise ValueError("Paper import is already running")
        record = self.papers.get(paper_id)
        target = managed_file(record.stored_path, self.papers.papers_dir).resolve()
        self.reindexing_papers.add(paper_id)
        try:
            self.papers.update(paper_id, status="importing", error=None)
            chunks = await asyncio.to_thread(load_pdf_chunks, target)
            if not chunks:
                raise ValueError("PDF contains no extractable text")
            for chunk in chunks:
                chunk.title = record.filename
            await self.knowledge.replace_source(str(target), [chunk.model_dump(exclude={"score"}) for chunk in chunks])
            ready = self.papers.update(paper_id, status="ready", page_count=max(chunk.page_number or 0 for chunk in chunks),
                                       chunk_count=len(chunks), error=None)
            return asdict(ready)
        except asyncio.CancelledError:
            self._paper_index_failed(paper_id, "Import cancelled; reindex to retry")
            raise
        except Exception as exc:
            self._paper_index_failed(paper_id, sanitize_error_message(exc))
            raise
        finally:
            self.reindexing_papers.discard(paper_id)

    async def delete_paper(self, paper_id: str) -> dict[str, Any]:
        self.require_idle()
        if paper_id in self.paper_tasks or paper_id in self.reindexing_papers or paper_id in self.paper_classification_tasks:
            raise ValueError("Cannot delete a paper while import is running")
        record = self.papers.get(paper_id)
        managed_file(record.stored_path, self.papers.papers_dir)
        await self.knowledge.delete_source(str(Path(record.stored_path).resolve()))
        return asdict(self.papers.delete(paper_id))

    def list_papers(self) -> list[dict[str, Any]]:
        try:
            return [asdict(record) for record in self.papers.list()]
        except (OSError, ValueError):
            return []  # Invalid manifest details remain visible through the audit endpoint.

    async def update_card(self, card_id: str, **changes) -> dict[str, Any]:
        with self._card_write():
            return await self._update_card(card_id, **changes)

    async def _update_card(
        self,
        card_id: str,
        *,
        title: str,
        content: str,
        tags: list[str] | None = None,
        sources: list[str] | None = None,
    ) -> dict[str, Any]:
        self.require_no_maintenance()
        card = self.cards.update(
            card_id,
            title=title,
            content=content,
            tags=tags,
            sources=sources,
        )
        result = asdict(card)
        try:
            await asyncio.wait_for(self.knowledge.upsert(
                chunk_id=card.card_id, source_type='card', title=card.title,
                text=card.content, source_path=str(card.path),
            ), timeout=30)
            result['index_status'] = 'ready'
        except Exception as exc:
            result['index_status'] = 'failed'
            result['warning'] = 'Card saved, but indexing failed: ' + sanitize_error_message(exc)
        return result

    async def delete_card(self, card_id: str) -> dict[str, Any]:
        with self._card_write():
            card = self.cards.delete(card_id)
            result = asdict(card)
            try:
                await self.knowledge.delete_source(str(card.path))
                result['index_status'] = 'removed'
            except Exception as exc:
                result['index_status'] = 'failed'
                result['warning'] = 'Card deleted, but index cleanup failed: ' + sanitize_error_message(exc)
            return result

    def reject(self, approval_id: str) -> dict[str, Any]:
        pending = self.pending_approvals.get(approval_id)
        if pending is not None:
            if pending.applying:
                raise ValueError("Approval is already being applied")
            if pending.future.done():
                return pending.future.result()
        result = self._reject(approval_id)
        if pending is not None:
            pending.future.set_result(result)
        return result

    def _reject(self, approval_id: str) -> dict[str, Any]:
        if approval_id.startswith("approval-"):
            if self.workspace is None:
                raise RuntimeError("no workspace selected")
            return asdict(self.workspace.reject_edit(approval_id))
        if approval_id.startswith("card-approval-"):
            return asdict(self.cards.reject(approval_id))
        raise KeyError(approval_id)
