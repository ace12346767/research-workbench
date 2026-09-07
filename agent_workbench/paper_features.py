"""Paper library operations kept independent from chat history and routing."""
import asyncio
from dataclasses import asdict

from agent_workbench.core.security import sanitize_error_message
from agent_workbench.knowledge.paths import managed_file
from agent_workbench.knowledge.paper_analysis import extract_pages, classify


class PaperFeatures:
    def paper_path(self, paper_id):
        try:
            record = self.papers.get(paper_id)
        except KeyError as exc:
            raise FileNotFoundError('Paper not found') from exc
        return managed_file(record.stored_path, self.papers.papers_dir).resolve()

    def start_paper_classification(self, paper_id, *, confirmed=False, automatic=False):
        if not automatic:
            self.require_idle()
        if not confirmed:
            raise ValueError('Confirm model usage before classification')
        record = self.papers.get(paper_id)
        if record.status != 'ready':
            raise ValueError('Wait for paper indexing to finish')
        if paper_id in self.paper_classification_tasks:
            return asdict(record)
        self.papers.update(paper_id, classification_status='queued', classification_error=None)
        self.paper_classification_tasks[paper_id] = asyncio.create_task(self._classify_paper(paper_id))
        return asdict(self.papers.get(paper_id))

    async def _classify_paper(self, paper_id):
        usage = {}
        try:
            async with self.paper_classification_lock:
                while self.active_cancellations or self.maintenance_task or self.compaction_task or self.provider_diagnostic_active or self.attachment_uploads or self.card_write_task:
                    await asyncio.sleep(.15)
                self.paper_classification_active = asyncio.current_task()
                self.papers.update(paper_id, classification_status='classifying')
                # Keep a generous reserve for schema and output under small provider windows.
                limit = min(24000, self.context_budget(self.config).window - 14000)
                if limit < 1000:
                    raise ValueError('Context window too small for paper classification')
                pages = await asyncio.to_thread(extract_pages, self.paper_path(paper_id), limit)
                topics = sorted({p.metadata.get('topic') for p in self.papers.list() if p.metadata.get('topic')})
                result = await classify(self.provider, pages, topics, usage, input_limit=self.context_budget(self.config).window-5120)
                self.papers.accept_classification(paper_id, result, usage)
        except asyncio.CancelledError:
            self.papers.update(paper_id, classification_status='cancelled', classification_usage=usage)
            raise
        except Exception as exc:
            self.papers.update(paper_id, classification_status='failed', classification_error=sanitize_error_message(exc), classification_usage=usage)
        finally:
            if self.paper_classification_active is asyncio.current_task():
                self.paper_classification_active = None
            self.paper_classification_tasks.pop(paper_id, None)

    async def cancel_paper_classification(self, paper_id):
        self.papers.get(paper_id)
        task = self.paper_classification_tasks.get(paper_id)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self.paper_classification_tasks.pop(paper_id, None)
            if self.papers.get(paper_id).classification_status == 'queued':
                self.papers.update(paper_id, classification_status='cancelled')
        return asdict(self.papers.get(paper_id))

    def paper_sources(self, ids):
        if len(ids) > 12 or len(set(ids)) != len(ids):
            raise ValueError('Select at most 12 distinct papers')
        return [str(self.paper_path(key)) for key in ids]

    def read_paper(self, paper_id: str, page: int = 1):
        import pymupdf
        with pymupdf.open(self.paper_path(paper_id)) as pdf:
            if page < 1 or page > len(pdf):
                raise ValueError('Page is outside this PDF')
            text = pdf[page-1].get_text()
            return {'paper_id': paper_id, 'page': page, 'pages': len(pdf),
                    'text': text[:16000], 'truncated': len(text)>16000, 'evidence_only': True}

    def paper_page_image(self, paper_id, page):
        import pymupdf
        with pymupdf.open(self.paper_path(paper_id)) as pdf:
            if page < 1 or page > len(pdf):
                raise ValueError('Page is outside this PDF')
            target = pdf[page-1]
            scale = min(1.5, 1800/max(target.rect.width, target.rect.height))
            return target.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).tobytes('png')
