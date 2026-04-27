"""
快速参考视频生成 API 路由
"""

import json
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, Query, UploadFile, File, Form, HTTPException
from sqlalchemy import select, func, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import get_db
from app.models import ContentGenerationJob, Account
from app.models.reference_asset import ReferenceAsset, ContentJobReference
from app.schemas import (
    FastReferenceJobRequest,
    ReferenceAssetResponse,
    ReferenceAssetCreate,
    MentionResolveRequest,
    MentionResolveResponse,
    ContentGenerationJobResponse,
)
from app.services.content_generation import content_generation_service
from app.services.reference_asset_service import ReferenceAssetService
from app.api.routers.content_generation import _to_job_response

router = APIRouter()


# ==================== Job Endpoints ====================


@router.post("/jobs", response_model=ContentGenerationJobResponse)
async def create_fast_reference_job(
    req: FastReferenceJobRequest,
    db: AsyncSession = Depends(get_db),
):
    resolved, missing = await ReferenceAssetService.resolve_mentions(db, req.prompt)
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"未找到素材: {', '.join(missing)}",
        )

    job = ContentGenerationJob(
        job_type="video",
        function_mode="fast_reference",
        prompt=req.prompt,
        model=req.model or "Dreamina Seedance 2.0 Fast",
        duration=req.duration or 5,
        resolution=req.resolution or "720p",
        ratio=req.ratio or "16:9",
        status="queued",
        retry_count=0,
        max_retry=10,
    )
    db.add(job)
    await db.flush()
    await db.refresh(job)

    if resolved:
        await ReferenceAssetService.create_job_references(db, job.id, resolved)

    await db.commit()
    await db.refresh(job)

    await content_generation_service.enqueue(job.id)

    return _to_job_response(job)


@router.get("/jobs", response_model=List[ContentGenerationJobResponse])
async def list_fast_reference_jobs(
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(ContentGenerationJob)
        .where(ContentGenerationJob.function_mode == "fast_reference")
        .order_by(ContentGenerationJob.created_at.desc())
    )
    if status:
        stmt = stmt.where(ContentGenerationJob.status == status)
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(stmt)
    return [_to_job_response(j) for j in result.scalars().all()]


@router.get("/jobs/{job_id}", response_model=ContentGenerationJobResponse)
async def get_fast_reference_job(
    job_id: int,
    db: AsyncSession = Depends(get_db),
):
    job = await db.get(ContentGenerationJob, job_id)
    if not job or job.function_mode != "fast_reference":
        raise HTTPException(status_code=404, detail="任务不存在")
    return _to_job_response(job)


@router.post("/jobs/{job_id}/retry", response_model=ContentGenerationJobResponse)
async def retry_fast_reference_job(
    job_id: int,
    db: AsyncSession = Depends(get_db),
):
    job = await db.get(ContentGenerationJob, job_id)
    if not job or job.function_mode != "fast_reference":
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.status not in ("failed",):
        raise HTTPException(status_code=400, detail="只能重试失败的任务")

    current_retry = getattr(job, "retry_count", 0) or 0
    max_retry = getattr(job, "max_retry", 10) or 10
    if current_retry >= max_retry:
        raise HTTPException(status_code=400, detail=f"已达最大重试次数 ({max_retry})")

    await db.execute(
        update(ContentGenerationJob)
        .where(ContentGenerationJob.id == job_id)
        .values(
            status="queued",
            error_message=None,
            remote_task_id=None,
            remote_history_id=None,
            retry_count=current_retry + 1,
            updated_at=datetime.now(),
        )
    )
    await db.commit()
    await db.refresh(job)

    await content_generation_service.enqueue(job_id)
    return _to_job_response(job)


@router.delete("/jobs/{job_id}")
async def delete_fast_reference_job(
    job_id: int,
    db: AsyncSession = Depends(get_db),
):
    job = await db.get(ContentGenerationJob, job_id)
    if not job or job.function_mode != "fast_reference":
        raise HTTPException(status_code=404, detail="任务不存在")

    if job.status in ("queued", "submitting"):
        await db.execute(
            update(ContentGenerationJob)
            .where(ContentGenerationJob.id == job_id)
            .values(status="cancelled", updated_at=datetime.now())
        )
        await db.commit()
        return {"message": "任务已取消"}

    await db.execute(
        delete(ContentJobReference).where(ContentJobReference.job_id == job_id)
    )
    await db.delete(job)
    await db.commit()
    return {"message": "任务已删除"}


# ==================== Asset Endpoints ====================


