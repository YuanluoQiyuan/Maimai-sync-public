"""歌曲版本与成绩合并工具函数

从 app.py 提取，供 db_store.py 和 web.app 共同使用，避免循环导入。
"""

import json
import logging
import math
import time
from typing import Optional

from maimai_sync.config import DEFAULT_DATA_DIR

logger = logging.getLogger(__name__)

# ============================================================
# 歌曲版本数据库（从水鱼查分器获取，本地缓存）
# ============================================================

# (id_map, title_map, chart_constants, level_labels)
# chart_constants: {(song_id, song_type, level_index): constant}
# level_labels: {(song_id, song_type, level_index): display_level_str (e.g. "13", "13+")}
_SONG_VERSION_CACHE: tuple[dict, dict, dict, dict, dict] = ({}, {}, {}, {}, {})
_CACHE_LOADED_AT: float = 0.0
_CACHE_TTL = 3600 * 6  # 6 小时缓存
_LOCAL_DB_PATH = DEFAULT_DATA_DIR / ".music_db.json"

def load_song_version_db() -> tuple[dict, dict, dict, dict]:
    """加载歌曲版本数据库，返回 (id_map, title_map, chart_constants, level_labels, max_dx_scores)"""
    global _SONG_VERSION_CACHE, _CACHE_LOADED_AT

    if _SONG_VERSION_CACHE[0] and (time.time() - _CACHE_LOADED_AT < _CACHE_TTL):
        return _SONG_VERSION_CACHE

    if _LOCAL_DB_PATH.exists():
        try:
            with open(_LOCAL_DB_PATH, "r", encoding="utf-8") as f:
                local_data = json.load(f)
            if local_data.get("_fetched_at", 0) + _CACHE_TTL > time.time():
                _SONG_VERSION_CACHE = parse_music_db(local_data.get("songs", []))
                _CACHE_LOADED_AT = time.time()
                return _SONG_VERSION_CACHE
        except Exception:
            pass

    try:
        import urllib.request
        url = "https://www.diving-fish.com/api/maimaidxprober/music_data"
        req = urllib.request.Request(url, headers={"User-Agent": "maimai-sync/0.3"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw_data = json.loads(resp.read())

        _LOCAL_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_LOCAL_DB_PATH, "w", encoding="utf-8") as f:
            json.dump({"_fetched_at": time.time(), "songs": raw_data}, f, ensure_ascii=False)

        _SONG_VERSION_CACHE = parse_music_db(raw_data)
        _CACHE_LOADED_AT = time.time()
    except Exception as e:
        logger.warning(f"获取水鱼歌曲数据库失败: {e}")
        if not _SONG_VERSION_CACHE[0] and _LOCAL_DB_PATH.exists():
            try:
                with open(_LOCAL_DB_PATH, "r", encoding="utf-8") as f:
                    local_data = json.load(f)
                _SONG_VERSION_CACHE = parse_music_db(local_data.get("songs", []))
                _CACHE_LOADED_AT = time.time()
            except Exception:
                pass

    return _SONG_VERSION_CACHE


def parse_music_db(songs: list) -> tuple[dict, dict, dict, dict]:
    """解析水鱼歌曲数据，返回 (id_map, title_map, chart_constants, level_labels, max_dx_scores)

    chart_constants: {(song_id_str, song_type, level_index): constant_float}
    level_labels: {(song_id_str, song_type, level_index): display_level_str (e.g. "13", "13+")}
    max_dx_scores: {(song_id_str, song_type, level_index): max_dx_score_int}
    """
    # DX Score: 每个 note 最高 3 分（大P=3, 小P=2, Great=1），最大 DX Score = 总物量 × 3
    # SD songs: [tap, hold, slide, break] (4 elements)
    # DX songs: [tap, hold, slide, touch, break] (5 elements)

    id_map = {}
    title_map = {}
    chart_constants = {}
    level_labels = {}
    max_dx_scores = {}

    for song in songs:
        sid = str(song.get("id", ""))
        stype = song.get("type", "")
        title = song.get("title", "") or song.get("basic_info", {}).get("title", "")
        is_new = song.get("basic_info", {}).get("is_new", False)
        normalized_type = {"SD": "standard", "DX": "dx"}.get(stype, stype.lower())

        id_map[(sid, normalized_type)] = is_new

        # 从 ds 数组提取谱面定数（ds[i] 对应 level_index=i: Basic/Advanced/Expert/Master/Re:Master）
        ds_list = song.get("ds", [])
        level_list = song.get("level", [])

        charts_list = song.get("charts", [])

        def _store_charts(target_sid):
            if isinstance(ds_list, list):
                for level_index, constant in enumerate(ds_list):
                    if level_index > 4:
                        break
                    if isinstance(constant, (int, float)) and constant > 0:
                        chart_constants[(target_sid, normalized_type, level_index)] = float(constant)
            if isinstance(level_list, list):
                for level_index, lv in enumerate(level_list):
                    if level_index > 4:
                        break
                    if lv:
                        level_labels[(target_sid, normalized_type, level_index)] = str(lv)
            if isinstance(charts_list, list):
                for level_index, chart in enumerate(charts_list):
                    if level_index > 4:
                        break
                    notes = chart.get("notes", [])
                    if isinstance(notes, list) and len(notes) >= 4:
                        total_notes = sum(notes)
                        max_dx = total_notes * 3
                        if max_dx > 0:
                            max_dx_scores[(target_sid, normalized_type, level_index)] = max_dx

        _store_charts(sid)

        # DX 歌曲也建立原始 ID 的映射
        if stype == "DX":
            try:
                raw_id = int(sid) - 10000
                if raw_id > 0:
                    raw_sid = str(raw_id)
                    id_map[(raw_sid, normalized_type)] = is_new
                    _store_charts(raw_sid)
            except ValueError:
                pass

        if title and (title not in title_map or is_new):
            title_map[title] = is_new

    return id_map, title_map, chart_constants, level_labels, max_dx_scores


def is_new_song(song_id: int, song_type: str, song_title: str = "") -> Optional[bool]:
    """判断是否当前版本新曲"""
    id_map, title_map, _, _, _ = load_song_version_db()
    stype = (song_type or "").lower()

    result = id_map.get((str(song_id), stype))
    if result is not None:
        return result

    if stype == "dx":
        result = id_map.get((str(song_id + 10000), stype))
        if result is not None:
            return result

    if song_title:
        result = title_map.get(song_title)
        if result is not None:
            return result

    return False


# ============================================================
# DX Rating 计算
# ============================================================

# 达成率区间 → 系数映射
_ACHIEVEMENT_COEFF = [
    (100.5, 22.4),       # SSS+
    (100.4999, 22.2),    # SSS (high)
    (100.0, 21.6),       # SSS
    (99.9999, 21.4),     # SS+ (high)
    (99.5, 21.1),        # SS+
    (99.0, 20.8),        # SS
    (98.9999, 20.6),     # S+ (high)
    (98.0, 20.3),        # S+
    (97.0, 20.0),        # S
    (96.9999, 17.6),     # S (low)
    (94.0, 16.8),        # AAA
    (90.0, 15.2),        # AA
    (80.0, 13.6),        # A
    (79.9999, 12.8),     # BBB (high)
    (75.0, 12.0),        # BBB
    (70.0, 11.2),        # BB
    (60.0, 9.6),         # B
    (50.0, 8.0),         # C
    (40.0, 6.4),         # D
    (30.0, 4.8),         # DD
    (20.0, 3.2),         # DDD
    (10.0, 1.6),         # DDDD
]


def compute_dx_rating(achievement: float, constant: float) -> float:
    """根据达成率和谱面定数计算单曲 DX Rating
    公式与 diving-fish.com 一致: floor(coeff * constant * min(100.5, achievement) / 100)
    """
    if constant <= 0 or achievement <= 0:
        return 0.0
    for threshold, coeff in _ACHIEVEMENT_COEFF:
        if achievement >= threshold:
            ach_capped = min(100.5, achievement)
            return math.floor(coeff * constant * ach_capped / 100.0)
    return 0.0


def lookup_chart_constant(song_id, song_type: str, level_index: int) -> float:
    """查找谱面定数"""
    _, _, constants, _, _ = load_song_version_db()
    stype = (song_type or "").lower()
    sid = str(song_id)
    result = constants.get((sid, stype, level_index), 0.0)
    if result <= 0 and stype == "dx":
        # 尝试另一种 ID 格式：DX 歌曲 ID 可能是 raw_id 或 raw_id+10000
        sid_int = int(song_id)
        try_alt = sid_int - 10000 if sid_int >= 10000 else sid_int + 10000
        if try_alt > 0:
            result = constants.get((str(try_alt), stype, level_index), 0.0)
    return result


def lookup_chart_level(song_id, song_type: str, level_index: int) -> str:
    """查找谱面定数显示值，返回精确定数如 "13.8"（不含难度前缀）"""
    constant = lookup_chart_constant(song_id, song_type, level_index)
    if constant > 0:
        return f"{constant:.1f}"
    return ""


def lookup_max_dx_score(song_id, song_type: str, level_index: int) -> int:
    """查找谱面最大 DX Score"""
    _, _, _, _, max_dx_scores = load_song_version_db()
    stype = (song_type or "").lower()
    sid = str(song_id)
    result = max_dx_scores.get((sid, stype, level_index), 0)
    if result <= 0 and stype == "dx":
        sid_int = int(song_id)
        try_alt = sid_int - 10000 if sid_int >= 10000 else sid_int + 10000
        if try_alt > 0:
            result = max_dx_scores.get((str(try_alt), stype, level_index), 0)
    return result


def compute_rate(achievement: float) -> int:
    """根据达成率计算评价等级 0-13（maimai DX 官方评级）"""
    if achievement >= 100.5: return 0   # SSS+
    if achievement >= 100.0: return 1   # SSS
    if achievement >= 99.5:  return 2   # SS+
    if achievement >= 99.0:  return 3   # SS
    if achievement >= 98.0:  return 4   # S+
    if achievement >= 97.0:  return 5   # S
    if achievement >= 94.0:  return 6   # AAA
    if achievement >= 90.0:  return 7   # AA
    if achievement >= 80.0:  return 8   # A
    if achievement >= 75.0:  return 9   # BBB
    if achievement >= 70.0:  return 10  # BB
    if achievement >= 60.0:  return 11  # B
    if achievement >= 50.0:  return 12  # C
    return 13  # D


def compute_dx_star(dx_score: int, max_dx_score: int) -> int:
    """根据 DX Score 和最大 DX Score 计算星星数 0-5"""
    if max_dx_score <= 0 or dx_score <= 0:
        return 0
    ratio = dx_score / max_dx_score
    if ratio >= 0.97: return 5
    if ratio >= 0.95: return 4
    if ratio >= 0.93: return 3
    if ratio >= 0.90: return 2
    if ratio >= 0.85: return 1
    return 0


# ============================================================
# 成绩数据合并
# ============================================================

def merge_score_data(data: dict) -> list[dict]:
    """合并 scores_raw 和 scores_upload，生成带标题、is_new、dx_rating 的成绩列表"""
    raw = data.get("scores_raw", [])
    upload = data.get("scores_upload", [])

    # 按 (song_id, level_index) 索引 upload 数据，因为 upload 可能有跳过项
    upload_map: dict[tuple, dict] = {}
    for u in upload:
        key = (u.get("id", u.get("musicId")), u.get("level_index", u.get("level")))
        if key[0] is not None and key not in upload_map:
            upload_map[key] = u

    results = []
    rated_count = 0
    total_count = 0

    for r in raw:
        entry = {**r}
        total_count += 1

        if r.get("level_index") == 10:
            entry["title"] = f"[宴]ID:{r['id']}"
            entry["level"] = "[宴]"
            entry["is_new"] = False
            results.append(entry)
            continue

        key = (r.get("id"), r.get("level_index"))
        u = upload_map.get(key)
        if u:
            entry["title"] = u.get("title", f"ID:{r['id']}")
        else:
            entry["title"] = f"ID:{r['id']}"

        is_new = is_new_song(r.get("id"), r.get("type", ""), entry.get("title", ""))
        entry["is_new"] = is_new if is_new is not None else False

        # 计算 DX Rating
        constant = lookup_chart_constant(r.get("id"), r.get("type", ""), r.get("level_index", 0))
        if constant > 0:
            entry["dx_rating"] = compute_dx_rating(
                entry.get("achievements", 0), constant
            )
            rated_count += 1

        # 计算评价和 DX 星
        entry["rate"] = compute_rate(entry.get("achievements", 0))
        max_dx = lookup_max_dx_score(r.get("id"), r.get("type", ""), r.get("level_index", 0))
        entry["dx_star"] = compute_dx_star(entry.get("dx_score", 0), max_dx)

        # 难度定数显示
        chart_level = lookup_chart_level(r.get("id"), r.get("type", ""), r.get("level_index", 0))
        if chart_level:
            entry["level"] = chart_level

        results.append(entry)

    if total_count > 0:
        logger.info("merge_score_data: %d/%d 条成绩计算出 DX Rating, %d 条无谱面定数",
                     rated_count, total_count, total_count - rated_count)

    return results
