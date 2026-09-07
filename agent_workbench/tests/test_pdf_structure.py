import asyncio
import sqlite3

import pymupdf
import pytest

from agent_workbench.knowledge.pdf_loader import load_pdf_chunks
from agent_workbench.knowledge.store import KnowledgeStore


def make_pdf(path, pages):
    with pymupdf.open() as document:
        for entries in pages:
            page = document.new_page()
            for x, y, text, size in entries:
                page.insert_text((x, y), text, fontsize=size)
        document.save(path)


def test_sections_do_not_merge_and_heading_continues_on_next_page(tmp_path):
    path = tmp_path / 'sections.pdf'
    make_pdf(path, [
        [(72, 72, '1 Introduction', 18),
         (72, 110, 'Background evidence for dynamic routing and workspace tools.', 11),
         (72, 170, '2 Methods', 18),
         (72, 210, 'Method evidence describes a conservative routing policy.', 11)],
        [(72, 72, 'The method continues with additional experimental evidence.', 11)],
    ])
    chunks = load_pdf_chunks(path)
    assert len(chunks) == 3
    assert [chunk.section_title for chunk in chunks] == ['1 Introduction', '2 Methods', '2 Methods']
    assert [chunk.page_number for chunk in chunks] == [1, 1, 2]
    assert 'Methods' not in chunks[0].text
    assert all(chunk.parser_version.startswith('pymupdf:') for chunk in chunks)
    assert all(chunk.chunker_version for chunk in chunks)


def test_two_columns_read_left_column_before_right_column(tmp_path):
    path = tmp_path / 'columns.pdf'
    make_pdf(path, [[
        (72, 72, 'Study of two column reading order', 18),
        (72, 120, 'Left first paragraph.', 11),
        (330, 120, 'Right first paragraph.', 11),
        (72, 170, 'Left second paragraph.', 11),
        (330, 170, 'Right second paragraph.', 11),
    ]])
    text = '\n'.join(chunk.text for chunk in load_pdf_chunks(path))
    assert text.index('Left second') < text.index('Right first')


def test_paragraph_boundary_is_preferred_to_fixed_character_slice(tmp_path):
    path = tmp_path / 'paragraphs.pdf'
    first = 'A complete paragraph with routing evidence.'
    second = 'Another complete paragraph with workspace evidence.'
    make_pdf(path, [[(72, 72, first, 11), (72, 120, second, 11)]])
    assert [chunk.text for chunk in load_pdf_chunks(path, max_chars=65)] == [first, second]


def test_long_paragraph_is_bounded_without_losing_words(tmp_path):
    path = tmp_path / 'long.pdf'
    text = ' '.join(['router'] * 40)
    make_pdf(path, [[(72, 72, text, 3)]])
    chunks = load_pdf_chunks(path, max_chars=40)
    assert all(len(chunk.text) <= 40 for chunk in chunks)
    assert ' '.join(chunk.text for chunk in chunks).split() == text.split()


@pytest.mark.parametrize('max_chars', [0, -1])
def test_invalid_chunk_limit_is_rejected_even_for_blank_pdf(tmp_path, max_chars):
    path = tmp_path / 'empty.pdf'
    make_pdf(path, [[]])
    with pytest.raises(ValueError, match='max_chars'):
        load_pdf_chunks(path, max_chars=max_chars)


def test_pdf_metadata_survives_atomic_index_replacement_and_reopen(tmp_path):
    path = tmp_path / 'sections.pdf'
    make_pdf(path, [[(72, 72, 'Methods', 18),
                     (72, 110, 'Routing evidence describes the method in detail.', 11)]])
    chunks = load_pdf_chunks(path)

    async def embed(texts):
        return [[1.0, 0.0] for _ in texts]

    root = tmp_path / 'index'
    store = KnowledgeStore(root, embed=embed)
    asyncio.run(store.replace_source(str(path), [chunk.model_dump(exclude={'score'}) for chunk in chunks]))
    reopened = KnowledgeStore(root, embed=embed)
    result = asyncio.run(reopened.search('method'))[0]
    assert result.section_title == 'Methods'
    assert result.parser_version == chunks[0].parser_version
    assert result.chunker_version == chunks[0].chunker_version
    assert result.source_path == str(path)


def test_existing_blob_index_without_pdf_metadata_remains_searchable(tmp_path):
    import numpy as np

    with sqlite3.connect(tmp_path / 'metadata.sqlite3') as connection:
        connection.execute('CREATE TABLE chunks (chunk_id TEXT PRIMARY KEY, source_type TEXT, '
                           'title TEXT, text TEXT, source_path TEXT, page_number INTEGER, '
                           'vector_blob BLOB, vector_dim INTEGER)')
        connection.execute('INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?)',
                           ('old', 'paper', 'Old', 'Evidence', 'old.pdf', 1,
                            np.array([1.0, 0.0], dtype='<f4').tobytes(), 2))

    async def embed(texts):
        return [[1.0, 0.0] for _ in texts]

    result = asyncio.run(KnowledgeStore(tmp_path, embed=embed).search('evidence'))[0]
    assert result.chunk_id == 'old'
    assert result.section_title is None
    assert result.parser_version is None
