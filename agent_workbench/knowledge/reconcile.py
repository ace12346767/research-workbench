from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from agent_workbench.core.security import sanitize_error_message
from agent_workbench.knowledge.paths import managed_file


class KnowledgeReconciler:
    def __init__(self, cards, papers, store, reindex_paper):
        self.cards, self.papers, self.store = cards, papers, store
        self.reindex_paper = reindex_paper

    def inspect(self) -> dict:
        issues, recognized = [], set()
        indexed = self.store.source_inventory()

        def issue(kind, path, source_id='', repairable=False):
            issues.append(dict(kind=kind, path=str(path), source_id=source_id, repairable=repairable))

        # A broken manifest must not turn every paper index into a deletion candidate.
        try:
            papers = self.papers.list()
            manifest_valid = True
        except (OSError, ValueError):
            issue('invalid_manifest', self.papers.manifest)
            papers, manifest_valid = [], False
        for target in sorted(self.cards.cards_dir.glob('*.md')):
            recognized.add(str(target))
            try:
                card = self.cards._load(target)
            except (OSError, ValueError):
                issue('invalid_card', target)
                continue
            rows = indexed.get(str(target), [])
            if not rows:
                issue('card_index_missing', target, card.card_id, True)
            elif len(rows) != 1 or any(rows[0][key] != value for key, value in
                    dict(chunk_id=card.card_id, source_type='card', title=card.title, text=card.content).items()):
                issue('card_index_stale', target, card.card_id, True)

        paper_paths = set()
        for paper in papers:
            raw_path = Path(paper.stored_path)
            path = str(raw_path.resolve())
            recognized.add(path)
            paper_paths.add(raw_path.absolute())
            try:
                target = managed_file(raw_path, self.papers.papers_dir)
            except (OSError, ValueError):
                issue('unsafe_paper_path', raw_path, paper.paper_id)
                continue
            try:
                size = target.stat().st_size
                if size > 50 * 1024 * 1024:
                    issue('paper_too_large', path, paper.paper_id)
                    continue
                with target.open('rb') as handle:
                    digest = hashlib.file_digest(handle, 'sha256').hexdigest()
            except FileNotFoundError:
                issue('paper_missing', path, paper.paper_id)
                continue
            except OSError:
                issue('paper_unreadable', path, paper.paper_id)
                continue
            if digest != paper.sha256:
                issue('paper_hash_mismatch', path, paper.paper_id)
                continue
            rows = indexed.get(path, [])
            if not rows:
                issue('paper_index_missing', path, paper.paper_id, True)
            elif (paper.status != 'ready' or paper.chunk_count != len(rows)
                  or paper.page_count != max(row['page_number'] or 0 for row in rows)):
                issue('paper_state_mismatch', path, paper.paper_id, True)
            elif paper.error:
                issue('paper_last_error', path, paper.paper_id, True)

        if manifest_valid:
            for target in self.papers.papers_dir.glob('*.pdf'):
                if target.absolute() not in paper_paths:
                    recognized.add(str(target.resolve()))
                    issue('untracked_pdf', target)
        for path, rows in indexed.items():
            if path in recognized:
                continue
            source_type = rows[0]['source_type']
            if source_type == 'paper' and not manifest_valid:
                continue
            root = self.cards.cards_dir if source_type == 'card' else self.papers.papers_dir
            try:
                target = managed_file(path, root)
            except (OSError, ValueError):
                issue('unmanaged_index', path)
                continue
            if source_type not in {'card', 'paper'} or target.exists():
                issue('unmanaged_index', path)
            else:
                issue('orphan_index', path, repairable=True)
        return dict(status='issues' if issues else 'healthy', issues=issues,
                    repairable_count=sum(item['repairable'] for item in issues), indexed_sources=len(indexed))

    async def repair(self) -> dict:
        before = await asyncio.to_thread(self.inspect)
        repaired, errors = [], []
        for item in before['issues']:
            if not item['repairable']:
                continue
            # Refresh evidence before each write; never apply an old browser report.
            current = await asyncio.to_thread(self.inspect)
            if item not in current['issues']:
                continue
            try:
                kind, path = item['kind'], item['path']
                if kind.startswith('card_index_'):
                    card = self.cards._load(Path(path))
                    await asyncio.wait_for(self.store.replace_source(path, [dict(
                        chunk_id=card.card_id, source_type='card', title=card.title,
                        text=card.content, source_path=path,
                    )]), timeout=30)
                elif kind in {'paper_index_missing', 'paper_last_error'}:
                    await asyncio.wait_for(self.reindex_paper(item['source_id']), timeout=120)
                elif kind == 'paper_state_mismatch':
                    self.papers.update(item['source_id'], status='ready', error=None,
                                       **self.store.source_summary(path))
                elif kind == 'orphan_index':
                    await self.store.delete_source(path)
                repaired.append(item)
            except Exception as exc:
                errors.append({**item, 'error': sanitize_error_message(exc)})
        return dict(repaired=repaired, errors=errors, after=await asyncio.to_thread(self.inspect))
