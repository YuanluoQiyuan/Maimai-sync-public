"""
maimai-sync - 舞萌DX 机台取分同步工具

流程：二维码取分 → 存本地 → 传水鱼
支持多用户账号系统，每个用户独立存储成绩和配置。
支持 MySQL 数据库存储（通过 MAIMAI_DB_URL 环境变量启用）。
"""

from maimai_sync.account import AccountManager, UserProfile
from maimai_sync.storage import JsonScoreStorage
from maimai_sync.uploader import DivingFishUploader

__version__ = "0.3.0"
__all__ = [
    "AccountManager",
    "UserProfile",
    "JsonScoreStorage",
    "DivingFishUploader",
]
