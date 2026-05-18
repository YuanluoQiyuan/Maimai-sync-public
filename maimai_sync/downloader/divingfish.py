"""水鱼查分器成绩下载"""

import logging
import time

import httpx

from maimai_sync.config import DIVINGFISH_DOWNLOAD_URL, DEFAULT_TIMEOUT

logger = logging.getLogger(__name__)

_LEVEL_LABELS = ["Basic", "Advanced", "Expert", "Master", "Re:MASTER"]


class DivingFishDownloader:
    """从水鱼查分器下载成绩

    通过 Import Token 认证，从水鱼 API 拉取已上传的成绩记录。

    Usage::

        downloader = DivingFishDownloader()
        result = await downloader.download("your_import_token")
    """

    def __init__(self, timeout: int = DEFAULT_TIMEOUT):
        self.timeout = timeout

    async def download(self, import_token: str) -> dict | None:
        """从水鱼下载成绩记录

        Args:
            import_token: 水鱼 Import Token

        Returns:
            {"records": [...], "rating": N} 或 None（失败时）

        Raises:
            ValueError: Token 无效或无权访问
        """
        logger.info("从水鱼下载成绩, token=%s...", import_token[:8])

        headers = {"Import-Token": import_token}

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(
                DIVINGFISH_DOWNLOAD_URL,
                headers=headers,
            )

        if resp.status_code == 200:
            data = resp.json()
            logger.info("下载成功: %d 条记录", len(data.get("records", [])))
            return data
        elif resp.status_code in (401, 403):
            logger.error("下载失败: HTTP %d - Token 无效或无权访问", resp.status_code)
            raise ValueError("Import Token 无效或无权访问")
        else:
            logger.error("下载失败: HTTP %d - %s", resp.status_code, resp.text[:200])
            return None


def _convert_df_records_to_raw(df_records: list[dict]) -> list[dict]:
    """将水鱼格式的成绩转为 raw score dict 格式

    水鱼实际字段: song_id, title, type(SD/DX), level_index, level, level_label,
                  achievements(float), dxScore, ds, fc, fs, rate, ra
    raw 字段: id, level, level_index, achievements, dx_score, dx_rating, dx_star, fc, fs, rate, type
    """
    raw_list = []
    for r in df_records:
        level_index = r.get("level_index", 0)
        if not isinstance(level_index, int) or level_index < 0 or level_index > 4:
            level_index = 0

        song_type = r.get("type", "")
        type_mapped = {"SD": "standard", "DX": "dx"}.get(song_type, song_type.lower())

        raw_list.append({
            "id": r.get("song_id", 0),
            "level": _LEVEL_LABELS[level_index] if 0 <= level_index < 5 else str(level_index),
            "level_index": level_index,
            "achievements": float(r.get("achievements", 0) or 0),
            "dx_score": int(r.get("dxScore", 0) or 0),
            "dx_rating": 0,
            "dx_star": 0,
            "fc": r.get("fc", ""),
            "fs": r.get("fs", ""),
            "rate": "",
            "type": type_mapped,
        })
    return raw_list


async def save_downloaded_scores(
    username: str, records: list[dict], rating: int
) -> bool:
    """将下载的成绩保存到数据库

    直接调用 CRUD 层，复用 merge_score_data() 计算 DX Rating 等字段。

    Args:
        username: 用户名
        records: 水鱼下载的原始成绩列表
        rating: 总 Rating

    Returns:
        是否保存成功
    """
    import traceback as _tb
    from maimai_sync.db.engine import get_session_factory
    from maimai_sync.db.crud import (
        db_get_user_by_username,
        db_save_snapshot,
        db_save_score_records,
        db_get_latest_snapshot,
    )
    from maimai_sync.utils.score_merge import merge_score_data

    try:
        async with get_session_factory()() as session:
            async with session.begin():
                user = await db_get_user_by_username(session, username)
                if not user:
                    # JSON 模式注册的用户可能不在 DB 中，尝试同步过来
                    from maimai_sync.account.manager import AccountManager
                    from maimai_sync.db.models import User as DbUser
                    json_mgr = AccountManager()
                    json_profile = await json_mgr.get_user(username)
                    if json_profile:
                        user = DbUser(
                            username=json_profile.username,
                            password_hash=json_profile.password_hash,
                            df_import_token=json_profile.df_import_token,
                            nickname=json_profile.nickname,
                            note=json_profile.note,
                            is_admin=json_profile.is_admin,
                        )
                        session.add(user)
                        await session.flush()
                        logger.info("用户 '%s' 已从 JSON 同步到数据库", username)
                    else:
                        logger.error("用户 '%s' 不存在", username)
                        return False

                latest = await db_get_latest_snapshot(session, user.id)
                if latest:
                    delta = time.time() - latest.snapshot_time.timestamp()
                    if (
                        delta < 300
                        and latest.rating == rating
                        and latest.score_count == len(records)
                    ):
                        logger.warning(
                            "跳过重复下载: 5分钟内已有相同快照 (rating=%d, count=%d)",
                            rating, len(records),
                        )
                        return False

                raw_dicts = _convert_df_records_to_raw(records)

                # 规范化 upload 记录: merge_score_data 用 id/musicId 做 key，
                # 水鱼 API 返回的是 song_id，需要补充 id 字段
                norm_records = []
                for r in records:
                    nr = dict(r)
                    if "id" not in nr and "song_id" in nr:
                        nr["id"] = nr["song_id"]
                    norm_records.append(nr)

                temp_data = {
                    "scores_raw": raw_dicts,
                    "scores_upload": norm_records,
                }
                merged = merge_score_data(temp_data)

                snapshot = await db_save_snapshot(
                    session,
                    user_id=user.id,
                    rating=rating,
                    score_count=len(records),
                    upload_data=records,
                )

                count = await db_save_score_records(
                    session,
                    snapshot_id=snapshot.id,
                    user_id=user.id,
                    scores=merged,
                )

                logger.info(
                    "下载成绩已保存: 用户=%s, 快照=%d, %d条记录",
                    username, snapshot.id, count,
                )
                return True
    except Exception:
        logger.error("保存下载成绩失败:\n%s", _tb.format_exc())
        return False
