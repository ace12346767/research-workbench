from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from agent_workbench.knowledge.models import KnowledgeChunk


CHUNKER_VERSION = "layout-paragraph-v1"


@dataclass
class TextLine:
    text: str
    bbox: tuple[float, float, float, float]
    block: int
    size: float
    bold: bool


def _lines(page) -> list[TextLine]:
    result = []
    # Exclude image bytes: raster content is not text and this loader does not OCR.
    data = page.get_text("dict", flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES)
    for index, block in enumerate(data["blocks"]):
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            spans = [span for span in line["spans"] if span["text"].strip()]
            text = "".join(span["text"] for span in line["spans"]).strip()
            if not text or not spans:
                continue
            dominant = max(spans, key=lambda span: len(span["text"]))
            result.append(TextLine(text, tuple(line["bbox"]), index,
                                   round(dominant["size"], 1), bool(dominant["flags"] & 16)))
    return result


def _reading_order(lines: list[TextLine], width: float) -> list[TextLine]:
    ordered = sorted(lines, key=lambda line: (line.bbox[1], line.bbox[0]))
    middle = width / 2
    left = [line for line in lines if line.bbox[2] < middle]
    right = [line for line in lines if line.bbox[0] > middle]
    # Only use column order when both sides have multiple lines and a clear gutter.
    if len(left) < 2 or len(right) < 2 or min(line.bbox[0] for line in right) - max(line.bbox[2] for line in left) < 12:
        return ordered
    result, band = [], []

    def flush():
        result.extend(line for line in band if line.bbox[2] < middle)
        result.extend(line for line in band if line.bbox[0] > middle)
        band.clear()

    for line in ordered:
        if line.bbox[0] <= middle <= line.bbox[2]:
            flush()
            result.append(line)
        else:
            band.append(line)
    flush()
    return result


def _is_heading(line: TextLine, body_size: float) -> bool:
    short = len(line.text) <= 160 and not line.text.endswith((".", ";", ","))
    return short and (line.size >= body_size * 1.15 or
                      (line.bold and line.size >= body_size and len(line.text) <= 100))


def _paragraphs(lines: list[TextLine], body_size: float):
    previous = None
    text = ""
    heading = False
    for line in lines:
        is_heading = _is_heading(line, body_size)
        same_paragraph = (previous is not None and not heading and not is_heading
                          and line.block == previous.block
                          and abs(line.bbox[0] - previous.bbox[0]) < 20
                          and 0 < line.bbox[1] - previous.bbox[1] < previous.size * 2)
        if text and not same_paragraph:
            yield text, heading
            text = ""
        text = f"{text}\n{line.text}" if text else line.text
        previous, heading = line, is_heading
    if text:
        yield text, heading


def _bounded_parts(text: str, limit: int):
    while len(text) > limit:
        boundary = max((i for i, character in enumerate(text[:limit + 1])
                        if character.isspace()), default=0)
        boundary = boundary or limit
        yield text[:boundary].strip()
        text = text[boundary:].strip()
    if text:
        yield text


def load_pdf_chunks(path: str | Path, *, max_chars: int = 1_500) -> list[KnowledgeChunk]:
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    pdf_path = Path(path).resolve()
    digest = hashlib.sha256(pdf_path.read_bytes()).hexdigest()[:16]
    chunks: list[KnowledgeChunk] = []
    section = None
    with pymupdf.open(pdf_path) as document:
        sizes = Counter()
        for page in document:
            for line in _lines(page):
                sizes[line.size] += len(line.text)
        body_size = sizes.most_common(1)[0][0] if sizes else 11.0
        for page_index, page in enumerate(document, 1):
            pending = ""
            chunk_index = 0

            def flush():
                nonlocal pending, chunk_index
                if not pending:
                    return
                chunk_index += 1
                chunks.append(KnowledgeChunk(
                    chunk_id=f"paper-{digest}-p{page_index}-c{chunk_index}",
                    source_type="paper", title=pdf_path.name, text=pending,
                    source_path=str(pdf_path), page_number=page_index,
                    section_title=section, parser_version=f"pymupdf:{pymupdf.VersionBind}",
                    chunker_version=CHUNKER_VERSION,
                ))
                pending = ""

            lines = _reading_order(_lines(page), page.rect.width)
            for text, heading in _paragraphs(lines, body_size):
                if heading:
                    flush()
                    section = text
                for part in _bounded_parts(text, max_chars):
                    candidate = f"{pending}\n\n{part}" if pending else part
                    if len(candidate) > max_chars:
                        flush()
                    pending = f"{pending}\n\n{part}" if pending else part
            flush()
    return chunks
