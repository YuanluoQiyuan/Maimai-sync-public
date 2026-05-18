"""SQLAlchemy ORM 模型

三张核心表：
- users: 用户账号（密码、水鱼 Token、代理等配置）
- score_snapshots: 每次同步的快照元信息（rating、时间等）
- score_records: 单条成绩记录（关联快照和用户）
"""

from datetime import datetime
from typing import Optional

from maimai_sync.utils import to_utc_iso

from sqlalchemy import String, Integer, Boolean, Text, DateTime, Float, ForeignKey, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """ORM 基类"""
    pass


class User(Base):
    """用户表"""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    # 水鱼 Import Token
    df_import_token: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    # 备注
    note: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    # 注册时间
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    # 昵称（排行榜显示用）
    nickname: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # 管理员标记
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    # 最后登录时间
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # 关系
    snapshots: Mapped[list["ScoreSnapshot"]] = relationship(back_populates="user", cascade="all, delete-orphan")

    def to_profile_dict(self) -> dict:
        """转为前端友好的配置字典（不含密码）"""
        return {
            "id": self.id,
            "username": self.username,
            "df_import_token": self.df_import_token,
            "note": self.note,
            "nickname": self.nickname,
            "is_admin": self.is_admin,
            "has_df_token": self.df_import_token is not None and len(self.df_import_token) > 0,
            "created_at": to_utc_iso(self.created_at),
            "last_login_at": to_utc_iso(self.last_login_at),
        }


class ScoreSnapshot(Base):
    """成绩快照表 — 每次同步存一条"""
    __tablename__ = "score_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    # 快照时间
    snapshot_time: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    # 总 Rating
    rating: Mapped[int] = mapped_column(Integer, default=0)
    # 成绩总数
    score_count: Mapped[int] = mapped_column(Integer, default=0)
    # 原始水鱼上传格式数据（JSON 数组，用于重传水鱼）
    upload_data_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 关系
    user: Mapped["User"] = relationship(back_populates="snapshots")
    records: Mapped[list["ScoreRecord"]] = relationship(back_populates="snapshot", cascade="all, delete-orphan")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "snapshot_time": to_utc_iso(self.snapshot_time),
            "rating": self.rating,
            "score_count": self.score_count,
        }


class ScoreRecord(Base):
    """单条成绩记录"""
    __tablename__ = "score_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(Integer, ForeignKey("score_snapshots.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    # 歌曲信息
    song_id: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    song_type: Mapped[str] = mapped_column(String(16), nullable=False, default="")  # standard / dx / utage
    level: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    level_index: Mapped[int] = mapped_column(Integer, default=0)
    # 成绩
    achievements: Mapped[float] = mapped_column(Float, default=0.0)
    dx_score: Mapped[int] = mapped_column(Integer, default=0)
    dx_rating: Mapped[float] = mapped_column(Float, default=0.0)
    dx_star: Mapped[int] = mapped_column(Integer, default=0)
    # FC / FS / Rate
    fc: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    fs: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    rate: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    # 版本标记
    is_new: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    # 关系
    snapshot: Mapped["ScoreSnapshot"] = relationship(back_populates="records")

    # 索引：按用户 + 歌曲快速查
    __table_args__ = (
        Index("ix_score_records_user_song", "user_id", "song_id", "song_type", "level_index"),
        Index("ix_score_records_user_rating", "user_id", "dx_rating", "is_new"),
    )

    def to_dict(self) -> dict:
        from maimai_sync.utils.score_merge import compute_rate, compute_dx_star, lookup_max_dx_score
        return {
            "id": self.song_id,
            "title": self.title,
            "type": self.song_type,
            "level": self.level,
            "level_index": self.level_index,
            "achievements": self.achievements,
            "dx_score": self.dx_score,
            "dx_rating": self.dx_rating,
            "dx_star": compute_dx_star(self.dx_score, lookup_max_dx_score(self.song_id, self.song_type, self.level_index)),
            "fc": self.fc,
            "fs": self.fs,
            "rate": compute_rate(self.achievements),
            "is_new": self.is_new,
        }



