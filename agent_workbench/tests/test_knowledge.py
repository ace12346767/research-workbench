from __future__ import annotations

import asyncio
from pathlib import Path

import fitz
import numpy as np

from agent_workbench.knowledge.cards import CardManager
from agent_workbench.knowledge.pdf_loader import load_pdf_chunks
from agent_workbench.knowledge.store import KnowledgeStore


async def keyword_embed(texts: list[str]) -> list[list[float]]:
    vectors = []
    for text in texts:
        lowered = text.lower()
        vectors.append([
            float("router" in lowered or "路由" in lowered),
            float("workspace" in lowered or "工作区" in lowered),
        ])
    return vectors


def test_card_draft_does_not_write_until_approved(tmp_path: Path) -> None:
    manager = CardManager(tmp_path / "cards")
    draft = manager.propose("Abstain strategy", "Use abstain for ambiguous routing", ["router"])

    assert list((tmp_path / "cards").glob("*.md")) == []
    approved = manager.approve(draft.approval_id)
    repeated = manager.approve(draft.approval_id)

    assert approved.status == "applied"
    assert repeated.path == approved.path
    assert Path(approved.path).read_text(encoding="utf-8").startswith("---")


def test_knowledge_store_returns_source_metadata(tmp_path: Path) -> None:
    store = KnowledgeStore(tmp_path / "knowledge", embed=keyword_embed)
    asyncio.run(
        store.upsert(
            chunk_id="card-1",
            source_type="card",
            title="Router notes",
            text="The router abstains when the margin is low.",
            source_path="cards/router.md",
        )
    )
    asyncio.run(
        store.upsert(
            chunk_id="card-2",
            source_type="card",
            title="Workspace notes",
            text="The workspace rejects parent path escapes.",
            source_path="cards/workspace.md",
        )
    )

    results = asyncio.run(store.search("router margin", top_k=1))
    assert results[0].chunk_id == "card-1"
    assert results[0].source_path == "cards/router.md"
    reopened = KnowledgeStore(tmp_path / "knowledge", embed=keyword_embed)
    assert asyncio.run(reopened.search("workspace", top_k=1))[0].chunk_id == "card-2"


def test_pdf_loader_preserves_page_number(tmp_path: Path) -> None:
    pdf_path = tmp_path / "paper.pdf"
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "Dynamic routing uses an abstain state.")
    document.save(pdf_path)
    document.close()

    chunks = load_pdf_chunks(pdf_path)
    assert chunks
    assert chunks[0].page_number == 1
    assert chunks[0].source_path == str(pdf_path)
    assert "abstain" in chunks[0].text


def test_cards_persist_sources_and_can_be_updated_and_deleted(tmp_path: Path) -> None:
    cards_dir = tmp_path / "cards"
    manager = CardManager(cards_dir)
    draft = manager.propose(
        "Routing",
        "Use abstain for ambiguous requests.",
        ["router"],
        ["paper.pdf#page=3"],
    )
    approved = manager.approve(draft.approval_id)

    reloaded = CardManager(cards_dir)
    cards = reloaded.list_cards()
    assert cards[0].card_id == approved.card_id
    assert cards[0].sources == ["paper.pdf#page=3"]

    updated = reloaded.update(
        approved.card_id,
        title="Routing policy",
        content="Prefer abstain when evidence is weak.",
        tags=["router", "safety"],
        sources=["paper.pdf#page=3"],
    )
    assert updated.title == "Routing policy"
    assert "evidence is weak" in Path(updated.path).read_text(encoding="utf-8")

    deleted = reloaded.delete(approved.card_id)
    assert deleted.card_id == approved.card_id
    assert reloaded.list_cards() == []


def test_knowledge_store_deletes_all_chunks_for_a_source(tmp_path: Path) -> None:
    store = KnowledgeStore(tmp_path / "knowledge", embed=keyword_embed)
    for chunk_id in ("paper-1", "paper-2"):
        asyncio.run(
            store.upsert(
                chunk_id=chunk_id,
                source_type="paper",
                title="Paper",
                text="router evidence",
                source_path="papers/paper.pdf",
            )
        )

    assert asyncio.run(store.delete_source("papers/paper.pdf")) == 2
    assert asyncio.run(store.search("router")) == []
