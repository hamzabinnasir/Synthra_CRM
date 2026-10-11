"""Knowledge base: chunking, change-aware indexing, atomic swap, retrieval.

Guarantees
* Re-indexing the same text is a no-op (document hash compare).
* A changed document re-embeds ONLY chunks whose text is new; chunks seen before (in this or any other
  document) reuse their stored vector, selected by content hash.
* Replacing a document's chunks happens inside ONE transaction, so a concurrent chat always sees the
  complete old set or the complete new set, never an empty window.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Optional

import asyncpg
import numpy as np

from .embedder import Embedder

CHUNK_SIZE = 800
CHUNK_OVERLAP = 120
MAX_CHUNKS_PER_DOCUMENT = 2000


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalise(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text.replace("\r\n", "\n")).strip()


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """Windows of about `size` characters that prefer to end on a paragraph or sentence boundary."""
    text = normalise(text)
    if not text:
        return []
    chunks: List[str] = []
    start, length = 0, len(text)
    while start < length:
        end = min(start + size, length)
        if end < length:
            window = text[start:end]
            floor = int(size * 0.6)
            cut = max(window.rfind("\n\n"), window.rfind(". "), window.rfind("\n"), window.rfind("? "), window.rfind("! "))
            if cut >= floor:
                end = start + cut + 1
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= length:
            break
        start = max(end - overlap, start + 1)
    return chunks


class KnowledgeBase:
    def __init__(self, embedder: Embedder, *, max_documents: int = 200) -> None:
        self.embedder, self.max_documents = embedder, max_documents

    async def upsert(self, pool: asyncpg.Pool, *, source: str, title: str, text: str) -> Dict[str, Any]:
        text = normalise(text)
        content_hash = sha(text)
        async with pool.acquire() as conn:
            existing = await conn.fetchrow("select id, content_hash, chunk_count from crm.kb_documents where source = $1", source)
            if existing and existing["content_hash"] == content_hash:
                return {"status": "unchanged", "id": existing["id"], "chunks": existing["chunk_count"],
                        "embedded": 0, "reused": existing["chunk_count"]}
            if not existing:
                count = await conn.fetchval("select count(*) from crm.kb_documents")
                if count >= self.max_documents:
                    raise ValueError(f"Document limit reached ({self.max_documents}). Delete one first.")
            pieces = chunk_text(text)
            if not pieces:
                raise ValueError("That document has no readable text.")
            if len(pieces) > MAX_CHUNKS_PER_DOCUMENT:
                raise ValueError("That document is too large to index. Split it into smaller parts.")
            hashes = [sha(p) for p in pieces]
            known = {r["content_hash"]: r["embedding"] for r in await conn.fetch(
                "select distinct on (content_hash) content_hash, embedding from crm.kb_chunks "
                "where content_hash = any($1::text[])", sorted(set(hashes)))}

        missing = [(h, p) for h, p in dict.fromkeys(zip(hashes, pieces)) if h not in known]
        if missing:
            for (h, _), vector in zip(missing, await self.embedder.embed([p for _, p in missing])):
                known[h] = vector                      # slow part happens outside any transaction

        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("select pg_advisory_xact_lock(hashtextextended($1, 0))", f"kb:{source}")
            doc_id = await conn.fetchval(
                "insert into crm.kb_documents (source, title, content_hash, size_bytes, chunk_count) "
                "values ($1, $2, $3, $4, $5) on conflict (source) do update set title = excluded.title, "
                "content_hash = excluded.content_hash, size_bytes = excluded.size_bytes, "
                "chunk_count = excluded.chunk_count, updated_at = now() returning id",
                source, title[:200], content_hash, len(text.encode("utf-8")), len(pieces))
            await conn.execute("delete from crm.kb_chunks where document_id = $1", doc_id)
            await conn.executemany(
                "insert into crm.kb_chunks (document_id, position, content, content_hash, embedding) "
                "values ($1, $2, $3, $4, $5)",
                [(doc_id, i, p, h, np.asarray(known[h], dtype=np.float32)) for i, (p, h) in enumerate(zip(pieces, hashes))])
        return {"status": "updated" if existing else "created", "id": doc_id, "chunks": len(pieces),
                "embedded": len(missing), "reused": len(pieces) - len(missing)}

    async def retrieve(self, pool: asyncpg.Pool, query: str, *, k: int = 4, min_similarity: float = 0.30) -> List[str]:
        query = (query or "").strip()
        if not query:
            return []
        (vector,) = await self.embedder.embed([query[:2000]])
        rows = await pool.fetch("select content, similarity from crm.match_chunks($1, $2, $3)",
                                np.asarray(vector, dtype=np.float32), k, min_similarity)
        return [r["content"] for r in rows]

    async def search(self, pool: asyncpg.Pool, query: str, *, k: int = 5) -> List[Dict[str, Any]]:
        (vector,) = await self.embedder.embed([query[:2000]])
        rows = await pool.fetch(
            "select c.content, d.title, d.id as document_id, (1 - (c.embedding <=> $1))::float8 as similarity "
            "from crm.kb_chunks c join crm.kb_documents d on d.id = c.document_id "
            "order by c.embedding <=> $1 limit $2", np.asarray(vector, dtype=np.float32), k)
        return [{"content": r["content"], "title": r["title"], "document_id": r["document_id"],
                 "similarity": round(r["similarity"], 4)} for r in rows]

    @staticmethod
    async def documents(pool: asyncpg.Pool) -> List[Dict[str, Any]]:
        rows = await pool.fetch("select id, source, title, size_bytes, chunk_count, created_at, updated_at "
                                "from crm.kb_documents order by updated_at desc")
        return [{**dict(r), "created_at": r["created_at"].isoformat(), "updated_at": r["updated_at"].isoformat()} for r in rows]

    @staticmethod
    async def delete(pool: asyncpg.Pool, document_id: int) -> bool:
        return (await pool.execute("delete from crm.kb_documents where id = $1", document_id)) == "DELETE 1"

    @staticmethod
    async def stats(pool: asyncpg.Pool) -> Dict[str, int]:
        row = await pool.fetchrow("select (select count(*) from crm.kb_documents) as documents, "
                                  "(select count(*) from crm.kb_chunks) as chunks")
        return {"documents": row["documents"], "chunks": row["chunks"]}
