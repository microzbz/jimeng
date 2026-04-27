"""
参考素材库服务 — CRUD + @mention 解析
"""

import hashlib
import re
import shutil
from pathlib import Path
from typing import List, Optional, Tuple

from sqlalchemy import select, text, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings, BASE_DIR
from app.models.reference_asset import ReferenceAsset, ContentJobReference
import logging

logger = logging.getLogger(__name__)

MENTION_RE = re.compile(r"@([A-Za-z0-9_\-一-鿿]+)")


def _assets_dir() -> Path:
    d = BASE_DIR / settings.fast_assets_dir
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


class ReferenceAssetService:

    @staticmethod
    async def list_assets(
        db: AsyncSession,
        search: Optional[str] = None,
        asset_type: Optional[str] = None,
    ) -> List[ReferenceAsset]:
        stmt = select(ReferenceAsset).order_by(ReferenceAsset.created_at.desc())
        if asset_type:
            stmt = stmt.where(ReferenceAsset.asset_type == asset_type)
        if search:
            pattern = f"%{search}%"
            stmt = stmt.where(
                (ReferenceAsset.name.ilike(pattern))
                | (ReferenceAsset.alias.ilike(pattern))
                | (ReferenceAsset.tags.ilike(pattern))
            )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def create_asset(
        db: AsyncSession,
        name: str,
        file_data: bytes,
        filename: str,
        mime_type: str,
        alias: Optional[str] = None,
        asset_type: str = "image",
        description: Optional[str] = None,
        tags: Optional[str] = None,
    ) -> ReferenceAsset:
        dest_dir = _assets_dir()
        ext = Path(filename).suffix or ".bin"
        safe_name = re.sub(r"[^\w\-]", "_", name)
        dest_path = dest_dir / f"{safe_name}{ext}"

        counter = 1
        while dest_path.exists():
            dest_path = dest_dir / f"{safe_name}_{counter}{ext}"
            counter += 1

        dest_path.write_bytes(file_data)

        rel_path = str(dest_path.relative_to(BASE_DIR)).replace("\\", "/")

        thumbnail_rel = None
        if asset_type == "image":
            try:
                from PIL import Image

                thumb_dir = BASE_DIR / "data" / "thumbnails" / "assets"
                thumb_dir.mkdir(parents=True, exist_ok=True)
                thumb_path = thumb_dir / f"{safe_name}_thumb.jpg"
                with Image.open(dest_path) as img:
                    img.thumbnail((256, 256))
                    img.convert("RGB").save(thumb_path, "JPEG", quality=80)
                thumbnail_rel = str(thumb_path.relative_to(BASE_DIR)).replace("\\", "/")
            except Exception as exc:
                logger.warning(f"Thumbnail generation failed: {exc}")

        asset = ReferenceAsset(
            name=name,
            alias=alias,
            asset_type=asset_type,
            file_path=rel_path,
            file_url=f"/fast-assets/{dest_path.name}",
            thumbnail_path=thumbnail_rel,
            file_size=len(file_data),
            sha256=hashlib.sha256(file_data).hexdigest(),
            mime_type=mime_type,
            description=description,
            tags=tags,
        )
        db.add(asset)
        await db.flush()
        await db.refresh(asset)
        return asset

    @staticmethod
    async def update_asset(
        db: AsyncSession,
        asset_id: int,
        name: Optional[str] = None,
        alias: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[str] = None,
        file_data: Optional[bytes] = None,
        filename: Optional[str] = None,
        mime_type: Optional[str] = None,
    ) -> Optional[ReferenceAsset]:
        asset = await db.get(ReferenceAsset, asset_id)
        if not asset:
            return None

        if name is not None:
            asset.name = name
        if alias is not None:
            asset.alias = alias
        if description is not None:
            asset.description = description
        if tags is not None:
            asset.tags = tags

        if file_data and filename:
            old_path = BASE_DIR / asset.file_path
            if old_path.exists():
                old_path.unlink()

            dest_dir = _assets_dir()
            ext = Path(filename).suffix or ".bin"
            safe_name = re.sub(r"[^\w\-]", "_", asset.name)
            dest_path = dest_dir / f"{safe_name}{ext}"
            dest_path.write_bytes(file_data)

            asset.file_path = str(dest_path.relative_to(BASE_DIR)).replace("\\", "/")
            asset.file_url = f"/fast-assets/{dest_path.name}"
            asset.file_size = len(file_data)
            asset.sha256 = hashlib.sha256(file_data).hexdigest()
            asset.mime_type = mime_type

        await db.flush()
        await db.refresh(asset)
        return asset

    @staticmethod
    async def delete_asset(db: AsyncSession, asset_id: int) -> bool:
        asset = await db.get(ReferenceAsset, asset_id)
        if not asset:
            return False

        ref_count = await db.scalar(
            select(func.count()).where(ContentJobReference.asset_id == asset_id)
        )
        if ref_count and ref_count > 0:
            from fastapi import HTTPException
            raise HTTPException(
                status_code=409,
                detail=f"素材被 {ref_count} 个任务引用，无法删除",
            )

        file_path = BASE_DIR / asset.file_path
        if file_path.exists():
            file_path.unlink()
        if asset.thumbnail_path:
            thumb = BASE_DIR / asset.thumbnail_path
            if thumb.exists():
                thumb.unlink()

        await db.delete(asset)
        await db.flush()
        return True

    # ---- @mention ----

    @staticmethod
    def extract_mentions(prompt: str) -> List[str]:
        return MENTION_RE.findall(prompt)

    @staticmethod
    async def resolve_mentions(
        db: AsyncSession, prompt: str
    ) -> Tuple[List[dict], List[str]]:
        names = ReferenceAssetService.extract_mentions(prompt)
        if not names:
            return [], []

        resolved: List[dict] = []
        missing: List[str] = []

        all_assets = (await db.execute(select(ReferenceAsset))).scalars().all()

        for mention_name in names:
            found = None
            for a in all_assets:
                if a.name == mention_name:
                    found = a
                    break
            if not found:
                for a in all_assets:
                    if a.alias:
                        aliases = [x.strip() for x in a.alias.split(",")]
                        if mention_name in aliases:
                            found = a
                            break
            if found:
                resolved.append(
                    {
                        "name": mention_name,
                        "asset_id": found.id,
                        "file_path": found.file_path,
                    }
                )
            else:
                missing.append(mention_name)

        return resolved, missing

    @staticmethod
    async def increment_usage(db: AsyncSession, asset_id: int) -> None:
        await db.execute(
            text(
                "UPDATE reference_assets SET usage_count = usage_count + 1 WHERE id = :id"
            ),
            {"id": asset_id},
        )

    @staticmethod
    async def create_job_references(
        db: AsyncSession,
        job_id: int,
        resolved_mentions: List[dict],
    ) -> None:
        for idx, mention in enumerate(resolved_mentions):
            ref = ContentJobReference(
                job_id=job_id,
                asset_id=mention["asset_id"],
                position=idx,
            )
            db.add(ref)
            await ReferenceAssetService.increment_usage(db, mention["asset_id"])
        await db.flush()
