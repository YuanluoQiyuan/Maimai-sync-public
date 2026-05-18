"""数据库 CRUD 操作

封装所有数据库读写逻辑，供 account/manager.py 和 web/app.py 调用。
"""

import json
import logging
from datetime import datetime

from maimai_sync.utils import to_utc_iso
from typing import Optional

from sqlalchemy import select, func, desc, and_, delete
from sqlalchemy.ext.asyncio import AsyncSession

from maimai_sync.account.auth import hash_password, verify_password
from maimai_sync.db.engine import get_session_factory
from maimai_sync.db.models import User, ScoreSnapshot, ScoreRecord

logger = logging.getLogger(__name__)


# ============================================================
# 用户 CRUD
# ============================================================

async def db_get_user_by_username(session: AsyncSession, username: str) -> Optional[User]:
    """按用户名查找用户"""
    result = await session.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()


async def db_get_user_by_id(session: AsyncSession, user_id: int) -> Optional[User]:
    """按 ID 查找用户"""
    return await session.get(User, user_id)


async def db_create_user(session: AsyncSession, username: str, password: str) -> User:
    """创建用户"""
    # 检查重名
    existing = await db_get_user_by_username(session, username)
    if existing:
        raise ValueError(f"用户 '{username}' 已存在")
    if not username.strip():
        raise ValueError("用户名不能为空")
    if len(password) < 4:
        raise ValueError("密码至少 4 位")

    user = User(
        username=username,
        password_hash=hash_password(password),
        created_at=datetime.utcnow(),
    )
    session.add(user)
    await session.flush()
    logger.info("注册成功: %s (id=%d)", username, user.id)
    return user


async def db_verify_login(session: AsyncSession, username: str, password: str) -> User:
    """验证登录，返回 User 对象"""
    user = await db_get_user_by_username(session, username)
    if not user:
        raise ValueError(f"用户 '{username}' 不存在")
    if not verify_password(password, user.password_hash):
        raise ValueError("密码错误")
    # 更新最后登录时间
    user.last_login_at = datetime.utcnow()
    await session.flush()
    return user


async def db_delete_user(session: AsyncSession, username: str, password: str) -> bool:
    """删除用户（需验证密码）"""
    user = await db_get_user_by_username(session, username)
    if not user:
        raise ValueError(f"用户 '{username}' 不存在")
    if not verify_password(password, user.password_hash):
        raise ValueError("密码错误")
    await session.delete(user)
    await session.flush()
    logger.info("用户已删除: %s", username)
    return True


async def db_change_password(session: AsyncSession, username: str, old_password: str, new_password: str) -> bool:
    """修改密码"""
    user = await db_get_user_by_username(session, username)
    if not user:
        raise ValueError(f"用户 '{username}' 不存在")
    if not verify_password(old_password, user.password_hash):
        raise ValueError("旧密码错误")
    if len(new_password) < 4:
        raise ValueError("新密码至少 4 位")
    user.password_hash = hash_password(new_password)
    await session.flush()
    return True


async def db_update_user_profile(session: AsyncSession, username: str, **kwargs) -> User:
    """更新用户配置字段"""
    user = await db_get_user_by_username(session, username)
    if not user:
        raise ValueError(f"用户 '{username}' 不存在")

    updatable = {"df_import_token", "nickname", "note"}
    for key, value in kwargs.items():
        if key in updatable:
            setattr(user, key, value)
        elif key == "password_hash":
            raise ValueError("请用 db_change_password() 修改密码")
        else:
            raise ValueError(f"不可更新字段: {key}")

    await session.flush()
    logger.info("配置已更新: %s → %s", username, list(kwargs.keys()))
    return user


async def db_list_users(session: AsyncSession) -> list[str]:
    """列出所有用户名"""
    result = await session.execute(select(User.username).order_by(User.id))
    return [row[0] for row in result.all()]


# ============================================================
# 成绩快照 CRUD
# ============================================================

async def db_save_snapshot(
    session: AsyncSession,
    user_id: int,
    rating: int,
    score_count: int,
    upload_data: Optional[list[dict]] = None,
) -> ScoreSnapshot:
    """保存一次同步快照"""
    snapshot = ScoreSnapshot(
        user_id=user_id,
        snapshot_time=datetime.utcnow(),
        rating=rating,
        score_count=score_count,
        upload_data_json=json.dumps(upload_data, ensure_ascii=False) if upload_data else None,
    )
    session.add(snapshot)
    await session.flush()
    return snapshot


async def db_get_snapshots(session: AsyncSession, user_id: int) -> list[ScoreSnapshot]:
    """获取用户的所有快照（按时间倒序）"""
    result = await session.execute(
        select(ScoreSnapshot)
        .where(ScoreSnapshot.user_id == user_id)
        .order_by(desc(ScoreSnapshot.snapshot_time))
    )
    return list(result.scalars().all())


async def db_delete_snapshot(session: AsyncSession, user_id: int, snapshot_id: int) -> bool:
    """删除指定快照及其关联的成绩记录"""
    # 先验证快照属于该用户
    result = await session.execute(
        select(ScoreSnapshot).where(
            ScoreSnapshot.id == snapshot_id,
            ScoreSnapshot.user_id == user_id,
        )
    )
    snapshot = result.scalar_one_or_none()
    if not snapshot:
        return False

    # 删除关联的成绩记录
    await session.execute(
        delete(ScoreRecord).where(ScoreRecord.snapshot_id == snapshot_id)
    )
    # 删除快照本身
    await session.execute(
        delete(ScoreSnapshot).where(ScoreSnapshot.id == snapshot_id)
    )
    await session.commit()
    return True


