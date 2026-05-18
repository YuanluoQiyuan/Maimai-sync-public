"""数据库模块

基于 SQLAlchemy 2.x 异步 ORM，支持 MySQL / SQLite。
通过环境变量 MAIMAI_DB_URL 配置连接串。
"""

from maimai_sync.db.engine import get_engine, get_session_factory, init_db
from maimai_sync.db.models import User, ScoreSnapshot, ScoreRecord

__all__ = [
    "get_engine",
    "get_session_factory",
    "init_db",
    "User",
    "ScoreSnapshot",
    "ScoreRecord",
]
