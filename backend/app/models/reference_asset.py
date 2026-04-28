"""
Dreamina Auto Register - 参考素材模型
"""

from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    ForeignKey,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.core.timezone import get_beijing_time


class ReferenceAsset(Base):
    """参考素材表"""

    __tablename__ = "reference_assets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), unique=True, nullable=False, index=True)
    alias = Column(String(512))
    asset_type = Column(String(20), default="image")
    file_path = Column(String(512), nullable=False)
    file_url = Column(String(1024))
    thumbnail_path = Column(String(512))
    file_size = Column(Integer)
    sha256 = Column(String(64))
    mime_type = Column(String(100))
    description = Column(Text)
    tags = Column(Text)
    usage_count = Column(Integer, default=0)

    created_at = Column(DateTime, default=get_beijing_time)
    updated_at = Column(
        DateTime, default=get_beijing_time, onupdate=get_beijing_time
    )

    job_references = relationship("ContentJobReference", back_populates="asset")

    def __repr__(self):
        return f"<ReferenceAsset(id={self.id}, name={self.name})>"


class ContentJobReference(Base):
    """内容生成任务-素材关联表"""

    __tablename__ = "content_job_references"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(
        Integer, ForeignKey("content_generation_jobs.id"), nullable=False
    )
    asset_id = Column(
        Integer, ForeignKey("reference_assets.id"), nullable=False
    )
    position = Column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint("job_id", "position", name="uq_job_position"),
    )

    asset = relationship("ReferenceAsset", back_populates="job_references")
    job = relationship("ContentGenerationJob")

    def __repr__(self):
        return f"<ContentJobReference(job_id={self.job_id}, asset_id={self.asset_id})>"
