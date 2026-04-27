"""
Database migration helpers for lightweight schema updates.
"""

from sqlalchemy import text
from app.core.database import async_session_factory
import logging

logger = logging.getLogger(__name__)


async def ensure_proxy_node_columns():
    """Ensure proxy_nodes has external proxy columns."""
    columns = [
        ("host", "VARCHAR(255)"),
        ("port", "INTEGER"),
        ("username", "VARCHAR(255)"),
        ("password", "VARCHAR(255)"),
        ("protocol", "VARCHAR(20)"),
        ("source", "VARCHAR(20) DEFAULT 'clash'"),
    ]

    async with async_session_factory() as session:
        result = await session.execute(text("PRAGMA table_info(proxy_nodes)"))
        existing_cols = {row[1] for row in result.fetchall()}

        for col_name, col_def in columns:
            if col_name in existing_cols:
                continue
            try:
                await session.execute(
                    text(f"ALTER TABLE proxy_nodes ADD COLUMN {col_name} {col_def}")
                )
                logger.info(f"Added column proxy_nodes.{col_name}")
            except Exception as exc:
                logger.error(f"Failed to add column {col_name}: {exc}")

        await session.commit()


async def ensure_accounts_login_status_column():
    """Ensure accounts has last_login_status column for consistency checks."""
    async with async_session_factory() as session:
        result = await session.execute(text("PRAGMA table_info(accounts)"))
        existing_cols = {row[1] for row in result.fetchall()}

        if "last_login_status" not in existing_cols:
            try:
                await session.execute(
                    text(
                        "ALTER TABLE accounts ADD COLUMN last_login_status VARCHAR(20)"
                    )
                )
                logger.info("Added column accounts.last_login_status")
                await session.commit()
            except Exception as exc:
                logger.error(f"Failed to add accounts.last_login_status: {exc}")


async def ensure_task_records_email_source_column():
    """Ensure task_records has email_source column for outlook mode support."""
    async with async_session_factory() as session:
        result = await session.execute(text("PRAGMA table_info(task_records)"))
        existing_cols = {row[1] for row in result.fetchall()}

        if "email_source" not in existing_cols:
            try:
                await session.execute(
                    text(
                        "ALTER TABLE task_records ADD COLUMN email_source VARCHAR(20) DEFAULT 'cloudflare'"
                    )
                )
                logger.info("Added column task_records.email_source")
                await session.commit()
            except Exception as exc:
                logger.error(f"Failed to add task_records.email_source: {exc}")


async def ensure_accounts_generation_columns():
    """Ensure accounts has content generation pool columns."""
    columns = [
        ("gen_enabled", "BOOLEAN DEFAULT 0"),
        ("gen_enabled_at", "DATETIME"),
        ("gen_last_used_at", "DATETIME"),
        ("gen_locked_until", "DATETIME"),
        ("gen_auto_disabled_reason", "VARCHAR(255)"),
    ]

    async with async_session_factory() as session:
        result = await session.execute(text("PRAGMA table_info(accounts)"))
        existing_cols = {row[1] for row in result.fetchall()}

        for col_name, col_def in columns:
            if col_name in existing_cols:
                continue
            try:
                await session.execute(
                    text(f"ALTER TABLE accounts ADD COLUMN {col_name} {col_def}")
                )
                logger.info(f"Added column accounts.{col_name}")
            except Exception as exc:
                logger.error(f"Failed to add accounts.{col_name}: {exc}")

        await session.commit()


async def ensure_content_generation_jobs_table():
    """Ensure content_generation_jobs table exists and has async task columns."""
    async with async_session_factory() as session:
        result = await session.execute(
            text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='content_generation_jobs'"
            )
        )
        exists = result.scalar() is not None
        if not exists:
            try:
                await session.execute(
                    text(
                        """
                    CREATE TABLE content_generation_jobs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        job_type VARCHAR(20) NOT NULL,
                        status VARCHAR(20) DEFAULT 'queued',
                        prompt TEXT,
                        model VARCHAR(100),
                        ratio VARCHAR(20),
                        resolution VARCHAR(20),
                        duration INTEGER,
                        function_mode VARCHAR(50),
                        input_images TEXT,
                        output_urls TEXT,
                        thumbnail_urls TEXT,
                        local_urls TEXT,
                        error_message TEXT,
                        remote_task_id TEXT,
                        remote_history_id TEXT,
                        remote_kind TEXT,
                        remote_status TEXT,
                        remote_fail_code TEXT,
                        remote_error_message TEXT,
                        account_id INTEGER,
                        region VARCHAR(50),
                        submitted_at DATETIME,
                        finished_at DATETIME,
                        created_at DATETIME,
                        updated_at DATETIME,
                        FOREIGN KEY(account_id) REFERENCES accounts(id)
                    )
                    """
                    )
                )
                logger.info("Created table content_generation_jobs")
                await session.commit()
            except Exception as exc:
                logger.error(f"Failed to create content_generation_jobs: {exc}")
        else:
            col_result = await session.execute(
                text("PRAGMA table_info(content_generation_jobs)")
            )
            existing_cols = {row[1] for row in col_result.fetchall()}
            columns = [
                ("thumbnail_urls", "TEXT"),
                ("local_urls", "TEXT"),
                ("function_mode", "VARCHAR(50)"),
                ("remote_task_id", "TEXT"),
                ("remote_history_id", "TEXT"),
                ("remote_kind", "TEXT"),
                ("remote_status", "TEXT"),
                ("remote_fail_code", "TEXT"),
                ("remote_error_message", "TEXT"),
                ("submitted_at", "DATETIME"),
                ("finished_at", "DATETIME"),
            ]

            for col_name, col_def in columns:
                if col_name in existing_cols:
                    continue
                try:
                    await session.execute(
                        text(
                            f"ALTER TABLE content_generation_jobs ADD COLUMN {col_name} {col_def}"
                        )
                    )
                    logger.info(f"Added column content_generation_jobs.{col_name}")
                    await session.commit()
                except Exception as exc:
                    logger.error(
                        f"Failed to add {col_name} to content_generation_jobs: {exc}"
                    )