@router.get("/assets", response_model=List[ReferenceAssetResponse])
async def list_assets(
    search: Optional[str] = Query(None),
    asset_type: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    return await ReferenceAssetService.list_assets(db, search=search, asset_type=asset_type)


MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50MB
ALLOWED_MIME_PREFIXES = ("image/", "video/")


@router.post("/assets", response_model=ReferenceAssetResponse)
async def upload_asset(
    file: UploadFile = File(...),
    name: str = Form(...),
    alias: Optional[str] = Form(None),
    asset_type: str = Form("image"),
    description: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    if asset_type not in ("image", "video"):
        raise HTTPException(status_code=400, detail="asset_type must be image or video")
    content_type = file.content_type or ""
    if not any(content_type.startswith(p) for p in ALLOWED_MIME_PREFIXES):
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {content_type}")
    file_data = await file.read()
    if len(file_data) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail=f"File too large (max {MAX_UPLOAD_SIZE // 1024 // 1024}MB)")
    asset = await ReferenceAssetService.create_asset(
        db,
        name=name,
        file_data=file_data,
        filename=file.filename or "upload.bin",
        mime_type=file.content_type or "application/octet-stream",
        alias=alias,
        asset_type=asset_type,
        description=description,
        tags=tags,
    )
    await db.commit()
    return asset


@router.put("/assets/{asset_id}", response_model=ReferenceAssetResponse)
async def update_asset(
    asset_id: int,
    name: Optional[str] = Form(None),
    alias: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    tags: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    db: AsyncSession = Depends(get_db),
):
    file_data = None
    filename = None
    mime_type = None
    if file:
        content_type = file.content_type or ""
        if not any(content_type.startswith(p) for p in ALLOWED_MIME_PREFIXES):
            raise HTTPException(status_code=400, detail=f"Unsupported file type: {content_type}")
        file_data = await file.read()
        if len(file_data) > MAX_UPLOAD_SIZE:
            raise HTTPException(status_code=413, detail=f"File too large (max {MAX_UPLOAD_SIZE // 1024 // 1024}MB)")
        filename = file.filename
        mime_type = file.content_type

    asset = await ReferenceAssetService.update_asset(
        db,
        asset_id,
        name=name,
        alias=alias,
        description=description,
        tags=tags,
        file_data=file_data,
        filename=filename,
        mime_type=mime_type,
    )
    if not asset:
        raise HTTPException(status_code=404, detail="素材不存在")
    await db.commit()
    return asset


@router.delete("/assets/{asset_id}")
async def delete_asset(
    asset_id: int,
    db: AsyncSession = Depends(get_db),
):
    ok = await ReferenceAssetService.delete_asset(db, asset_id)
    if not ok:
        raise HTTPException(status_code=404, detail="素材不存在")
    await db.commit()
    return {"message": "素材已删除"}


@router.post("/assets/resolve", response_model=MentionResolveResponse)
async def resolve_mentions(
    req: MentionResolveRequest,
    db: AsyncSession = Depends(get_db),
):
    resolved, missing = await ReferenceAssetService.resolve_mentions(db, req.prompt)
    return MentionResolveResponse(mentions=resolved, missing=missing)


# ==================== Account Pool Endpoints ====================


@router.post("/accounts/{account_id}/toggle")
async def toggle_fast_account(
    account_id: int,
    is_enabled: bool = Query(...),
    db: AsyncSession = Depends(get_db),
):
    account = await db.get(Account, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="账号不存在")
    if is_enabled and (
        not account.session_id or account.health_status != "healthy"
    ):
        raise HTTPException(status_code=400, detail="账号不符合条件")
    await db.execute(
        update(Account)
        .where(Account.id == account_id)
        .values(fast_enabled=is_enabled)
    )
    await db.commit()
    return {"message": "ok"}


@router.post("/accounts/batch-toggle")
async def batch_toggle_fast_accounts(
    is_enabled: bool = Query(...),
    status: Optional[str] = Query(None),
    health_status: Optional[str] = Query(None),
    region: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Account)
    if status:
        if status == "success":
            stmt = stmt.where(Account.status.in_(["success", "active"]))
        else:
            stmt = stmt.where(Account.status == status)
    if health_status:
        stmt = stmt.where(Account.health_status == health_status)
    if region and region != "all":
        stmt = stmt.where(Account.region.ilike(f"%{region}%"))
    if search:
        stmt = stmt.where(Account.email.ilike(f"%{search}%"))

    accounts = (await db.execute(stmt)).scalars().all()
    updated = 0
    for acct in accounts:
        if is_enabled and (not acct.session_id or acct.health_status != "healthy"):
            continue
        await db.execute(
            update(Account).where(Account.id == acct.id).values(fast_enabled=is_enabled)
        )
        updated += 1
    await db.commit()
    return {"message": f"已更新 {updated} 个账号"}
