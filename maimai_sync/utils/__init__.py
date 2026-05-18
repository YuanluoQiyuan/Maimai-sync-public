"""工具函数模块"""

from datetime import datetime


def to_utc_iso(dt: datetime | None) -> str | None:
    """将 UTC datetime 转为 ISO 字符串，追加 Z 后缀表示 UTC"""
    if dt is None:
        return None
    return dt.isoformat() + "Z"