async def ensure_fast_reference_tables():
    """Create reference_assets and content_job_references tables if not exist."""
    async with async_session_factory() as session:
        await session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS reference_assets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name VARCHAR(255) NOT NULL UNIQUE,
                    alias VARCHAR(512),
                    asset_type VARCHAR(20) DEFAULT 'image',
                    file_path VARCHAR(512) NOT NULL,
                    file_url VARCHAR(1024),
                    thumbnail_path VARCHAR(512),
                    file_size INTEGER DEFAULT 0,
                    sha256 VARCHAR(64),
                    mime_type VARCHAR(100),
                    description TEXT,
                    tags VARCHAR(512),
                    usage_count INTEGER DEFAULT 0,
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            )
        )
        await session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS content_job_references (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL,
                    asset_id INTEGER NOT NULL,
                    position INTEGER DEFAULT 0,
                    FOREIGN KEY(job_id) REFERENCES content_generation_jobs(id) ON DELETE CASCADE,
                    FOREIGN KEY(asset_id) REFERENCES reference_assets(id) ON DELETE RESTRICT,
                    UNIQUE(job_id, position)
                )
                """
            )
        )
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS ix_reference_assets_name ON reference_assets(name)",
            "CREATE INDEX IF NOT EXISTS ix_reference_assets_asset_type ON reference_assets(asset_type)",
            "CREATE INDEX IF NOT EXISTS ix_content_job_references_job_id ON content_job_references(job_id)",
            "CREATE INDEX IF NOT EXISTS ix_content_job_references_asset_id ON content_job_references(asset_id)",
        ]:
            await session.execute(text(idx_sql))
        await session.commit()
        logger.info("Ensured fast_reference tables exist")


async def ensure_fast_reference_fields():
    """Add fast_reference columns to content_generation_jobs."""
    columns = [
        ("retry_count", "INTEGER DEFAULT 0"),
        ("max_retry", "INTEGER DEFAULT 10"),
        ("video_url", "VARCHAR(1024)"),
        ("browser_session_log", "TEXT"),
        ("polling_region", "VARCHAR(20)"),
        ("browser_started_at", "DATETIME"),
        ("browser_finished_at", "DATETIME"),
    ]

    async with async_session_factory() as session:
        result = await session.execute(
            text("PRAGMA table_info(content_generation_jobs)")
        )
        existing_cols = {row[1] for row in result.fetchall()}

        for col_name, col_def in columns:
            if col_name in existing_cols:
                continue
            try:
                await session.execute(
                    text(
                        f"ALTER TABLE content_generation_jobs ADD COLUMN {col_name} {col_def}"
                    )
                )
                logger.info(f"Added column content_generation_jobs.{col_name}")
            except Exception as exc:
                logger.error(
                    f"Failed to add {col_name} to content_generation_jobs: {exc}"
                )

        await session.commit()


async def ensure_accounts_fast_enabled():
    """Add fast_enabled column to accounts table."""
    async with async_session_factory() as session:
        result = await session.execute(text("PRAGMA table_info(accounts)"))
        existing_cols = {row[1] for row in result.fetchall()}

        if "fast_enabled" not in existing_cols:
            try:
                await session.execute(
                    text(
                        "ALTER TABLE accounts ADD COLUMN fast_enabled BOOLEAN DEFAULT 0"
                    )
                )
                logger.info("Added column accounts.fast_enabled")
            except Exception as exc:
                logger.error(f"Failed to add accounts.fast_enabled: {exc}")

        await session.commit()
