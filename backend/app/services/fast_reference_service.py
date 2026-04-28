"""
FastReferenceService — 快速参考视频生成编排服务
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import async_session_factory
from app.models import Account, ContentGenerationJob
from app.models.reference_asset import ContentJobReference
from app.services.account_lease_service import AccountLeaseService
from app.services.fast_reference_executor import (
    FastReferenceBrowserExecutor,
    FastReferenceResult,
)
from app.services.fast_reference_poller import FastReferencePoller
from app.services.reference_asset_service import ReferenceAssetService
from app.api.routers.accounts import resolve_account_proxy

logger = logging.getLogger(__name__)


class FastReferenceService:

    def __init__(self):
        self._queue: asyncio.Queue = asyncio.Queue()
        self._workers: List[asyncio.Task] = []
        self._polling_task: Optional[asyncio.Task] = None
        self._semaphore = asyncio.Semaphore(settings.fast_max_browsers)
        self._running = False

    async def start(self):
        self._running = True
        await self._recover_stale_jobs()
        for i in range(settings.fast_max_browsers):
            task = asyncio.create_task(self._worker_loop(i))
            self._workers.append(task)
        self._polling_task = asyncio.create_task(self._polling_loop())
        logger.info(
            "FastReferenceService started with %d workers", settings.fast_max_browsers
        )

    async def stop(self):
        self._running = False
        for _ in self._workers:
            await self._queue.put(None)
        if self._polling_task:
            self._polling_task.cancel()
        for w in self._workers:
            try:
                await asyncio.wait_for(w, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                w.cancel()
        self._workers.clear()
        logger.info("FastReferenceService stopped")

    async def enqueue(self, job_id: int):
        await self._queue.put(job_id)
        logger.info("Job #%d enqueued for fast reference", job_id)

    async def _worker_loop(self, worker_id: int):
        logger.info("Fast worker #%d started", worker_id)
        while self._running:
            try:
                job_id = await asyncio.wait_for(self._queue.get(), timeout=5)
            except asyncio.TimeoutError:
                continue
            if job_id is None:
                break
            try:
                await self._run_job(job_id, worker_id)
            except Exception as exc:
                logger.error("Worker #%d job #%d error: %s", worker_id, job_id, exc)

    async def _run_job(self, job_id: int, worker_id: int):
        async with self._semaphore:
            async with async_session_factory() as db:
                job = (
                    await db.execute(
                        select(ContentGenerationJob).where(
                            ContentGenerationJob.id == job_id
                        )
                    )
                ).scalar_one_or_none()
                if not job:
                    logger.warning("Job #%d not found", job_id)
                    return
                if job.status != "queued":
                    logger.info("Job #%d skipped, status=%s", job_id, job.status)
                    return

                account = await AccountLeaseService.acquire(
                    db, purpose="fast_reference", job_id=job_id
                )
                if not account:
                    logger.warning("No available account for job #%d, re-queuing", job_id)
                    await asyncio.sleep(10)
                    await self._queue.put(job_id)
                    return

                await db.execute(
                    update(ContentGenerationJob)
                    .where(ContentGenerationJob.id == job_id)
                    .values(
                        status="submitting",
                        account_id=account.id,
                        browser_started_at=datetime.now(),
                    )
                )
                await db.commit()

                try:
                    result = await self._execute_browser(db, job, account)
                    await self._handle_result(db, job_id, account, result)
                except Exception as exc:
                    logger.error("Job #%d execution failed: %s", job_id, exc)
                    await db.execute(
                        update(ContentGenerationJob)
                        .where(ContentGenerationJob.id == job_id)
                        .values(
                            status="failed",
                            error_message=str(exc),
                            browser_finished_at=datetime.now(),
                        )
                    )
                    await db.commit()
                    await AccountLeaseService.release(db, account.id, job_id)
                    await self._handle_account_after_job(db, account, success=False)

    async def _execute_browser(
        self, db: AsyncSession, job, account
    ) -> FastReferenceResult:
        refs = (
            await db.execute(
                select(ContentJobReference)
                .where(ContentJobReference.job_id == job.id)
                .order_by(ContentJobReference.position)
            )
        ).scalars().all()

        ref_files = []
        for ref in refs:
            asset = await ReferenceAssetService.get_asset(db, ref.asset_id)
            if asset:
                ref_files.append(asset.file_path)
                await ReferenceAssetService.increment_usage(db, asset.id)

        proxy_url, _ = await resolve_account_proxy(account, db)

        executor = FastReferenceBrowserExecutor(
            session_id=account.session_id,
            prompt=job.prompt or "",
            region=getattr(account, "region", None),
            reference_files=ref_files if ref_files else None,
            proxy_url=proxy_url,
        )
        return await asyncio.wait_for(
            executor.execute(), timeout=settings.fast_task_timeout
        )

    async def _handle_result(
        self,
        db: AsyncSession,
        job_id: int,
        account,
        result: FastReferenceResult,
    ):
        now = datetime.now()
        if result.success and result.history_id:
            await db.execute(
                update(ContentGenerationJob)
                .where(ContentGenerationJob.id == job_id)
                .values(
                    status="submitted",
                    remote_task_id=result.task_id,
                    remote_history_id=result.history_id,
                    submitted_at=now,
                    browser_finished_at=now,
                    browser_session_log=result.browser_session_log,
                )
            )
            await db.commit()
            await AccountLeaseService.extend(db, account.id, job_id, 600)
        elif result.submitted_evidence:
            await db.execute(
                update(ContentGenerationJob)
                .where(ContentGenerationJob.id == job_id)
                .values(
                    status="failed",
                    error_message="ambiguous_submission",
                    browser_finished_at=now,
                    browser_session_log=result.browser_session_log,
                )
            )
            await db.commit()
            await AccountLeaseService.release(db, account.id, job_id)
            await self._handle_account_after_job(db, account, success=False)
        else:
            retry_count = (
                await db.execute(
                    select(ContentGenerationJob.retry_count).where(
                        ContentGenerationJob.id == job_id
                    )
                )
            ).scalar_one_or_none() or 0

            if retry_count < settings.fast_max_retry:
                await db.execute(
                    update(ContentGenerationJob)
                    .where(ContentGenerationJob.id == job_id)
                    .values(
                        status="queued",
                        retry_count=retry_count + 1,
                        browser_finished_at=now,
                        browser_session_log=result.browser_session_log,
                        error_message=result.error,
                    )
                )
                await db.commit()
                await AccountLeaseService.release(db, account.id, job_id)
                await self._queue.put(job_id)
            else:
                await db.execute(
                    update(ContentGenerationJob)
                    .where(ContentGenerationJob.id == job_id)
                    .values(
                        status="failed",
                        error_message=f"max_retry_exceeded: {result.error}",
                        browser_finished_at=now,
                        browser_session_log=result.browser_session_log,
                    )
                )
                await db.commit()
                await AccountLeaseService.release(db, account.id, job_id)
                await self._handle_account_after_job(db, account, success=False)

    async def _polling_loop(self):
        while self._running:
            try:
                await asyncio.sleep(settings.fast_poll_interval)
                async with async_session_factory() as db:
                    jobs = (
                        await db.execute(
                            select(ContentGenerationJob).where(
                                ContentGenerationJob.function_mode == "fast_reference",
                                ContentGenerationJob.status.in_(
                                    ["submitted", "processing"]
                                ),
                            )
                        )
                    ).scalars().all()

                    for job in jobs:
                        try:
                            await self._poll_single_job(db, job)
                        except Exception as exc:
                            logger.error("Poll job #%d error: %s", job.id, exc)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Polling loop error: %s", exc)
                await asyncio.sleep(10)

    async def _poll_single_job(self, db: AsyncSession, job):
        history_id = job.remote_history_id or job.remote_task_id
        if not history_id or not job.account_id:
            return

        account = (
            await db.execute(select(Account).where(Account.id == job.account_id))
        ).scalar_one_or_none()
        if not account or not account.session_id:
            return

        submitted_at = job.submitted_at
        if submitted_at and (datetime.now() - submitted_at).total_seconds() > settings.fast_task_timeout:
            await db.execute(
                update(ContentGenerationJob)
                .where(ContentGenerationJob.id == job.id)
                .values(status="failed", error_message="polling_timeout")
            )
            await db.commit()
            await AccountLeaseService.release(db, account.id, job.id)
            await self._handle_account_after_job(db, account, success=False)
            return

        primary_region = getattr(job, "polling_region", None) or getattr(
            account, "region", None
        )
        result, effective_region = await FastReferencePoller.poll_with_region_degradation(
            account.session_id, history_id, primary_region
        )

        if not result:
            return

        if effective_region and effective_region != primary_region:
            await db.execute(
                update(ContentGenerationJob)
                .where(ContentGenerationJob.id == job.id)
                .values(polling_region=effective_region)
            )
            await db.commit()

        status = result.get("status")
        if status == "success":
            video_url = result.get("video_url")
            local_path = None
            if video_url:
                local_path = await FastReferencePoller.download_video(
                    video_url, job.id
                )
            await db.execute(
                update(ContentGenerationJob)
                .where(ContentGenerationJob.id == job.id)
                .values(
                    status="success",
                    video_url=video_url,
                    local_urls=json.dumps([local_path]) if local_path else None,
                    finished_at=datetime.now(),
                )
            )
            await db.commit()
            await AccountLeaseService.release(db, account.id, job.id)
            await self._handle_account_after_job(db, account, success=True)
        elif status == "failed":
            await db.execute(
                update(ContentGenerationJob)
                .where(ContentGenerationJob.id == job.id)
                .values(
                    status="failed",
                    error_message=result.get("error", "remote_failed"),
                    finished_at=datetime.now(),
                )
            )
            await db.commit()
            await AccountLeaseService.release(db, account.id, job.id)
            await self._handle_account_after_job(db, account, success=False)
        else:
            await AccountLeaseService.extend(db, account.id, job.id, 120)

    async def _handle_account_after_job(
        self, db: AsyncSession, account, success: bool
    ):
        strategy = settings.fast_account_strategy
        if strategy == "one_time":
            await db.execute(
                update(Account)
                .where(Account.id == account.id)
                .values(fast_enabled=False)
            )
            await db.commit()
        elif strategy == "disable_on_low_credit":
            await db.refresh(account)
            total_credits = (account.credits_total or 0)
            if total_credits < settings.fast_credit_threshold:
                await db.execute(
                    update(Account)
                    .where(Account.id == account.id)
                    .values(fast_enabled=False)
                )
                await db.commit()
                logger.info(
                    "Account #%d disabled: credits %d < threshold %d",
                    account.id, total_credits, settings.fast_credit_threshold,
                )

    async def _recover_stale_jobs(self):
        async with async_session_factory() as db:
            queued = (
                await db.execute(
                    select(ContentGenerationJob).where(
                        ContentGenerationJob.function_mode == "fast_reference",
                        ContentGenerationJob.status == "queued",
                    )
                )
            ).scalars().all()
            for job in queued:
                await self._queue.put(job.id)
                logger.info("Recovered queued job #%d", job.id)

            stale_submitting = (
                await db.execute(
                    select(ContentGenerationJob).where(
                        ContentGenerationJob.function_mode == "fast_reference",
                        ContentGenerationJob.status == "submitting",
                        ContentGenerationJob.remote_task_id.is_(None),
                    )
                )
            ).scalars().all()
            cutoff = datetime.now() - timedelta(seconds=settings.fast_task_timeout)
            for job in stale_submitting:
                started = getattr(job, "browser_started_at", None)
                if started and started < cutoff:
                    await db.execute(
                        update(ContentGenerationJob)
                        .where(ContentGenerationJob.id == job.id)
                        .values(status="failed", error_message="stale_submitting")
                    )
                    if job.account_id:
                        await AccountLeaseService.release(db, job.account_id, job.id)

            await db.commit()
            logger.info(
                "Recovered %d queued, %d stale submitting jobs",
                len(queued),
                len(stale_submitting),
            )
