"""
Fast Reference API Router — 快速参考视频生成 API
"""

import json
import os
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models import ContentGenerationJob
from app.models.reference_asset import ReferenceAsset, ContentJobReference
from app.schemas import FastReferenceJobRequest, ReferenceAssetResponse
from app.services.reference_asset_service import ReferenceAssetService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/fast-reference", tags=["fast-reference"])


@router.post("/jobs")
async def create_job(req: FastReferenceJobRequest, db: AsyncSession = Depends(get_db)):
    mention_names = ReferenceAssetService.extract_mentions(req.prompt)
    resolved, missing = await ReferenceAssetService.resolve_mentions(db, mention_names)
    if missing:
        raise HTTPException(400, f"Unresolved references: {', '.join(missing)}")

    job = ContentGenerationJob(
        job_type="video",
        prompt=req.prompt,
        model=req.model or "Seedance 2.0 Fast",
        function_mode="fast_reference",
        status="queued",
        retry_count=0,
        max_retry=settings.fast_max_retry,
        duration=req.duration,
        resolution=req.resolution,
        ratio=req.ratio,
    )
    db.add(job)
    await db.flush()

    for i, (name, asset_id, file_path) in enumerate(resolved):
        ref = ContentJobReference(job_id=job.id, asset_id=asset_id, position=i)
        db.add(ref)

    await db.commit()
    await db.refresh(job)

    from app.main import fast_reference_service
    if fast_reference_service:
        await fast_reference_service.enqueue(job.id)

    return {"id": job.id, "status": job.status, "prompt": job.prompt}


