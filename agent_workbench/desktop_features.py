"""Desktop session, context and explicit authorization commands."""
import asyncio
from dataclasses import asdict
from pathlib import Path

from agent_workbench.core.security import sanitize_error_message
from agent_workbench.core.workspace import Workspace, ApprovalRequired
from agent_workbench.knowledge.cards import CardDraft


class DesktopFeatures:
    def read_history(self, query: str = '', record_id: int = 0, offset: int = 0, limit: int = 4000):
        import json
        if len(query) > 500 or record_id < 0 or offset < 0:
            raise ValueError('Invalid history lookup')
        limit = max(128, min(limit, 8000))
        matches = []
        for row in reversed(self.history_store.records()):
            if record_id and row['id'] != record_id:
                continue
            public = [{k: v for k, v in m.items() if k in {'role','content','tool_calls','tool_call_id'}} for m in row['messages']]
            attached=row['messages'][0].get('_attachments',[])
            if attached:
                public[0]['attachments']=attached
            text = json.dumps(public, ensure_ascii=False)
            start = offset
            if query and not record_id:
                pos = text.casefold().find(query.casefold())
                if pos < 0:
                    continue
                start = max(0, pos - 200)
            content = text[start:start + limit]
            matches.append({'record_id':row['id'], 'status':row['status'], 'content':content,
                            'offset': start, 'next_offset':start + len(content) if start + len(content) < len(text) else None})
            if record_id or len(matches) >= 3:
                break
        return {'matches': matches, 'session_id': self.history_store.session_id, 'evidence_only': True}

    def context_summary(self):
        return {'checkpoint': self.history_store.checkpoint(), 'usage': self.history_store.compaction_usage(),
                'running': self.compaction_task is not None}

    def cancel_compaction(self):
        if self.compaction_cancel is None:
            return False
        self.compaction_cancel.set()
        return True

    async def compact_context(self):
        from agent_workbench.core.compaction import SemanticCompactor
        from agent_workbench.core.loop import SYSTEM_PROMPT
        self.require_idle()
        self.sync_conversation_provider()
        self.compaction_cancel = asyncio.Event()
        self.compaction_task = asyncio.current_task()
        try:
            compactor = SemanticCompactor(self.conversation, self.provider, self.context_budget(self.config))
            await compactor.prepare({'role':'system', 'content':SYSTEM_PROMPT}, [], self.build_tools().schemas(),
                                    self.compaction_cancel, force=True)
            return {**self.context_summary(), 'status': compactor.last_status, 'running': False}
        except asyncio.CancelledError:
            if not self.compaction_cancel.is_set():
                raise
            return {'status':'cancelled'}
        finally:
            self.compaction_cancel = None
            self.compaction_task = None

    async def migrate_data(self, destination):
        from agent_workbench.data_location import migrate_data
        self.require_idle()
        if self.paper_tasks or self.reindexing_papers or self.paper_classification_tasks:
            raise ValueError('Wait for imports to finish before migrating data')
        if self.data_location_pointer is None:
            raise ValueError('Explicit test data directories cannot change the user data location')
        self.maintenance_task = asyncio.current_task()
        try:
            # Synchronous copy keeps the local command transactional with respect to API writes.
            result = migrate_data(self.data_dir, Path(destination), self.data_location_pointer)
            self.restart_required = True
            return result
        finally:
            self.maintenance_task = None

    def list_sessions(self):
        return self.history_store.sessions()

    def new_session(self):
        self.require_idle()
        self.history_store.create(str(self.workspace.root) if self.workspace else None)
        return self.conversation_snapshot()

    def select_session(self, session_id):
        self.require_idle()
        selected = self.history_store.select(session_id)
        path = selected['workspace']
        self.workspace = Workspace(path) if path and Path(path).is_dir() else None
        self.permission_mode = 'ask'
        return self.conversation_snapshot()

    def delete_session(self, session_id):
        self.require_idle()
        self.history_store.delete(session_id)
        return self.select_session(self.history_store.session_id)

    def set_permission_mode(self, mode):
        self.require_idle()
        if mode not in {'read_only', 'ask', 'auto_edit'}:
            raise ValueError('Unknown permission mode')
        if self.workspace is None and mode != 'ask':
            raise ValueError('Select a workspace before granting permission')
        self.permission_mode = mode
        return {'mode': mode}

    def edit_workspace_file(self, path: str, expected_text: str, replacement_text: str):
        if self.permission_mode == 'read_only':
            raise PermissionError('Workspace is read-only')
        target = self.workspace.resolve_path(path)
        sensitive = any(part.casefold() in {'.git', '.ssh', '.aws', '.azure', '.codex', '.dsh'} for part in target.relative_to(self.workspace.root).parts)
        sensitive |= target.name.casefold().startswith('.env') or target.name.casefold() in {'credentials.json', 'credentials', 'secrets.json', 'id_rsa', 'id_ed25519'}
        sensitive |= target.suffix.casefold() in {'.pem', '.key', '.pfx', '.p12'}
        if self.permission_mode == 'auto_edit' and sensitive:
            raise PermissionError('Sensitive files are not allowed in automatic edit mode')
        try:
            self.workspace.propose_edit(path, expected_text, replacement_text)
        except ApprovalRequired as exc:
            if self.permission_mode != 'auto_edit':
                raise
            proposal = exc.proposal
            self.history_store.record_audit(self.workspace.root, {'status': 'proposed', 'path': path, 'diff': proposal.diff,
                                                                 'original_text': proposal.original_text, 'approval_id': proposal.approval_id})
            result = self.workspace.approve_edit(proposal.approval_id)
            self.history_store.record_audit(self.workspace.root, {'status': result.status, 'path': path, 'diff': result.diff,
                                                                 'approval_id': result.approval_id})
            return {'status': result.status, 'relative_path': path, 'diff': result.diff}

    def workspace_audit(self):
        return self.history_store.audit(self.workspace.root) if self.workspace else []

    async def confirm_card_draft(self, draft_id):
        with self._card_write():
            item = self.card_drafts.get(draft_id)
            if item['update_card_id']:
                current = next((asdict(c) for c in self.cards.list_cards() if c.card_id == item['update_card_id']), None)
                if current != item['original']:
                    raise ValueError('Card changed after draft creation; review a new draft')
                card = self.cards.update(item['update_card_id'], title=item['title'], content=item['content'], tags=item['tags'], sources=item['sources'])
            else:
                fields = {key: item[key] for key in CardDraft.__dataclass_fields__ if key in item}
                self.cards._drafts[item['approval_id']] = CardDraft(**fields)
                card = self.cards.approve(item['approval_id'])
            self.card_drafts.discard(draft_id)
            result = asdict(card)
            try:
                await asyncio.wait_for(self.knowledge.upsert(chunk_id=card.card_id, source_type='card', title=card.title,
                                          text=card.content, source_path=str(card.path)), timeout=30)
                result['index_status'] = 'ready'
            except Exception as exc:
                result.update(index_status='failed', warning='Card saved, but indexing failed: ' + sanitize_error_message(exc))
            return result

    def context_estimate(self, message='', attachment_ids=None):
        from agent_workbench.core.loop import SYSTEM_PROMPT
        from agent_workbench.core.compaction import request_size
        self.sync_conversation_provider()
        budget = self.context_budget(self.config)
        system = SYSTEM_PROMPT
        if self.config.guidance.enabled:
            system += '\n' + max((p.prompt for p in (self.config.guidance.profiles.fast, self.config.guidance.profiles.standard, self.config.guidance.profiles.deep)), key=len)
        checkpoint, _, history = self.conversation.context_state()
        summary = checkpoint['summary'] if checkpoint else None
        content=self.attachments.content(message,attachment_ids or [])
        current=[{'role':'user','content':content}]
        used = request_size({'role':'system','content':system}, history, current, self.build_tools().schemas(), summary)
        image_count=sum(1 for m in [m for turn in history for m in turn]+current if isinstance(m.get('content'),list)
                        for p in m['content'] if p.get('type')=='image_url')
        input_limit = budget.window - budget.output_tokens - budget.reserve
        covered = sum(r['id'] <= checkpoint['through_id'] and r['status'] in {'stop','length'} for r in self.history_store.records()) if checkpoint else 0
        info = {'estimator':'utf8_bytes_conservative','estimated_input':used,'input_limit':input_limit,
                'image_count':image_count,'image_estimate':image_count * 8192,
                'output_reserved':budget.output_tokens, 'window':budget.window, 'summary_kind':'semantic' if summary else None,
                'compacted_turns':covered, 'history_turns':len(history), 'dropped_turns':0, 'truncated_tools':0,
                'compaction_pending':used >= int(input_limit * .85), 'compaction_usage':self.history_store.compaction_usage()}
        from agent_workbench.providers.reasoning import reasoning_capabilities
        capacity = self.config.context_window or reasoning_capabilities(self.config).get('context_window')
        return {**info, 'capacity': capacity, 'capacity_source': 'user' if self.config.context_window else 'model_profile' if capacity else 'unknown',
                'usage': self.conversation.snapshot().get('usage', {}), 'protected_default': capacity is None}

    def sync_conversation_provider(self):
        import json
        self.conversation.provider_key = json.dumps([self.config.provider_mode, self.config.base_url, self.config.model])
