import asyncio
import json
import sqlite3

import numpy as np
import pytest

from agent_workbench.knowledge.store import KnowledgeStore


async def embed(texts):
    return [[float("old" in text), float("new" in text)] for text in texts]


def chunk(key, text, source="paper.pdf"):
    return dict(chunk_id=key, source_type="paper", title="Paper", text=text,
                source_path=source, page_number=1)


def test_source_replacement_removes_obsolete_chunks_and_survives_restart(tmp_path):
    store = KnowledgeStore(tmp_path, embed=embed)
    asyncio.run(store.upsert(**chunk("old", "old evidence")))
    asyncio.run(store.upsert(**chunk("other", "old unrelated", "other.pdf")))
    asyncio.run(store.replace_source("paper.pdf", [chunk("new", "new evidence")]))
    reopened = KnowledgeStore(tmp_path, embed=embed)
    results = asyncio.run(reopened.search("new"))
    assert [item.chunk_id for item in results] == ["new", "other"]
    assert results[0].text == "new evidence"


def test_embedding_failure_keeps_entire_previous_index(tmp_path):
    store = KnowledgeStore(tmp_path, embed=embed)
    asyncio.run(store.upsert(**chunk("old", "old evidence")))
    async def broken(texts):
        raise RuntimeError("embedding failed")
    store.embed = broken
    with pytest.raises(RuntimeError):
        asyncio.run(store.replace_source("paper.pdf", [chunk("new", "new evidence")]))
    reopened = KnowledgeStore(tmp_path, embed=embed)
    assert asyncio.run(reopened.search("old"))[0].text == "old evidence"


