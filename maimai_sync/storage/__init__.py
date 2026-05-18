"""存储模块

支持两种后端：
  - JsonScoreStorage: JSON 文件存储（默认，兼容旧模式）
  - DbScoreStorage: MySQL 数据库存储（通过 MAIMAI_DB_URL 启用）

AccountManager 和 Web API 会自动选择后端。
"""

from maimai_sync.storage.json_store import JsonScoreStorage

__all__ = ["JsonScoreStorage"]


def get_storage(username: str | None = None):
    """自动选择存储后端（默认数据库）"""
    from maimai_sync.storage.db_store import DbScoreStorage
    return DbScoreStorage(username=username)
