from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from collections.abc import Awaitable, Callable
from contextlib import closing, contextmanager
from pathlib import Path

import numpy as np

from agent_workbench.knowledge.models import KnowledgeChunk


Embedder = Callable[[list[str]], Awaitable[list[list[float]]]]


def _vector(value) -> np.ndarray:
    vector = np.asarray(value, dtype="<f4")
    if vector.ndim != 1 or vector.size == 0 or not np.all(np.isfinite(vector)):
        raise ValueError("embedder returned an invalid vector")
    return vector


def _cosine(left: np.ndarray, right: np.ndarray) -> float:
    if left.shape != right.shape:
        raise ValueError("embedding dimension does not match the existing index")
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    return float(np.dot(left, right) / denominator) if denominator else 0.0


class KnowledgeStore:
    def __init__(self, root: str | Path, *, embed: Embedder) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "metadata.sqlite3"
        self.embeddings = self.root / "embeddings.npy"
        self.embed = embed
        with self._connect() as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(chunks)")}
            if columns and "vector_blob" not in columns:
                self._backup_legacy(connection)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id TEXT PRIMARY KEY, source_type TEXT NOT NULL,
                    title TEXT NOT NULL, text TEXT NOT NULL, source_path TEXT NOT NULL,
                    page_number INTEGER, vector_blob BLOB NOT NULL, vector_dim INTEGER NOT NULL
                )
            """)
            if columns and "vector_blob" not in columns:
                connection.execute("ALTER TABLE chunks ADD COLUMN vector_blob BLOB")
                connection.execute("ALTER TABLE chunks ADD COLUMN vector_dim INTEGER")
            if "metadata_json" not in columns:
                connection.execute("ALTER TABLE chunks ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'")
            self._legacy_index = "vector_index" in columns
            self._legacy_json = "vector_json" in columns
            self._migrate_vectors(connection)

    @contextmanager
    def _connect(self):
        with closing(sqlite3.connect(self.database, timeout=30)) as connection:
            with connection:
                yield connection

    def _backup_legacy(self, connection) -> None:
        backup = self.root / "metadata.pre-vector-blob.sqlite3"
        if backup.exists():
            return
        fd, temp_name = tempfile.mkstemp(prefix=".metadata-backup-", suffix=".sqlite3", dir=self.root)
        os.close(fd)
        try:
            with closing(sqlite3.connect(temp_name)) as target:
                connection.backup(target)
            os.replace(temp_name, backup)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise

    def _migrate_vectors(self, connection) -> None:
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT * FROM chunks WHERE vector_blob IS NULL").fetchall()
        if not rows:
            return
        matrix = None
        if self.embeddings.is_file():
            matrix = np.load(self.embeddings, allow_pickle=False)
            if matrix.ndim != 2:
                raise ValueError("Legacy matrix is invalid; original index preserved for recovery")
        vectors = []
        for row in rows:
            if self._legacy_index and row["vector_index"] is not None:
                index = int(row["vector_index"])
                if matrix is None or index < 0 or index >= len(matrix):
                    raise ValueError("Legacy vector is missing; restore the original index or reindex")
                vector = _vector(matrix[index])
            elif self._legacy_json:
                vector = _vector(json.loads(row["vector_json"]))
            else:
                raise ValueError("Legacy chunk has no recoverable vector")
            vectors.append(vector)
        if len({vector.size for vector in vectors}) != 1:
            raise ValueError("Legacy vectors have inconsistent dimensions")
        for row, vector in zip(rows, vectors, strict=True):
            connection.execute("UPDATE chunks SET vector_blob=?, vector_dim=? WHERE chunk_id=?",
                               (vector.tobytes(), int(vector.size), row["chunk_id"]))

    async def _embed_chunks(self, chunks: list[dict]) -> list[np.ndarray]:
        vectors = []
        for offset in range(0, len(chunks), 32):
            batch = chunks[offset:offset + 32]
            result = await self.embed([f"passage: {chunk['text']}" for chunk in batch])
            if len(result) != len(batch):
                raise ValueError("embedder returned a different number of vectors")
            vectors.extend(_vector(value) for value in result)
        if vectors and len({vector.size for vector in vectors}) != 1:
            raise ValueError("embedder returned inconsistent vector dimensions")
        return vectors

    def _check_dimensions(self, connection, vectors) -> None:
        row = connection.execute("SELECT vector_dim FROM chunks LIMIT 1").fetchone()
        if row and vectors and row[0] != vectors[0].size:
            raise ValueError("embedding dimension does not match the existing index")

    def _write_chunk(self, connection, chunk, vector) -> None:
        columns = ["chunk_id", "source_type", "title", "text", "source_path", "page_number", "vector_blob", "vector_dim"]
        values = [chunk["chunk_id"], chunk["source_type"], chunk["title"], chunk["text"],
                  chunk["source_path"], chunk.get("page_number"), vector.tobytes(), int(vector.size)]
        columns.append("metadata_json")
        values.append(json.dumps({key: chunk.get(key) for key in
                                  ("section_title", "parser_version", "chunker_version")}))
        # Keep legacy NOT NULL columns satisfiable; new reads use only the blob.
        if self._legacy_index:
            columns.append("vector_index")
            values.append(-1)
        if self._legacy_json:
            columns.append("vector_json")
            values.append(json.dumps(vector.tolist()))
        placeholders = ", ".join("?" for _ in values)
        updates = ", ".join(f"{name}=excluded.{name}" for name in columns if name != "chunk_id")
        connection.execute(f"INSERT INTO chunks ({', '.join(columns)}) VALUES ({placeholders}) "
                           f"ON CONFLICT(chunk_id) DO UPDATE SET {updates}", values)

    async def upsert(self, *, chunk_id: str, source_type: str, title: str, text: str,
                     source_path: str, page_number: int | None = None) -> None:
        chunk = dict(chunk_id=chunk_id, source_type=source_type, title=title, text=text,
                     source_path=source_path, page_number=page_number)
        vectors = await self._embed_chunks([chunk])
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._check_dimensions(connection, vectors)
            self._write_chunk(connection, chunk, vectors[0])

    async def replace_source(self, source_path: str, chunks: list[dict]) -> None:
        if any(chunk["source_path"] != source_path for chunk in chunks):
            raise ValueError("replacement chunks must belong to the same source")
        ids = [chunk["chunk_id"] for chunk in chunks]
        if len(ids) != len(set(ids)):
            raise ValueError("replacement chunk ids must be unique")
        vectors = await self._embed_chunks(chunks)
        # No await inside this transaction: readers see the old or the complete new source.
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._check_dimensions(connection, vectors)
            for key in ids:
                owner = connection.execute("SELECT source_path FROM chunks WHERE chunk_id=?", (key,)).fetchone()
                if owner and owner[0] != source_path:
                    raise ValueError("replacement chunk id belongs to another source")
            connection.execute("DELETE FROM chunks WHERE source_path=?", (source_path,))
            for chunk, vector in zip(chunks, vectors, strict=True):
                self._write_chunk(connection, chunk, vector)

    async def search(self, query: str, *, top_k: int = 5,
                     source_types: list[str] | None = None, source_paths: list[str] | None = None) -> list[KnowledgeChunk]:
        sql = "SELECT chunk_id, source_type, title, text, source_path, page_number, vector_blob, vector_dim, metadata_json FROM chunks"
        params = []
        conditions = []
        if source_types:
            conditions.append(f"source_type IN ({', '.join('?' for _ in source_types)})")
            params.extend(source_types)
        if source_paths is not None:
            if not source_paths:
                return []
            conditions.append(f"source_path IN ({', '.join('?' for _ in source_paths)})")
            params.extend(source_paths)
        if conditions:
            sql += ' WHERE ' + ' AND '.join(conditions)
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        if not rows:
            return []
        result = await self.embed([f"query: {query}"])
        if len(result) != 1:
            raise ValueError("embedder returned an invalid query vector count")
        query_vector = _vector(result[0])
        results = []
        for row in rows:
            vector = _vector(np.frombuffer(row[6], dtype="<f4"))
            if vector.size != row[7]:
                raise ValueError("Stored vector dimension is invalid; reindex required")
            results.append(KnowledgeChunk(chunk_id=row[0], source_type=row[1], title=row[2], text=row[3],
                                         source_path=row[4], page_number=row[5], score=_cosine(query_vector, vector),
                                         **json.loads(row[8])))
        return sorted(results, key=lambda item: item.score or 0.0, reverse=True)[:max(0, top_k)]

    async def delete_source(self, source_path: str) -> int:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM chunks WHERE source_path=?", (source_path,))
            return cursor.rowcount

    def source_summary(self, source_path: str) -> dict[str, int]:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*), COALESCE(MAX(page_number), 0) FROM chunks WHERE source_path=?",
                                     (source_path,)).fetchone()
        return {"chunk_count": row[0], "page_count": row[1]}

    def source_inventory(self) -> dict[str, list[dict]]:
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute('SELECT chunk_id, source_type, title, text, source_path, page_number FROM chunks').fetchall()
        result = {}
        for row in rows:
            result.setdefault(row['source_path'], []).append(dict(row))
        return result
