"""Durable, non-blocking model proposals; only explicit confirmation writes cards."""
from __future__ import annotations
import json
import uuid
from dataclasses import asdict


class DraftStore:
    def __init__(self, history, cards):
        self.history, self.cards = history, cards
        with history.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS card_drafts (draft_id TEXT PRIMARY KEY, payload TEXT NOT NULL)')

    def list(self):
        with self.history.connect() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT payload FROM card_drafts ORDER BY rowid')]

    def get(self, draft_id):
        with self.history.connect() as db:
            row = db.execute('SELECT payload FROM card_drafts WHERE draft_id=?', (draft_id,)).fetchone()
        if row is None:
            raise ValueError('Draft not found')
        return json.loads(row[0])

    def put(self, item):
        with self.history.connect() as db:
            db.execute('INSERT OR REPLACE INTO card_drafts VALUES (?,?)', (item['draft_id'], json.dumps(item, ensure_ascii=False)))
        return item

    def propose(self, title: str, content: str, reason: str, tags: list[str] | None = None,
                sources: list[str] | None = None, update_card_id: str | None = None):
        if len(title) > 160 or len(content) > 50000 or not reason.strip():
            raise ValueError('A concise title, bounded content and a save reason are required')
        if len(self.list()) >= 100:
            raise ValueError('Review pending card drafts before adding more')
        existing = next((card for card in self.cards.list_cards() if card.card_id == update_card_id), None)
        if update_card_id and not existing:
            raise ValueError('Card to update was not found')
        for draft in self.list():
            if draft['title'].casefold() == title.strip().casefold() and draft['content'] == content.strip():
                return draft
        proposal = self.cards.propose(title, content, tags, sources)
        self.cards._drafts.pop(proposal.approval_id)
        item = {**asdict(proposal), 'draft_id': uuid.uuid4().hex, 'reason': reason.strip(),
                'session_id': self.history.session_id, 'update_card_id': update_card_id,
                'original': asdict(existing) if existing else None,
                'duplicates': [{'card_id': c.card_id, 'title': c.title} for c in self.cards.list_cards()
                               if c.title.casefold() == title.strip().casefold()]}
        return self.put(item)

    def edit(self, draft_id, title, content, tags=None, sources=None):
        item = self.get(draft_id)
        if not title.strip() or not content.strip() or len(title) > 160 or len(content) > 50000:
            raise ValueError('Invalid card draft')
        item.update(title=title.strip(), content=content.strip(), tags=tags or [], sources=sources or [])
        return self.put(item)

    def discard(self, draft_id):
        self.get(draft_id)
        with self.history.connect() as db:
            db.execute('DELETE FROM card_drafts WHERE draft_id=?', (draft_id,))
