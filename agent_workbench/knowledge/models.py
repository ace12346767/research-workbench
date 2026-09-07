from __future__ import annotations

from pydantic import BaseModel


class KnowledgeChunk(BaseModel):
    chunk_id: str
    source_type: str
    title: str
    text: str
    source_path: str
    page_number: int | None = None
    section_title: str | None = None
    parser_version: str | None = None
    chunker_version: str | None = None
    score: float | None = None