async def db_get_latest_snapshot(session: AsyncSession, user_id: int) -> Optional[ScoreSnapshot]:
    """获取用户最新的快照"""
    result = await session.execute(
        select(ScoreSnapshot)
        .where(ScoreSnapshot.user_id == user_id)
        .order_by(desc(ScoreSnapshot.snapshot_time))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def db_get_snapshot_by_id(session: AsyncSession, snapshot_id: int) -> Optional[ScoreSnapshot]:
    """按 ID 获取快照"""
    return await session.get(ScoreSnapshot, snapshot_id)


# ============================================================
# 成绩记录 CRUD
# ============================================================

async def db_save_score_records(
    session: AsyncSession,
    snapshot_id: int,
    user_id: int,
    scores: list[dict],
) -> int:
    """批量保存成绩记录

    Args:
        scores: 已包含 title、is_new 等字段的字典列表

    Returns:
        保存的记录数
    """
    records = []
    for s in scores:
        record = ScoreRecord(
            snapshot_id=snapshot_id,
            user_id=user_id,
            song_id=s.get("id", 0),
            title=s.get("title", ""),
            song_type=s.get("type", ""),
            level=s.get("level", ""),
            level_index=s.get("level_index", 0),
            achievements=float(s.get("achievements", 0) or 0),
            dx_score=int(s.get("dx_score", 0) or 0),
            dx_rating=float(s.get("dx_rating", 0) or 0),
            dx_star=int(s.get("dx_star", 0) or 0),
            fc=s.get("fc"),
            fs=s.get("fs"),
            rate=s.get("rate"),
            is_new=bool(s.get("is_new", False)),
        )
        records.append(record)

    session.add_all(records)
    await session.flush()
    return len(records)


async def db_get_scores_by_snapshot(session: AsyncSession, snapshot_id: int, user_id: Optional[int] = None) -> list[ScoreRecord]:
    """获取某个快照的所有成绩，可选按 user_id 二次校验"""
    conditions = [ScoreRecord.snapshot_id == snapshot_id]
    if user_id is not None:
        conditions.append(ScoreRecord.user_id == user_id)
    result = await session.execute(
        select(ScoreRecord)
        .where(and_(*conditions))
        .order_by(ScoreRecord.id)
    )
    return list(result.scalars().all())


async def db_get_latest_scores(session: AsyncSession, user_id: int) -> list[ScoreRecord]:
    """获取用户最新快照的所有成绩"""
    snapshot = await db_get_latest_snapshot(session, user_id)
    if not snapshot:
        return []
    return await db_get_scores_by_snapshot(session, snapshot.id)


# ============================================================
# 排行榜查询
# ============================================================

async def db_get_leaderboard(session: AsyncSession, limit: int = 50) -> list[dict]:
    """获取 Rating 排行榜

    返回每个用户的最新 Rating，按 Rating 倒序排列。
    """
    # 子查询：每个用户的最新快照 ID
    latest = (
        select(
            ScoreSnapshot.user_id,
            func.max(ScoreSnapshot.id).label("max_id"),
        )
        .group_by(ScoreSnapshot.user_id)
        .subquery()
    )

    # 关联查询
    result = await session.execute(
        select(User.username, User.nickname, ScoreSnapshot.rating, ScoreSnapshot.score_count, ScoreSnapshot.snapshot_time)
        .join(latest, and_(User.id == latest.c.user_id))
        .join(ScoreSnapshot, ScoreSnapshot.id == latest.c.max_id)
        .order_by(desc(ScoreSnapshot.rating))
        .limit(limit)
    )

    rows = result.all()
    return [
        {
            "rank": i + 1,
            "username": row.username,
            "nickname": row.nickname or row.username,
            "rating": row.rating,
            "score_count": row.score_count,
            "snapshot_time": to_utc_iso(row.snapshot_time),
        }
        for i, row in enumerate(rows)
    ]


async def db_get_user_b50(session: AsyncSession, user_id: int) -> dict:
    """获取用户的 B50 详情（B35 + B15）

    从最新快照的成绩中，按 is_new 分类取 Rating 最高的各 35/15 首。
    """
    snapshot = await db_get_latest_snapshot(session, user_id)
    if not snapshot:
        return {"b35": [], "b15": [], "rating": 0}

    # B35：旧曲 dx_rating 最高的 35 首
    b35_result = await session.execute(
        select(ScoreRecord)
        .where(and_(ScoreRecord.snapshot_id == snapshot.id, ScoreRecord.is_new == False, ScoreRecord.song_type != "utage", ScoreRecord.dx_rating > 0))
        .order_by(desc(ScoreRecord.dx_rating))
        .limit(35)
    )
    b35 = [r.to_dict() for r in b35_result.scalars().all()]

    # B15：新曲 dx_rating 最高的 15 首
    b15_result = await session.execute(
        select(ScoreRecord)
        .where(and_(ScoreRecord.snapshot_id == snapshot.id, ScoreRecord.is_new == True, ScoreRecord.song_type != "utage", ScoreRecord.dx_rating > 0))
        .order_by(desc(ScoreRecord.dx_rating))
        .limit(15)
    )
    b15 = [r.to_dict() for r in b15_result.scalars().all()]

    return {
        "rating": snapshot.rating,
        "b35": b35,
        "b15": b15,
        "b35_rating": sum(r["dx_rating"] for r in b35),
        "b15_rating": sum(r["dx_rating"] for r in b15),
    }
