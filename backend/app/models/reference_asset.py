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
    name = Column(String(255), unique=True, nullable=False, index=True, comment="素材名称")
    alias = Column(String(512), comment="别名（逗号分隔）")
    asset_type = Column(String(20), default="image", index=True, comment="类型: image/video")
    file_path = Column(String(512), nullable=False, comment="文件相对路径")
    file_url = Column(String(1024), comment="文件访问 URL")
    thumbnail_path = Column(String(512), comment="缩略图路径")
    file_size = Column(Integer, default=0, comment="文件大小(bytes)")
    sha256 = Column(String(64), comment="文件 SHA256")
    mime_type = Column(String(100), comment="MIME 类型")
    description = Column(Text, comment="描述")
    tags = Column(String(512), comment="标签（逗号分隔）")
    usage_count = Column(Integer, default=0, comment="使用次数")
    created_at = Column(DateTime, default=get_beijing_time, comment="创建时间")
    updated_at = Column(
        DateTime,
        default=get_beijing_time,
        onupdate=get_beijing_time,
        comment="更新时间",
    )

    job_references = relationship("ContentJobReference", back_populates="asset")

    def __repr__(self):
        return f"<ReferenceAsset(id={self.id}, name={self.name})>"


class ContentJobReference(Base):
    """内容生成任务-素材关联表"""

    __tablename__ = "content_job_references"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(
        Integer,
        ForeignKey("content_generation_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="任务ID",
    )
    asset_id = Column(
        Integer,
        ForeignKey("reference_assets.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="素材ID",
    )
    position = Column(Integer, default=0, comment="引用位置")

    __table_args__ = (
        UniqueConstraint("job_id", "position", name="uq_job_position"),
    )

    asset = relationship("ReferenceAsset", back_populates="job_references")

    def __repr__(self):
        return f"<ContentJobReference(job_id={self.job_id}, asset_id={self.asset_id})>"
