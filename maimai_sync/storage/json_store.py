"""JSON 本地成绩存储

按用户隔离存储成绩数据，每个用户有独立的数据目录。
兼容无账号模式（数据存根目录）。
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from maimai_sync.models import MaimaiScores, PlayerIdentifier
from maimai_sync.config import DEFAULT_DATA_DIR
from maimai_sync.utils import to_utc_iso

logger = logging.getLogger(__name__)


def _json_safe(val):
    """确保值可 JSON 序列化（处理枚举等类型）"""
    if val is None:
        return None
    if isinstance(val, (str, int, float, bool)):
        return val
    if hasattr(val, "value"):
        return val.value
    return str(val)


_LEVEL_LABELS = ["Basic", "Advanced", "Expert", "Master", "Re:MASTER"]


def _score_to_dict(score) -> dict:
    """将成绩对象转为可序列化的字典

    兼容两种模型：
    - RawScore（新架构，字段: music_id/level/achievement/dx_score/fc_status/fs_status）
    - 旧 maimai-py 的 ScoreExtend（字段: id/level/level_index/achievements/dx_score/...）
    """
    # 检测是否为 RawScore 类型（有 music_id 字段）
    if hasattr(score, "music_id"):
        music_id = _json_safe(score.music_id) or 0
        level_index = _json_safe(score.level) or 0
        return {
            "id": music_id,
            "level": _LEVEL_LABELS[level_index] if 0 <= level_index < 5 else str(level_index),
            "level_index": level_index,
            "achievements": (_json_safe(score.achievement) or 0) / 10000.0,
            "dx_score": _json_safe(score.dx_score),
            "dx_rating": None,
            "dx_star": None,
            "fc": _json_safe(score.fc_status),
            "fs": _json_safe(score.fs_status),
            "rate": None,
            "type": "dx" if int(music_id) >= 10000 else "standard",
        }
    # 旧格式 ScoreExtend
    return {
        "id": _json_safe(getattr(score, "id", None)),
        "level": _json_safe(getattr(score, "level", None)),
        "level_index": _json_safe(getattr(score, "level_index", None)),
        "achievements": _json_safe(getattr(score, "achievements", None)),
        "dx_score": _json_safe(getattr(score, "dx_score", None)),
        "dx_rating": _json_safe(getattr(score, "dx_rating", None)),
        "dx_star": _json_safe(getattr(score, "dx_star", None)),
        "fc": _json_safe(getattr(score, "fc", None)),
        "fs": _json_safe(getattr(score, "fs", None)),
        "rate": _json_safe(getattr(score, "rate", None)),
        "type": _json_safe(getattr(score, "type", None)),
    }


class JsonScoreStorage:
    """JSON 文件成绩存储

    支持按用户隔离存储。传入 username 时，数据存入
    {data_dir}/users/{username}/ 目录；不传则存入 {data_dir}/ 根目录
    （兼容旧模式）。

    Usage::

        # 有账号模式
        storage = JsonScoreStorage(username="alice")
        filepath = await storage.save(identifier, scores, upload_data)

        # 无账号模式（兼容）
        storage = JsonScoreStorage()
        filepath = await storage.save(identifier, scores, upload_data)
    """

    def __init__(self, data_dir: Optional[Path] = None, username: Optional[str] = None):
        """
        Args:
            data_dir: 数据存储根目录，默认为 ./maimai_data
            username: 用户名，传入后成绩存入用户专属目录
        """
        self._root_dir = data_dir or DEFAULT_DATA_DIR
        self._username = username

        # 确定实际存储目录
        if username:
            self.data_dir = self._root_dir / "users" / username
        else:
            self.data_dir = self._root_dir

        self.data_dir.mkdir(parents=True, exist_ok=True)

    @property
    def username(self) -> Optional[str]:
        return self._username

    async def save(
        self,
        identifier: PlayerIdentifier,
        scores: MaimaiScores,
        upload_data: list[dict],
    ) -> Path:
        """保存成绩到本地 JSON 文件

        Args:
            identifier: 玩家标识（含加密 credentials）
            scores: 原始成绩对象
            upload_data: 已序列化为水鱼上传格式的成绩列表

        Returns:
            保存的文件路径
        """
        timestamp = datetime.now()
        filename = f"scores_{timestamp.strftime('%Y%m%d_%H%M%S')}.json"

        data = {
            "timestamp": to_utc_iso(timestamp),
            "username": self._username,
            "rating": scores.rating,
            "score_count": len(scores.scores),
            "scores_raw": [_score_to_dict(s) for s in scores.scores],
            "scores_upload": upload_data,
        }

        filepath = self.data_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info("成绩已保存: %s (原始%d条, 可上传%d条)",
                     filepath, len(data["scores_raw"]), len(upload_data))
        return filepath

    async def load(self, filepath: str) -> dict:
        """加载指定缓存文件

        Args:
            filepath: JSON 文件路径

        Returns:
            成绩数据字典
        """
        path = Path(filepath)
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    async def load_latest(self) -> dict:
        """加载最新的缓存文件

        Returns:
            成绩数据字典

        Raises:
            FileNotFoundError: 没有找到缓存文件
        """
        json_files = sorted(self.data_dir.glob("scores_*.json"), reverse=True)
        if not json_files:
            raise FileNotFoundError("没有找到本地缓存文件")
        return await self.load(str(json_files[0]))

    async def list_files(self) -> list[dict]:
        """列出所有缓存文件的摘要信息

        Returns:
            包含各文件摘要的字典列表
        """
        json_files = sorted(self.data_dir.glob("scores_*.json"), reverse=True)
        result = []
        for f in json_files:
            try:
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                result.append({
                    "filename": f.name,
                    "filepath": str(f),
                    "timestamp": data.get("timestamp"),
                    "username": data.get("username"),
                    "rating": data.get("rating"),
                    "score_count": data.get("score_count"),
                    "has_upload_data": bool(data.get("scores_upload")),
                })
            except Exception:
                result.append({
                    "filename": f.name,
                    "filepath": str(f),
                    "error": True,
                })
        return result
