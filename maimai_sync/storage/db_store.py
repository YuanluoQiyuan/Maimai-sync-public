"""MySQL 数据库成绩存储

替代 JsonScoreStorage，将成绩数据写入 MySQL 数据库。
"""

import json
import logging
from datetime import datetime
from typing import Optional

from maimai_sync.models import MaimaiScores, PlayerIdentifier
from maimai_sync.config import DEFAULT_DATA_DIR
from maimai_sync.utils import to_utc_iso

logger = logging.getLogger(__name__)


def _json_safe(val):
    """确保值可 JSON 序列化"""
    if val is None:
        return None
    if isinstance(val, (str, int, float, bool)):
        return val
    if hasattr(val, "value"):
        return val.value
    return str(val)


_LEVEL_LABELS = ["Basic", "Advanced", "Expert", "Master", "Re:MASTER"]


def _score_to_dict(score) -> dict:
    """将 RawScore 对象转为可序列化的字典"""
    music_id = getattr(score, "music_id", 0) or 0
    achievement_raw = getattr(score, "achievement", 0) or 0
    fc_int = getattr(score, "fc_status", 0) or 0
    fs_int = getattr(score, "fs_status", 0) or 0
    level_index = getattr(score, "level", 0) or 0

    combo_map = ["", "fc", "fcp", "ap", "app"]
    sync_map = ["", "fs", "fsp", "fsd", "fsdp", "sync"]

    return {
        "id": music_id,
        "level": _LEVEL_LABELS[level_index] if 0 <= level_index < 5 else str(level_index),
        "level_index": level_index,
        "achievements": achievement_raw / 10000.0,
        "dx_score": getattr(score, "dx_score", 0) or 0,
        "dx_rating": 0,
        "dx_star": 0,
        "fc": combo_map[min(fc_int, 4)] if 0 <= fc_int < 5 else "",
        "fs": sync_map[min(fs_int, 5)] if 0 <= fs_int < 6 else "",
        "rate": "",
        "type": "dx" if music_id >= 10000 else "standard",
    }


class DbScoreStorage:
    """MySQL 数据库成绩存储

    成绩数据写入数据库，不再使用 JSON 文件。
    保留与 JsonScoreStorage 相同的接口，方便无缝替换。
    """

    def __init__(self, data_dir=None, username: Optional[str] = None):
        self._username = username
        self._root_dir = data_dir or DEFAULT_DATA_DIR

    @property
    def username(self) -> Optional[str]:
        return self._username

    async def save(
        self,
        identifier: PlayerIdentifier,
        scores: MaimaiScores,
        upload_data: list[dict],
    ) -> str:
        """保存成绩到数据库

        Returns:
            快照 ID（字符串形式）
        """
        import traceback as _tb
        from maimai_sync.db.engine import get_session_factory
        from maimai_sync.db.crud import (
            db_get_user_by_username,
            db_save_snapshot,
            db_save_score_records,
        )
        from maimai_sync.utils.score_merge import merge_score_data

        try:
            async with get_session_factory()() as session:
                async with session.begin():
                    user = await db_get_user_by_username(session, self._username)
                    if not user:
                        raise ValueError(f"用户 '{self._username}' 不存在")

                    raw_dicts = [_score_to_dict(s) for s in scores.scores]
                    temp_data = {
                        "scores_raw": raw_dicts,
                        "scores_upload": upload_data,
                    }
                    merged = merge_score_data(temp_data)

                    snapshot = await db_save_snapshot(
                        session,
                        user_id=user.id,
                        rating=scores.rating,
                        score_count=len(scores.scores),
                        upload_data=None,  # 暂不存 upload_data，避免 TEXT 溢出
                    )

                    count = await db_save_score_records(
                        session,
                        snapshot_id=snapshot.id,
                        user_id=user.id,
                        scores=merged,
                    )

                    logger.info("成绩已保存到数据库: 用户=%s, 快照=%d, %d条记录",
                                 self._username, snapshot.id, count)
                    return str(snapshot.id)
        except Exception:
            logger.error("保存成绩到数据库失败:\n%s", _tb.format_exc())
            raise

    async def load(self, snapshot_id: str) -> dict:
        """加载指定快照的成绩数据"""
        snapshot_id_int = int(snapshot_id)
        from maimai_sync.db.engine import get_session_factory
        from maimai_sync.db.crud import db_get_snapshot_by_id, db_get_scores_by_snapshot

        async with get_session_factory()() as session:
            snapshot = await db_get_snapshot_by_id(session, snapshot_id_int)
            if not snapshot:
                raise FileNotFoundError(f"快照不存在: {snapshot_id}")

            records = await db_get_scores_by_snapshot(session, snapshot_id_int)

            return {
                "timestamp": to_utc_iso(snapshot.snapshot_time),
                "username": self._username,
                "rating": snapshot.rating,
                "score_count": snapshot.score_count,
                "credentials": snapshot.credentials,
                "scores_raw": [r.to_dict() for r in records],
                "scores_upload": json.loads(snapshot.upload_data_json) if snapshot.upload_data_json else [],
            }

    async def load_latest(self) -> dict:
        """加载最新快照"""
        from maimai_sync.db.engine import get_session_factory
        from maimai_sync.db.crud import db_get_user_by_username, db_get_latest_snapshot, db_get_scores_by_snapshot

        async with get_session_factory()() as session:
            user = await db_get_user_by_username(session, self._username)
            if not user:
                raise FileNotFoundError(f"用户 '{self._username}' 不存在")

            snapshot = await db_get_latest_snapshot(session, user.id)
            if not snapshot:
                raise FileNotFoundError("没有找到成绩快照")

            records = await db_get_scores_by_snapshot(session, snapshot.id)

            return {
                "timestamp": to_utc_iso(snapshot.snapshot_time),
                "username": self._username,
                "rating": snapshot.rating,
                "score_count": snapshot.score_count,
                "credentials": snapshot.credentials,
                "scores_raw": [r.to_dict() for r in records],
                "scores_upload": json.loads(snapshot.upload_data_json) if snapshot.upload_data_json else [],
            }

    async def list_files(self) -> list[dict]:
        """列出所有快照摘要（兼容旧接口）"""
        from maimai_sync.db.engine import get_session_factory
        from maimai_sync.db.crud import db_get_user_by_username, db_get_snapshots

        async with get_session_factory()() as session:
            user = await db_get_user_by_username(session, self._username)
            if not user:
                return []

            snapshots = await db_get_snapshots(session, user.id)
            return [
                {
                    "filename": f"snapshot_{s.id}",
                    "filepath": str(s.id),
                    "timestamp": to_utc_iso(s.snapshot_time),
                    "username": self._username,
                    "rating": s.rating,
                    "score_count": s.score_count,
                    "has_upload_data": s.upload_data_json is not None,
                }
                for s in snapshots
            ]
