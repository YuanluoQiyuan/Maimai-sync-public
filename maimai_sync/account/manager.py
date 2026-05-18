"""账号管理

管理用户账号的增删改查、登录验证、用户配置存储。
支持两种后端：
  - JSON 文件（默认，兼容旧模式）
  - MySQL 数据库（通过 MAIMAI_DB_URL 环境变量启用）
"""

import json
import logging
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional
import asyncio

from maimai_sync.account.auth import hash_password, verify_password
from maimai_sync.config import DEFAULT_DATA_DIR, ACCOUNTS_FILENAME

logger = logging.getLogger(__name__)


@dataclass
class UserProfile:
    """用户配置（JSON 后端使用，MySQL 后端直接用 ORM 模型）"""

    username: str
    password_hash: str
    # 水鱼 Import Token（用于传分）
    df_import_token: Optional[str] = None
    # 昵称（排行榜显示用）
    nickname: Optional[str] = None
    # 管理员标记
    is_admin: bool = False
    # 备注
    note: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "UserProfile":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    @classmethod
    def from_db_user(cls, user) -> "UserProfile":
        """从 ORM User 模型转换"""
        return cls(
            username=user.username,
            password_hash=user.password_hash,
            df_import_token=user.df_import_token,
            nickname=user.nickname,
            is_admin=user.is_admin,
            note=user.note,
        )


def _use_db() -> bool:
    """判断是否使用数据库后端（始终启用，SQLite 为默认）"""
    return True


