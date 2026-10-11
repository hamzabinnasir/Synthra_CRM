"""/api/kb: the workspace's knowledge base (upload, paste, list, search, delete)."""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import PurePath

import asyncpg
from fastapi import APIRouter, Depends, File, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from ..errors import AppError, BadRequest, NotFound
from ..kb.extract import UnsupportedDocument, extract_text
from ..kb.service import KnowledgeBase
from ..security.tokens import AccessClaims
from .common import audit, throttle, workspace_pool
from .deps import current_claims

log = logging.getLogger("app.kb")
router = APIRouter(prefix="/api/kb", tags=["knowledge-base"])


class EmbedderUnavailable(AppError):
    status, code = 503, "embedder_unavailable"


class TextIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=150)
    text: str = Field(min_length=1, max_length=500_000)


class SearchIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=500)


def _kb(request: Request) -> KnowledgeBase:
    return request.app.state.kb


async def _index(request: Request, pool: asyncpg.Pool, source: str, title: str, text: str) -> dict:
    try:
        return await _kb(request).upsert(pool, source=source, title=title, text=text)
    except ValueError as exc:
        raise BadRequest(str(exc), code="invalid_document")
    except Exception:
        log.exception("kb_index_failed")
        raise EmbedderUnavailable(
            "The embedding model is not ready. The first upload downloads it (about 90 MB), so try again in a minute. "
            "Offline? Set CRM_EMBEDDER=hash.")


@router.get("")
async def overview(claims: AccessClaims = Depends(current_claims), pool: asyncpg.Pool = Depends(workspace_pool)):
    return {"documents": await KnowledgeBase.documents(pool), "stats": await KnowledgeBase.stats(pool)}


@router.post("/upload")
async def upload(request: Request, file: UploadFile = File(...), claims: AccessClaims = Depends(current_claims),
                 pool: asyncpg.Pool = Depends(workspace_pool)):
    await throttle(request, claims, "kb-write", 120)
    limit = request.app.state.settings.max_upload_bytes
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise BadRequest(f"That file is larger than {limit // 1_000_000} MB.", code="file_too_large")
    name = PurePath(file.filename or "document").name[:150] or "document"
    try:
        text = await asyncio.to_thread(extract_text, name, data)
    except UnsupportedDocument as exc:
        raise BadRequest(str(exc), code="unsupported_document")
    result = await _index(request, pool, f"upload:{name.lower()}", name, text)
    await audit(request, claims, "kb_document_saved", status=result["status"])
    return result


@router.post("/text")
async def add_text(body: TextIn, request: Request, claims: AccessClaims = Depends(current_claims),
                   pool: asyncpg.Pool = Depends(workspace_pool)):
    await throttle(request, claims, "kb-write", 120)
    slug = re.sub(r"[^a-z0-9]+", "-", body.title.lower()).strip("-")[:80] or "note"
    result = await _index(request, pool, f"text:{slug}", body.title, body.text)
    await audit(request, claims, "kb_document_saved", status=result["status"])
    return result


@router.post("/search")
async def search(body: SearchIn, request: Request, claims: AccessClaims = Depends(current_claims),
                 pool: asyncpg.Pool = Depends(workspace_pool)):
    await throttle(request, claims, "kb-search", 600)
    try:
        return {"results": await _kb(request).search(pool, body.query)}
    except Exception:
        log.exception("kb_search_failed")
        raise EmbedderUnavailable("The embedding model is not ready yet. Try again in a minute.")


@router.delete("/{document_id}", status_code=204)
async def delete(document_id: int, request: Request, claims: AccessClaims = Depends(current_claims),
                 pool: asyncpg.Pool = Depends(workspace_pool)):
    if not await KnowledgeBase.delete(pool, document_id):
        raise NotFound("Document not found.")
    await audit(request, claims, "kb_document_deleted")
