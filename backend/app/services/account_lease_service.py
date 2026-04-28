"""
AccountLeaseService — 账号租约管理（共享服务）
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account

logger = logging.getLogger(__name__)


class AccountLeaseService:

    @staticmethod
    async def acquire(
        db: AsyncSession,
        purpose: str,
        job_id: int,
        lease_seconds: int = 600,
    ) -> Optional[Account]:
        now = datetime.now()

        if purpose == "fast_reference":
            pool_filter = Account.fast_enabled == True
        else:
            pool_filter = Account.gen_enabled == True

        stmt = (
            select(Account)
            .where(pool_filter)
            .where(Account.session_id.isnot(None))
            .where(Account.health_status == "healthy")
            .order_by(Account.gen_last_used_at.asc().nullsfirst(), Account.id.asc())
            .limit(20)
        )
        candidates = (await db.execute(stmt)).scalars().all()

        for account in candidates:
            locked_until = account.__dict__.get("gen_locked_until")
            if locked_until and locked_until > now:
                continue

            result = await db.execute(
                update(Account)
                .where(Account.id == account.id)
                .where(
                    (Account.gen_locked_until.is_(None))
                    | (Account.gen_locked_until <= now)
                )
                .values(
                    gen_locked_until=now + timedelta(seconds=lease_seconds),
                    gen_last_used_at=now,
                    gen_lock_job_id=job_id,
                )
            )
            if result.rowcount == 0:
                continue

            await db.commit()
            await db.refresh(account)
            logger.info(
                "Account #%d leased for %s (job #%d)", account.id, purpose, job_id
            )
            return account

        return None

    @staticmethod
    async def release(
        db: AsyncSession,
        account_id: int,
        job_id: int,
    ) -> bool:
        result = await db.execute(
            update(Account)
            .where(Account.id == account_id)
            .where(Account.gen_lock_job_id == job_id)
            .values(gen_locked_until=None, gen_lock_job_id=None)
        )
        await db.commit()
        released = result.rowcount > 0
        if released:
            logger.info("Account #%d lease released (job #%d)", account_id, job_id)
        else:
            logger.warning(
                "Account #%d lease release skipped — lock owner mismatch (job #%d)",
                account_id,
                job_id,
            )
        return released

    @staticmethod
    async def extend(
        db: AsyncSession,
        account_id: int,
        job_id: int,
        seconds: int = 600,
    ) -> bool:
        now = datetime.now()
        result = await db.execute(
            update(Account)
            .where(Account.id == account_id)
            .where(Account.gen_lock_job_id == job_id)
            .values(gen_locked_until=now + timedelta(seconds=seconds))
        )
        await db.commit()
        return result.rowcount > 0