def test_sql_failure_rolls_back_metadata_and_vectors(tmp_path):
    store = KnowledgeStore(tmp_path, embed=embed)
    asyncio.run(store.upsert(**chunk("old", "old evidence")))
    with sqlite3.connect(store.database) as connection:
        connection.execute("""CREATE TRIGGER fail_insert BEFORE INSERT ON chunks
                              WHEN NEW.chunk_id='broken' BEGIN SELECT RAISE(ABORT, 'disk failure'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        asyncio.run(store.replace_source("paper.pdf", [chunk("new", "new evidence"), chunk("broken", "new broken")]))
    reopened = KnowledgeStore(tmp_path, embed=embed)
    results = asyncio.run(reopened.search("old"))
    assert len(results) == 1
    assert results[0].chunk_id == "old"
    assert results[0].score == pytest.approx(1)


def test_upsert_sql_failure_cannot_overwrite_old_vector(tmp_path):
    store = KnowledgeStore(tmp_path, embed=embed)
    asyncio.run(store.upsert(**chunk("same", "old evidence")))
    with sqlite3.connect(store.database) as connection:
        connection.execute("""CREATE TRIGGER fail_update BEFORE UPDATE ON chunks
                              BEGIN SELECT RAISE(ABORT, 'write failure'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        asyncio.run(store.upsert(**chunk("same", "new evidence")))
    results = asyncio.run(KnowledgeStore(tmp_path, embed=embed).search("old"))
    assert results[0].text == "old evidence"
    assert results[0].score == pytest.approx(1)


@pytest.mark.parametrize("legacy", ["npy", "json"])
def test_legacy_index_migrates_without_removing_original_vectors(tmp_path, legacy):
    database = tmp_path / "metadata.sqlite3"
    vector_column = "vector_index INTEGER" if legacy == "npy" else "vector_json TEXT NOT NULL"
    with sqlite3.connect(database) as connection:
        connection.execute(f"CREATE TABLE chunks (chunk_id TEXT PRIMARY KEY, source_type TEXT, title TEXT, text TEXT, source_path TEXT, page_number INTEGER, {vector_column})")
        connection.execute("INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?)",
                           ("old", "paper", "Paper", "old evidence", "paper.pdf", 1,
                            0 if legacy == "npy" else json.dumps([1, 0])))
    if legacy == "npy":
        np.save(tmp_path / "embeddings.npy", np.asarray([[1, 0]], dtype=np.float32))
    store = KnowledgeStore(tmp_path, embed=embed)
    assert asyncio.run(store.search("old"))[0].score == pytest.approx(1)
    assert (tmp_path / "metadata.pre-vector-blob.sqlite3").exists()
    asyncio.run(store.upsert(**chunk("old", "new evidence")))
    assert asyncio.run(KnowledgeStore(tmp_path, embed=embed).search("new"))[0].score == pytest.approx(1)
    if legacy == "npy":
        assert np.load(tmp_path / "embeddings.npy").tolist() == [[1, 0]]


@pytest.mark.parametrize("vector", [[float("nan"), 0], [1, 0, 0]])
def test_invalid_replacement_vectors_preserve_index(tmp_path, vector):
    store = KnowledgeStore(tmp_path, embed=embed)
    asyncio.run(store.upsert(**chunk("old", "old evidence")))
    async def invalid(texts):
        return [vector for _ in texts]
    store.embed = invalid
    with pytest.raises(ValueError):
        asyncio.run(store.replace_source("paper.pdf", [chunk("new", "new evidence")]))
    assert asyncio.run(KnowledgeStore(tmp_path, embed=embed).search("old"))[0].text == "old evidence"


def test_cancelled_replacement_preserves_index(tmp_path):
    async def scenario():
        store = KnowledgeStore(tmp_path, embed=embed)
        await store.upsert(**chunk("old", "old evidence"))
        entered = asyncio.Event()
        async def waiting(texts):
            entered.set()
            await asyncio.Event().wait()
        store.embed = waiting
        task = asyncio.create_task(store.replace_source("paper.pdf", [chunk("new", "new evidence")]))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (await KnowledgeStore(tmp_path, embed=embed).search("old"))[0].text == "old evidence"
    asyncio.run(scenario())


def test_runtime_reindex_failure_keeps_paper_ready_and_old_results(tmp_path):
    import fitz
    from agent_workbench.tests.test_approval_flow import make_runtime
    async def scenario():
        runtime = make_runtime(tmp_path)
        with fitz.open() as pdf:
            pdf.new_page().insert_text((72, 72), "old evidence")
            record = await runtime.import_pdf("paper.pdf", pdf.tobytes())
        async def broken(texts):
            raise RuntimeError("embedding unavailable")
        runtime.knowledge.embed = broken
        with pytest.raises(RuntimeError):
            await runtime.reindex_paper(record["paper_id"])
        runtime.knowledge.embed = embed
        assert (await runtime.knowledge.search("old"))[0].text == "old evidence"
        paper = runtime.list_papers()[0]
        assert paper["status"] == "ready"
        assert "retained" in paper["error"]
    asyncio.run(scenario())


def test_startup_marks_interrupted_import_failed_without_stale_spinner(tmp_path):
    from agent_workbench.tests.test_approval_flow import make_runtime
    from agent_workbench.runtime import ApplicationRuntime
    runtime = make_runtime(tmp_path)
    record, _ = runtime.papers.stage("interrupted.pdf", b"%PDF-placeholder")
    restarted = ApplicationRuntime(data_dir=runtime.data_dir, router=runtime.router,
                                   provider=runtime.provider, embed=embed)
    assert restarted.papers.get(record.paper_id).status == "failed"


def test_runtime_prevents_delete_while_reindexing(tmp_path):
    import fitz
    from agent_workbench.tests.test_approval_flow import make_runtime
    async def scenario():
        runtime = make_runtime(tmp_path)
        with fitz.open() as pdf:
            pdf.new_page().insert_text((72, 72), "old evidence")
            record = await runtime.import_pdf("paper.pdf", pdf.tobytes())
        entered = asyncio.Event()
        async def waiting(texts):
            entered.set()
            await asyncio.Event().wait()
        runtime.knowledge.embed = waiting
        task = asyncio.create_task(runtime.reindex_paper(record["paper_id"]))
        await asyncio.wait_for(entered.wait(), 1)
        try:
            with pytest.raises(ValueError):
                await runtime.delete_paper(record["paper_id"])
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert runtime.papers.get(record["paper_id"]).status == "ready"
    asyncio.run(scenario())