@router.get("/jobs")
async def list_jobs(
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(ContentGenerationJob).where(
        ContentGenerationJob.function_mode == "fast_reference"
    )
    if status:
        stmt = stmt.where(ContentGenerationJob.status == status)
    stmt = stmt.order_by(ContentGenerationJob.id.desc())

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    jobs = (await db.execute(stmt)).scalars().all()

    return {
        "items": [_job_to_dict(j) for j in jobs],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/jobs/{job_id}")
async def get_job(job_id: int, db: AsyncSession = Depends(get_db)):
    job = (
        await db.execute(
            select(ContentGenerationJob).where(ContentGenerationJob.id == job_id)
        )
    ).scalar_one_or_none()
    if not job or job.function_mode != "fast_reference":
        raise HTTPException(404, "Job not found")
    return _job_to_dict(job)


@router.post("/jobs/{job_id}/retry")
async def retry_job(job_id: int, db: AsyncSession = Depends(get_db)):
    job = (
        await db.execute(
            select(ContentGenerationJob).where(ContentGenerationJob.id == job_id)
        )
    ).scalar_one_or_none()
    if not job or job.function_mode != "fast_reference":
        raise HTTPException(404, "Job not found")
    if job.status not in ("failed",):
        raise HTTPException(400, "Only failed jobs can be retried")

    job.status = "queued"
    job.retry_count = (job.retry_count or 0) + 1
    job.error_message = None
    await db.commit()

    from app.main import fast_reference_service
    if fast_reference_service:
        await fast_reference_service.enqueue(job.id)

    return {"id": job.id, "status": "queued"}


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: int, db: AsyncSession = Depends(get_db)):
    job = (
        await db.execute(
            select(ContentGenerationJob).where(ContentGenerationJob.id == job_id)
        )
    ).scalar_one_or_none()
    if not job or job.function_mode != "fast_reference":
        raise HTTPException(404, "Job not found")
    if job.status in ("submitting", "submitted", "processing"):
        raise HTTPException(400, "Cannot delete active job")
    await db.delete(job)
    await db.commit()
    return {"message": "deleted"}


# ==================== Asset CRUD ====================


@router.get("/assets")
async def list_assets(
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    assets = await ReferenceAssetService.list_assets(
        db, search=search, offset=(page - 1) * page_size, limit=page_size
    )
    total_stmt = select(func.count()).select_from(ReferenceAsset)
    if search:
        total_stmt = total_stmt.where(
            ReferenceAsset.name.contains(search)
            | ReferenceAsset.alias.contains(search)
        )
    total = (await db.execute(total_stmt)).scalar() or 0
    return {
        "items": [ReferenceAssetResponse.model_validate(a).model_dump() for a in assets],
        "total": total,
    }


ALLOWED_MIME_PREFIXES = ("image/",)
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"}
MAX_ASSET_SIZE = 20 * 1024 * 1024  # 20 MB


@router.post("/assets")
async def upload_asset(
    file: UploadFile = File(...),
    name: Optional[str] = Form(None),
    alias: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported file extension: {ext}")
    if file.content_type and not file.content_type.startswith(ALLOWED_MIME_PREFIXES[0]):
        raise HTTPException(400, f"Unsupported MIME type: {file.content_type}")

    assets_dir = ReferenceAssetService.get_assets_dir()
    file_bytes = await file.read()

    if len(file_bytes) > MAX_ASSET_SIZE:
        raise HTTPException(400, f"File too large (max {MAX_ASSET_SIZE // 1024 // 1024}MB)")

    asset_name = name or os.path.splitext(file.filename or "unnamed")[0]
    existing = await ReferenceAssetService.get_asset_by_name(db, asset_name)
    if existing:
        raise HTTPException(400, f"Asset name '{asset_name}' already exists")

    sha256 = ReferenceAssetService.compute_sha256(file_bytes)
    ext = os.path.splitext(file.filename or "")[1] or ".png"
    file_path = assets_dir / f"{sha256}{ext}"
    file_path.write_bytes(file_bytes)

    asset = await ReferenceAssetService.create_asset(
        db,
        name=asset_name,
        file_path=str(file_path),
        alias=alias,
        file_size=len(file_bytes),
        sha256=sha256,
        mime_type=file.content_type,
        description=description,
        tags=tags,
    )
    return ReferenceAssetResponse.model_validate(asset).model_dump()


@router.put("/assets/{asset_id}")
async def update_asset(
    asset_id: int,
    name: Optional[str] = Form(None),
    alias: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    kwargs = {}
    if name is not None:
        kwargs["name"] = name
    if alias is not None:
        kwargs["alias"] = alias
    if description is not None:
        kwargs["description"] = description
    if tags is not None:
        kwargs["tags"] = tags

    asset = await ReferenceAssetService.update_asset(db, asset_id, **kwargs)
    if not asset:
        raise HTTPException(404, "Asset not found")
    return ReferenceAssetResponse.model_validate(asset).model_dump()


@router.delete("/assets/{asset_id}")
async def delete_asset(asset_id: int, db: AsyncSession = Depends(get_db)):
    active_refs = (
        await db.execute(
            select(func.count())
            .select_from(ContentJobReference)
            .join(ContentGenerationJob)
            .where(
                ContentJobReference.asset_id == asset_id,
                ContentGenerationJob.status.in_(
                    ["queued", "submitting", "submitted", "processing"]
                ),
            )
        )
    ).scalar() or 0
    if active_refs > 0:
        raise HTTPException(400, "Asset is referenced by active jobs")

    deleted = await ReferenceAssetService.delete_asset(db, asset_id)
    if not deleted:
        raise HTTPException(404, "Asset not found")
    return {"message": "deleted"}


@router.post("/assets/resolve")
async def resolve_mentions(
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    prompt = body.get("prompt", "")
    mention_names = ReferenceAssetService.extract_mentions(prompt)
    resolved, missing = await ReferenceAssetService.resolve_mentions(db, mention_names)
    return {
        "matches": [
            {"name": name, "asset_id": aid, "file_path": fp}
            for name, aid, fp in resolved
        ],
        "missing": missing,
    }


@router.post("/accounts/{account_id}/toggle")
async def toggle_fast_account(
    account_id: int,
    is_enabled: bool = Query(...),
    db: AsyncSession = Depends(get_db),
):
    from app.models import Account

    account = (
        await db.execute(select(Account).where(Account.id == account_id))
    ).scalar_one_or_none()
    if not account:
        raise HTTPException(404, "Account not found")
    account.fast_enabled = is_enabled
    await db.commit()
    return {"message": "ok"}


def _job_to_dict(job) -> dict:
    local_urls = []
    if job.local_urls:
        try:
            local_urls = json.loads(job.local_urls)
        except (json.JSONDecodeError, TypeError):
            pass
    return {
        "id": job.id,
        "prompt": job.prompt,
        "model": job.model,
        "status": job.status,
        "function_mode": job.function_mode,
        "account_id": job.account_id,
        "remote_task_id": job.remote_task_id,
        "error_message": job.error_message,
        "local_urls": local_urls,
        "video_url": getattr(job, "video_url", None),
        "retry_count": getattr(job, "retry_count", 0),
        "created_at": str(job.created_at) if job.created_at else None,
        "submitted_at": str(job.submitted_at) if job.submitted_at else None,
        "finished_at": str(job.finished_at) if job.finished_at else None,
    }
