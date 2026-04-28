"""
ReferenceAssetService — 参考素材管理
"""

import logging
import re
import hashlib
from pathlib import Path
from typing import List, Optional, Tuple

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.reference_asset import ReferenceAsset

logger = logging.getLogger(__name__)

MENTION_RE = re.compile(r"@([A-Za-z0-9_\-一-鿿]+)")


class ReferenceAssetService:

    @staticmethod
    async def list_assets(
        db: AsyncSession,
        search: Optional[str] = None,
        offset: int = 0,
        limit: int = 50,
    ) -> List[ReferenceAsset]:
        stmt = select(ReferenceAsset).order_by(ReferenceAsset.id.desc())
        if search:
            stmt = stmt.where(
                ReferenceAsset.name.contains(search)
                | ReferenceAsset.alias.contains(search)
            )
        stmt = stmt.offset(offset).limit(limit)
        return list((await db.execute(stmt)).scalars().all())

    @staticmethod
    async def get_asset(db: AsyncSession, asset_id: int) -> Optional[ReferenceAsset]:
        return (
            await db.execute(
                select(ReferenceAsset).where(ReferenceAsset.id == asset_id)
            )
        ).scalar_one_or_none()

    @staticmethod
    async def get_asset_by_name(
        db: AsyncSession, name: str
    ) -> Optional[ReferenceAsset]:
        return (
            await db.execute(
                select(ReferenceAsset).where(ReferenceAsset.name == name)
            )
        ).scalar_one_or_none()

    @staticmethod
    async def create_asset(
        db: AsyncSession,
        name: str,
        file_path: str,
        *,
        alias: Optional[str] = None,
        asset_type: str = "image",
        file_url: Optional[str] = None,
        thumbnail_path: Optional[str] = None,
        file_size: Optional[int] = None,
        sha256: Optional[str] = None,
        mime_type: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[str] = None,
    ) -> ReferenceAsset:
        asset = ReferenceAsset(
            name=name,
            file_path=file_path,
            alias=alias,
            asset_type=asset_type,
            file_url=file_url,
            thumbnail_path=thumbnail_path,
            file_size=file_size,
            sha256=sha256,
            mime_type=mime_type,
            description=description,
            tags=tags,
        )
        db.add(asset)
        await db.commit()
        await db.refresh(asset)
        return asset

    @staticmethod
    async def update_asset(
        db: AsyncSession, asset_id: int, **kwargs
    ) -> Optional[ReferenceAsset]:
        asset = (
            await db.execute(
                select(ReferenceAsset).where(ReferenceAsset.id == asset_id)
            )
        ).scalar_one_or_none()
        if not asset:
            return None
        for k, v in kwargs.items():
            if hasattr(asset, k):
                setattr(asset, k, v)
        await db.commit()
        await db.refresh(asset)
        return asset

    @staticmethod
    async def delete_asset(db: AsyncSession, asset_id: int) -> bool:
        asset = (
            await db.execute(
                select(ReferenceAsset).where(ReferenceAsset.id == asset_id)
            )
        ).scalar_one_or_none()
        if not asset:
            return False
        await db.delete(asset)
        await db.commit()
        return True

    @staticmethod
    def extract_mentions(prompt: str) -> List[str]:
        return MENTION_RE.findall(prompt)

    @staticmethod
    async def resolve_mentions(
        db: AsyncSession, mention_names: List[str]
    ) -> Tuple[List[Tuple[str, int, str]], List[str]]:
        resolved: List[Tuple[str, int, str]] = []
        missing: List[str] = []

        all_assets = list(
            (await db.execute(select(ReferenceAsset))).scalars().all()
        )

        for name in mention_names:
            found = None
            for asset in all_assets:
                if asset.name == name:
                    found = asset
                    break
            if not found:
                for asset in all_assets:
                    if asset.alias:
                        aliases = [a.strip() for a in asset.alias.split(",")]
                        if name in aliases:
                            found = asset
                            break
            if found:
                resolved.append((name, found.id, found.file_path))
            else:
                missing.append(name)

        return resolved, missing

    @staticmethod
    async def increment_usage(db: AsyncSession, asset_id: int) -> None:
        await db.execute(
            update(ReferenceAsset)
            .where(ReferenceAsset.id == asset_id)
            .values(usage_count=ReferenceAsset.usage_count + 1)
        )
        await db.commit()

    @staticmethod
    def get_assets_dir() -> Path:
        path = Path(settings.fast_assets_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def compute_sha256(file_bytes: bytes) -> str:
        return hashlib.sha256(file_bytes).hexdigest()
