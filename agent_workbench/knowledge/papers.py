from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from agent_workbench.knowledge.paths import managed_file


@dataclass(slots=True)
class PaperRecord:
    paper_id: str
    filename: str
    stored_path: str
    sha256: str
    status: str = "importing"
    page_count: int = 0
    chunk_count: int = 0
    error: str | None = None
    duplicate: bool = False
    metadata: dict = field(default_factory=dict)
    manual_fields: list[str] = field(default_factory=list)
    revision: int = 0
    classification_status: str = 'unclassified'
    classification_error: str | None = None
    classification_usage: dict = field(default_factory=dict)
    topic_slot: int | None = None


class PaperManager:
    def __init__(self, papers_dir: str | Path) -> None:
        self.papers_dir = Path(papers_dir)
        self.papers_dir.mkdir(parents=True, exist_ok=True)
        self.manifest = self.papers_dir / "manifest.json"

    def _records(self) -> list[PaperRecord]:
        managed_file(self.manifest, self.papers_dir)
        if not self.manifest.is_file():
            return []
        try:
            payload = json.loads(self.manifest.read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                raise ValueError('manifest must be an array')
            records = [PaperRecord(**item) for item in payload]
            seen = set()
            for record in records:
                from agent_workbench.knowledge.paper_analysis import PaperMetadata
                PaperMetadata.model_validate(record.metadata)
                if (not isinstance(record.revision, int) or record.revision < 0
                        or not isinstance(record.manual_fields, list)
                        or any(key not in PaperMetadata.model_fields or key == 'evidence' for key in record.manual_fields)
                        or record.classification_status not in {'unclassified','manual','queued','classifying','ready','failed','cancelled'}
                        or not isinstance(record.classification_usage, dict)
                        or record.topic_slot is not None and (not isinstance(record.topic_slot, int) or record.topic_slot < 0)):
                    raise ValueError('invalid paper classification metadata')
                if (not isinstance(record.paper_id, str) or record.paper_id in seen
                        or not re.fullmatch(r'[A-Za-z0-9_-]+', record.paper_id)
                        or any(not isinstance(value, str) for value in (record.filename, record.stored_path, record.sha256))
                        or record.status not in {'importing', 'ready', 'failed'}
                        or any(not isinstance(value, int) or value < 0 for value in (record.page_count, record.chunk_count))):
                    raise ValueError('invalid paper metadata')
                seen.add(record.paper_id)
            return records
        except (ValueError, TypeError) as exc:
            raise ValueError('Invalid paper manifest; original file preserved') from exc

    def _save(self, records: list[PaperRecord]) -> None:
        managed_file(self.manifest, self.papers_dir)
        if self.manifest.is_file():
            original = self.manifest.read_bytes()
            if any('metadata' not in item for item in json.loads(original)):
                backup = self.papers_dir / 'manifest.pre-v15.json'
                managed_file(backup, self.papers_dir)
                if not backup.exists():
                    with backup.open('xb') as handle:
                        handle.write(original)
        fd, temp_name = tempfile.mkstemp(prefix=".manifest.", suffix=".tmp", dir=self.papers_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                json.dump([asdict(item) for item in records], handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.manifest)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise

    def stage(self, filename: str, content: bytes) -> tuple[PaperRecord, bool]:
        if not content or len(content) > 50 * 1024 * 1024:
            raise ValueError("PDF must be between 1 byte and 50 MB")
        safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', '_', Path(filename).name).strip(' .')
        if not safe_name.lower().endswith(".pdf"):
            raise ValueError("only PDF files are supported")
        digest = hashlib.sha256(content).hexdigest()
        records = self._records()
        for record in records:
            if record.sha256 == digest:
                record.duplicate = True
                return record, True
        paper_id = f"paper-{uuid.uuid4().hex}"
        target = self.papers_dir / f"{digest[:12]}-{safe_name}"
        managed_file(target, self.papers_dir)
        target.write_bytes(content)
        record = PaperRecord(
            paper_id=paper_id,
            filename=safe_name,
            stored_path=str(target),
            sha256=digest,
        )
        records.append(record)
        self._save(records)
        return record, False

    def get(self, paper_id: str) -> PaperRecord:
        for record in self._records():
            if record.paper_id == paper_id:
                return record
        raise KeyError(paper_id)

    def update(self, paper_id: str, **changes: object) -> PaperRecord:
        records = self._records()
        for record in records:
            if record.paper_id == paper_id:
                for key, value in changes.items():
                    setattr(record, key, value)
                record.duplicate = False
                self._save(records)
                return record
        raise KeyError(paper_id)

    def list(self) -> list[PaperRecord]:
        return sorted(self._records(), key=lambda item: item.filename.casefold())

    def edit_metadata(self, paper_id, metadata, revision):
        from agent_workbench.knowledge.paper_analysis import PaperMetadata
        record = self.get(paper_id)
        if record.revision != revision:
            raise ValueError('Paper changed; reload before saving')
        values = PaperMetadata.model_validate(metadata).model_dump(mode='json')
        current = PaperMetadata.model_validate(record.metadata).model_dump(mode='json')
        changed = [key for key in values if values[key] != current[key] and key != 'evidence']
        # Evidence is sourced by the classifier, never supplied by an edit form.
        values['evidence'] = {key: val for key, val in current['evidence'].items() if key not in changed}
        return self.update(paper_id, metadata=values, manual_fields=sorted(set(record.manual_fields + changed)),
                           revision=record.revision + 1, topic_slot=self.topic_slot(values.get('topic')),
                           classification_status='manual' if 'topic' in changed else record.classification_status)

    def topic_slot(self, topic):
        if not topic:
            return None
        records = self.list()
        existing = next((p.topic_slot for p in records if p.metadata.get('topic') == topic and p.topic_slot is not None), None)
        if existing is not None:
            return existing
        return max((p.topic_slot for p in records if p.topic_slot is not None), default=-1) + 1

    def accept_classification(self, paper_id, metadata, usage):
        record = self.get(paper_id)
        for key in record.manual_fields:
            metadata[key] = record.metadata.get(key)
            metadata.get('evidence', {}).pop(key, None)
        return self.update(paper_id, metadata=metadata, revision=record.revision + 1,
                           classification_status='ready', classification_error=None, classification_usage=usage,
                           topic_slot=self.topic_slot(metadata.get('topic')))

    def rename_topic(self, source, target):
        target = target.strip()
        if not target or len(target) > 60 or len(source) > 60:
            raise ValueError('Topic name must contain 1-60 characters')
        records = self._records()
        matches = [p for p in records if p.metadata.get('topic', '') == source]
        if not matches:
            raise ValueError('Topic no longer exists')
        slot = next((p.topic_slot for p in records if p.metadata.get('topic') == target and p.topic_slot is not None), None)
        if slot is None:
            slot = next((p.topic_slot for p in matches if p.topic_slot is not None), self.topic_slot(target))
        for p in matches:
            p.metadata = {**p.metadata, 'topic': target}
            p.topic_slot = slot
            p.manual_fields = sorted(set(p.manual_fields + ['topic']))
            p.revision += 1
        self._save(records)
        return {'updated': len(matches), 'topic': target}

    def delete(self, paper_id: str) -> PaperRecord:
        records = self._records()
        for index, record in enumerate(records):
            if record.paper_id == paper_id:
                managed_file(record.stored_path, self.papers_dir).unlink(missing_ok=True)
                records.pop(index)
                self._save(records)
                record.status = "deleted"
                return record
        raise KeyError(paper_id)
