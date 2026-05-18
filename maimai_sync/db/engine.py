"""数据库引擎和会话管理

支持 MySQL (aiomysql) 和 SQLite (aiosqlite)。
通过环境变量 MAIMAI_DB_URL 配置：
  MySQL:   mysql+aiomysql://user:pass@localhost:3306/maimai_sync
  SQLite:  sqlite+aiosqlite:///./maimai_data/maimai.db

未设置时默认使用 SQLite（本机开发零配置）。
"""

import logging
import os
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession, AsyncEngine, create_async_engine, async_sessionmaker

from maimai_sync.db.models import Base

logger = logging.getLogger(__name__)

# 全局引擎和会话工厂
_engine: Optional[AsyncEngine] = None
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def _get_db_url() -> str:
    """获取数据库连接 URL"""
    url = os.environ.get("MAIMAI_DB_URL", "")
    if url:
        return url
    # 默认 SQLite（本机开发零配置）
    from maimai_sync.config import DEFAULT_DATA_DIR
    db_path = DEFAULT_DATA_DIR / "maimai.db"
    DEFAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)
    return f"sqlite+aiosqlite:///{db_path}"


def get_engine() -> AsyncEngine:
    """获取异步引擎（单例）"""
    global _engine
    if _engine is None:
        url = _get_db_url()
        logger.info(f"数据库连接: {_mask_url(url)}")
        # MySQL 需要连接池，SQLite 不需要
        connect_args = {}
        if url.startswith("sqlite"):
            connect_args = {"check_same_thread": False}

        _engine = create_async_engine(
            url,
            echo=False,
            pool_pre_ping=True,
            pool_size=5 if "mysql" in url else 0,
            max_overflow=10 if "mysql" in url else 0,
            connect_args=connect_args,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """获取会话工厂（单例）
    
    返回 async_sessionmaker 实例，使用时需要调用：
        factory = get_session_factory()
        async with factory() as session:
            ...
    """
    global _session_factory
    if _session_factory is None:
        engine = get_engine()
        _session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


async def init_db():
    """初始化数据库（建表）"""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("数据库表初始化完成")


async def close_db():
    """关闭数据库连接"""
    global _engine, _session_factory
    if _engine:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("数据库连接已关闭")


def _mask_url(url: str) -> str:
    """隐藏 URL 中的密码"""
    import re
    return re.sub(r'://([^:]+):([^@]+)@', r'://\1:****@', url)
