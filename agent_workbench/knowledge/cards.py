from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from agent_workbench.knowledge.paths import managed_file


@dataclass(slots=True)
class CardDraft:
    approval_id: str
    card_id: str
    title: str
    content: str
    tags: list[str]
    sources: list[str]
    status: str = "pending"
    path: str | None = None
    created_by: str = "user_confirmed_agent_draft"


class CardApprovalRequired(RuntimeError):
    def __init__(self, draft: CardDraft) -> None:
        super().__init__(f"approval required: {draft.approval_id}")
        self.draft = draft


class CardManager:
    def __init__(self, cards_dir: str | Path) -> None:
        self.cards_dir = Path(cards_dir)
        self.cards_dir.mkdir(parents=True, exist_ok=True)
        self._drafts: dict[str, CardDraft] = {}

    def propose(
        self,
        title: str,
        content: str,
        tags: list[str] | None = None,
        sources: list[str] | None = None,
    ) -> CardDraft:
        now = datetime.now().astimezone()
        card_id = f"card_{now:%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}"
        draft = CardDraft(
            approval_id=f"card-approval-{uuid.uuid4().hex}",
            card_id=card_id,
            title=title.strip(),
            content=content.strip(),
            tags=[tag.strip() for tag in (tags or []) if tag.strip()],
            sources=[source.strip() for source in (sources or []) if source.strip()],
        )
        if not draft.title or not draft.content:
            raise ValueError("card title and content are required")
        self._drafts[draft.approval_id] = draft
        return draft

    def approve(self, approval_id: str) -> CardDraft:
        draft = self._drafts[approval_id]
        if draft.status == "applied":
            return draft
        if draft.status != "pending":
            raise ValueError("approval is no longer pending")
        target = self.cards_dir / f"{draft.card_id}.md"
        self._write(draft, target)
        draft.status = "applied"
        draft.path = str(target)
        return draft

    def _write(self, draft: CardDraft, target: Path) -> None:
        managed_file(target, self.cards_dir)
        title = json.dumps(draft.title, ensure_ascii=False)
        tags = json.dumps(draft.tags, ensure_ascii=False)
        sources = json.dumps(draft.sources, ensure_ascii=False)
        created_at = datetime.now().astimezone().isoformat(timespec="seconds")
        text = (
            "---\n"
            f"id: {draft.card_id}\n"
            f"title: {title}\n"
            f"tags: {tags}\n"
            f"sources: {sources}\n"
            f"created_at: {created_at}\n"
            f"created_by: {draft.created_by}\n"
            "---\n\n"
            f"# {draft.title}\n\n{draft.content}\n"
        )
        fd, temp_name = tempfile.mkstemp(prefix=f".{draft.card_id}.", suffix=".tmp", dir=self.cards_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise

    def _load(self, target: Path) -> CardDraft:
        managed_file(target, self.cards_dir)
        if target.stat().st_size > 2 * 1024 * 1024:
            raise ValueError('Card exceeds the 2 MiB read limit')
        lines = target.read_text(encoding="utf-8-sig").splitlines()
        if not lines or lines[0] != '---':
            raise ValueError(f"invalid card file: {target.name}")
        try:
            closing = lines.index('---', 1)
        except ValueError:
            raise ValueError(f"invalid card file: {target.name}") from None
        metadata: dict[str, str] = {}
        for line in lines[1:closing]:
            if ":" in line:
                key, value = line.split(":", 1)
                metadata[key.strip()] = value.strip()
        body = '\n'.join(lines[closing + 1:]).strip()
        body_lines = body.splitlines()
        if body_lines and body_lines[0].startswith("# "):
            body = "\n".join(body_lines[1:]).strip()
        card_id = metadata.get("id") or target.stem
        title = json.loads(metadata.get('title', json.dumps(target.stem)))
        tags = json.loads(metadata.get('tags', '[]'))
        sources = json.loads(metadata.get('sources', '[]'))
        if (card_id != target.stem or not re.fullmatch(r'[A-Za-z0-9_-]+', card_id)
                or not isinstance(title, str) or not title.strip() or not body
                or any(not isinstance(values, list) or any(not isinstance(item, str) for item in values)
                       for values in (tags, sources))):
            raise ValueError(f'invalid card metadata: {target.name}')
        return CardDraft(
            approval_id=f"stored-{card_id}",
            card_id=card_id,
            title=title,
            content=body,
            tags=tags,
            sources=sources,
            status="applied",
            path=str(target),
            created_by=metadata.get("created_by", "user_created"),
        )

    def list_cards(self) -> list[CardDraft]:
        cards = []
        for target in self.cards_dir.glob("*.md"):
            try:
                cards.append(self._load(target))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
        return sorted(cards, key=lambda item: item.card_id, reverse=True)

    def _card_path(self, card_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", card_id):
            raise ValueError("invalid card id")
        target = self.cards_dir / f"{card_id}.md"
        if target.resolve().parent != self.cards_dir.resolve():
            raise ValueError("card path escapes directory")
        return target

    def update(
        self,
        card_id: str,
        *,
        title: str,
        content: str,
        tags: list[str] | None = None,
        sources: list[str] | None = None,
    ) -> CardDraft:
        target = self._card_path(card_id)
        if not target.is_file():
            raise KeyError(card_id)
        existing = self._load(target)
        existing.title = title.strip()
        existing.content = content.strip()
        existing.tags = [tag.strip() for tag in (tags or []) if tag.strip()]
        existing.sources = [source.strip() for source in (sources or []) if source.strip()]
        if not existing.title or not existing.content:
            raise ValueError("card title and content are required")
        self._write(existing, target)
        return existing

    def delete(self, card_id: str) -> CardDraft:
        target = self._card_path(card_id)
        if not target.is_file():
            raise KeyError(card_id)
        card = self._load(target)
        target.unlink()
        card.status = "deleted"
        return card

    def reject(self, approval_id: str) -> CardDraft:
        draft = self._drafts[approval_id]
        if draft.status == "pending":
            draft.status = "rejected"
        return draft
