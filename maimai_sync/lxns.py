"""LXNS (落雪) 查分器 API 客户端"""
import logging
import time
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

LXNS_BASE = "https://maimai.lxns.net/api/v0"

# 全局缓存
_song_cache: Optional[dict] = None  # {song_id: song_data}
_alias_cache: Optional[dict] = None  # {song_id: [aliases]}
_cache_ts: float = 0
_CACHE_TTL = 3600 * 6


async def fetch_song(song_id: int) -> Optional[dict]:
    """获取单首歌曲详情（含定数、拟合、谱师等）—— 公共接口，无需鉴权"""
    url = f"{LXNS_BASE}/maimai/song/{song_id}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    return await resp.json()
                logger.warning("LXNS song/%s 返回 %s", song_id, resp.status)
    except Exception as e:
        logger.warning("LXNS song/%s 请求失败: %s", song_id, e)
    return None


async def fetch_all_songs() -> Optional[dict]:
    """获取全部歌曲列表（缓存 6 小时）"""
    global _song_cache, _cache_ts
    now = time.time()
    if _song_cache is not None and (now - _cache_ts) < _CACHE_TTL:
        return _song_cache

    url = f"{LXNS_BASE}/maimai/song/list"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    songs = data.get("songs", [])
                    _song_cache = {str(s["id"]): s for s in songs}
                    _cache_ts = now
                    logger.info("LXNS 歌曲列表已更新，共 %d 首", len(_song_cache))
                    return _song_cache
    except Exception as e:
        logger.warning("LXNS song/list 请求失败: %s", e)
    return _song_cache or {}


async def fetch_aliases() -> Optional[dict]:
    """获取曲目别名列表（缓存 6 小时）"""
    global _alias_cache, _cache_ts
    # 别名单独用 _cache_ts，简单处理
    if _alias_cache is not None and (time.time() - _cache_ts) < _CACHE_TTL:
        return _alias_cache

    url = f"{LXNS_BASE}/maimai/alias/list"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    aliases_list = data.get("aliases", [])
                    _alias_cache = {str(a["song_id"]): a.get("aliases", []) for a in aliases_list}
                    logger.info("LXNS 别名列表已更新，共 %d 首", len(_alias_cache))
                    return _alias_cache
    except Exception as e:
        logger.warning("LXNS alias/list 请求失败: %s", e)
    return _alias_cache or {}