class AccountManager:
    """账号管理器

    自动检测运行环境：
    - 设置了 MAIMAI_DB_URL → 使用数据库
    - 未设置 → 使用 JSON 文件（兼容旧模式）
    """

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = data_dir or DEFAULT_DATA_DIR
        self._accounts_path = self.data_dir / ACCOUNTS_FILENAME
        self._accounts: dict[str, UserProfile] = {}
        # 非 DB 模式才加载 JSON
        if not _use_db():
            self._load()

    def _load(self):
        """从磁盘加载账号数据（JSON 模式）"""
        if self._accounts_path.exists():
            try:
                with open(self._accounts_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                self._accounts = {
                    name: UserProfile.from_dict(data)
                    for name, data in raw.items()
                }
                logger.info("已加载 %d 个账号", len(self._accounts))
            except Exception as e:
                logger.error("加载账号数据失败: %s", e)
                self._accounts = {}

    def _save(self):
        """保存账号数据到磁盘（JSON 模式）"""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        raw = {name: profile.to_dict() for name, profile in self._accounts.items()}
        with open(self._accounts_path, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
        logger.info("账号数据已保存")

    # ============================================================
    # 通用接口（自动路由到 JSON 或 DB 后端）
    # DB 后端方法为 async，需要 await 调用
    # ============================================================

    async def register(self, username: str, password: str) -> UserProfile:
        """注册新用户"""
        if _use_db():
            return await self._db_register(username, password)
        return self._json_register(username, password)

    async def login(self, username: str, password: str) -> UserProfile:
        """验证登录"""
        if _use_db():
            return await self._db_login(username, password)
        return self._json_login(username, password)

    async def delete(self, username: str, password: str) -> bool:
        """删除用户（需验证密码）"""
        if _use_db():
            return await self._db_delete(username, password)
        return self._json_delete(username, password)

    async def change_password(self, username: str, old_password: str, new_password: str) -> bool:
        """修改密码"""
        if _use_db():
            return await self._db_change_password(username, old_password, new_password)
        return self._json_change_password(username, old_password, new_password)

    async def get_user(self, username: str) -> Optional[UserProfile]:
        """获取用户配置（不验证密码）"""
        if _use_db():
            return await self._db_get_user(username)
        return self._accounts.get(username)

    async def update_profile(self, username: str, **kwargs) -> UserProfile:
        """更新用户配置字段"""
        if _use_db():
            return await self._db_update_profile(username, **kwargs)
        return self._json_update_profile(username, **kwargs)

    async def list_users(self) -> list[str]:
        """列出所有用户名"""
        if _use_db():
            return await self._db_list_users()
        return list(self._accounts.keys())

    # ============================================================
    # JSON 后端实现
    # ============================================================

    def _json_register(self, username: str, password: str) -> UserProfile:
        if username in self._accounts:
            raise ValueError(f"用户 '{username}' 已存在")
        if not username.strip():
            raise ValueError("用户名不能为空")
        if len(password) < 4:
            raise ValueError("密码至少 4 位")

        profile = UserProfile(
            username=username,
            password_hash=hash_password(password),
        )
        self._accounts[username] = profile
        user_dir = self._get_user_dir(username)
        user_dir.mkdir(parents=True, exist_ok=True)
        self._save()
        return profile

    def _json_login(self, username: str, password: str) -> UserProfile:
        if username not in self._accounts:
            raise ValueError(f"用户 '{username}' 不存在")
        profile = self._accounts[username]
        if not verify_password(password, profile.password_hash):
            raise ValueError("密码错误")
        return profile

    def _json_delete(self, username: str, password: str) -> bool:
        if username not in self._accounts:
            raise ValueError(f"用户 '{username}' 不存在")
        profile = self._accounts[username]
        if not verify_password(password, profile.password_hash):
            raise ValueError("密码错误")
        del self._accounts[username]
        self._save()
        return True

    def _json_change_password(self, username: str, old_password: str, new_password: str) -> bool:
        profile = self._accounts.get(username)
        if not profile:
            raise ValueError(f"用户 '{username}' 不存在")
        if not verify_password(old_password, profile.password_hash):
            raise ValueError("旧密码错误")
        if len(new_password) < 4:
            raise ValueError("新密码至少 4 位")
        profile.password_hash = hash_password(new_password)
        self._save()
        return True

    def _json_update_profile(self, username: str, **kwargs) -> UserProfile:
        profile = self._accounts.get(username)
        if not profile:
            raise ValueError(f"用户 '{username}' 不存在")
        updatable = {"df_import_token", "nickname", "note"}
        for key, value in kwargs.items():
            if key in updatable:
                setattr(profile, key, value)
            elif key == "password_hash":
                raise ValueError("请用 change_password() 修改密码")
            else:
                raise ValueError(f"不可更新字段: {key}")
        self._save()
        return profile

    def _json_list_users(self) -> list[str]:
        return list(self._accounts.keys())

    # ============================================================
    # 数据库后端实现
    # ============================================================

    async def _db_register(self, username: str, password: str) -> UserProfile:
        from maimai_sync.db.engine import get_session_factory
        from maimai_sync.db.crud import db_create_user

        async with get_session_factory()() as session:
            async with session.begin():
                user = await db_create_user(session, username, password)
                return UserProfile.from_db_user(user)

    async def _db_login(self, username: str, password: str) -> UserProfile:
        from maimai_sync.db.engine import get_session_factory
        from maimai_sync.db.crud import db_verify_login

        async with get_session_factory()() as session:
            async with session.begin():
                user = await db_verify_login(session, username, password)
                return UserProfile.from_db_user(user)

    async def _db_delete(self, username: str, password: str) -> bool:
        from maimai_sync.db.engine import get_session_factory
        from maimai_sync.db.crud import db_delete_user

        async with get_session_factory()() as session:
            async with session.begin():
                return await db_delete_user(session, username, password)

    async def _db_change_password(self, username: str, old_password: str, new_password: str) -> bool:
        from maimai_sync.db.engine import get_session_factory
        from maimai_sync.db.crud import db_change_password

        async with get_session_factory()() as session:
            async with session.begin():
                return await db_change_password(session, username, old_password, new_password)

    async def _db_get_user(self, username: str) -> Optional[UserProfile]:
        from maimai_sync.db.engine import get_session_factory
        from maimai_sync.db.crud import db_get_user_by_username

        async with get_session_factory()() as session:
            user = await db_get_user_by_username(session, username)
            return UserProfile.from_db_user(user) if user else None

    async def _db_update_profile(self, username: str, **kwargs) -> UserProfile:
        from maimai_sync.db.engine import get_session_factory
        from maimai_sync.db.crud import db_update_user_profile

        async with get_session_factory()() as session:
            async with session.begin():
                user = await db_update_user_profile(session, username, **kwargs)
                return UserProfile.from_db_user(user)

    async def _db_list_users(self) -> list[str]:
        from maimai_sync.db.engine import get_session_factory
        from maimai_sync.db.crud import db_list_users

        async with get_session_factory()() as session:
            return await db_list_users(session)

    # ============================================================
    # 辅助方法
    # ============================================================

    def _get_user_dir(self, username: str) -> Path:
        """获取用户专属数据目录"""
        return self.data_dir / "users" / username

    def get_user_data_dir(self, username: str) -> Path:
        """获取用户数据目录（公开接口，确保目录存在）"""
        user_dir = self._get_user_dir(username)
        user_dir.mkdir(parents=True, exist_ok=True)
        return user_dir
