"""公共数据模型"""

from dataclasses import dataclass, field
from typing import List


@dataclass
class RawScore:
    """机台原始成绩数据封装"""
    music_id: int
    level: int
    achievement: int
    dx_score: int
    fc_status: int
    fs_status: int


@dataclass
class MaimaiScores:
    """成绩集封装"""
    rating: int
    scores: List[RawScore] = field(default_factory=list)


@dataclass
class PlayerIdentifier:
    """玩家身份标识"""
    user_id: int
    token: str
    credentials: str
