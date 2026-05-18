"""FastAPI 后端 — 为前端提供成绩数据 API

用法:
    python -m maimai_sync.web.app
    # 或
    uvicorn maimai_sync.web.app:app --reload --port 8765

支持两种存储后端：
  - JSON 文件（默认）：无 MAIMAI_DB_URL 环境变量时
  - MySQL 数据库：设置 MAIMAI_DB_URL 时自动切换
"""

import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from maimai_sync.account.manager import AccountManager
from maimai_sync.config import DEFAULT_DATA_DIR, ACCOUNTS_FILENAME
from maimai_sync.utils import to_utc_iso
from maimai_sync.utils.score_merge import (
    load_song_version_db,
    parse_music_db,
    is_new_song,
    merge_score_data,
)

app = FastAPI(title="maimai-sync Web", version="0.3.0")

logger = logging.getLogger("maimai_sync.web")


# ============================================================
# 全局异常处理 — 确保 500 错误也打印堆栈到日志
# ============================================================

import traceback

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """捕获所有未处理异常，打印详细堆栈"""
    # HTTPException 由 FastAPI 自身处理，不走这里
    if isinstance(exc, HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    print(f"!!! UNHANDLED EXCEPTION on {request.method} {request.url.path}:\n{tb}", flush=True)
    logger.error("Unhandled exception on %s %s:\n%s", request.method, request.url.path, tb)
    return JSONResponse(
        status_code=500,
        content={"detail": f"内部错误: {exc}"},
    )

# 账号管理器
_account_mgr: Optional[AccountManager] = None

def _get_account_mgr() -> AccountManager:
    global _account_mgr
    if _account_mgr is None:
        _account_mgr = AccountManager()
    return _account_mgr

# Session 存储: session_id -> username
_sessions: dict[str, float] = {}  # session_id -> 创建时间戳
_session_username: dict[str, str] = {}  # session_id -> username
_SESSION_TTL = 3600 * 24 * 7  # 7 天过期
_COOKIE_NAME = "maimai_session"

# 静态文件目录
_STATIC_DIR = Path(__file__).parent / "static"





def _use_db() -> bool:
    """是否使用数据库后端（始终启用，SQLite 为默认）"""
    return True


# ============================================================
# 启动时初始化数据库
# ============================================================

@app.on_event("startup")
async def startup():
    """应用启动时初始化数据库和日志"""
    # 确保 maimai_sync 子模块的日志能输出到 uvicorn
    import logging
    maimai_logger = logging.getLogger("maimai_sync")
    if not maimai_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s: %(name)s: %(message)s"))
        maimai_logger.addHandler(handler)
    maimai_logger.setLevel(logging.INFO)

    from maimai_sync.db.engine import init_db, get_session_factory
    await init_db()
    logger.info("数据库后端已初始化")

    # 将 JSON 中的用户迁移到数据库
    accounts_path = DEFAULT_DATA_DIR / ACCOUNTS_FILENAME
    if accounts_path.exists():
        import json as _json
        try:
            with open(accounts_path, "r", encoding="utf-8") as f:
                raw = _json.load(f)
        except Exception:
            raw = {}

        if raw:
            from maimai_sync.db.crud import db_get_user_by_username
            from maimai_sync.db.models import User as DbUser
            async with get_session_factory()() as session:
                async with session.begin():
                    for username, data in raw.items():
                        existing = await db_get_user_by_username(session, username)
                        if not existing:
                            user = DbUser(
                                username=data.get("username", username),
                                password_hash=data.get("password_hash", ""),
                                df_import_token=data.get("df_import_token"),
                                nickname=data.get("nickname"),
                                note=data.get("note"),
                                is_admin=data.get("is_admin", False),
                            )
                            session.add(user)
                            logger.info("用户 '%s' 已从 JSON 迁移到数据库", username)
                    await session.flush()
            logger.info("JSON 用户迁移完成")


@app.on_event("shutdown")
async def shutdown():
    """应用关闭时清理"""
    if _use_db():
        from maimai_sync.db.engine import close_db
        await close_db()


# ============================================================
# 歌曲版本数据库 — 已迁移至 maimai_sync.utils.score_merge
# 保留向后兼容别名
# ============================================================

_SONG_VERSION_CACHE: tuple[dict[tuple[str, str], bool], dict[str, bool]] = ({}, {})
_CACHE_LOADED_AT: float = 0.0
_CACHE_TTL = 3600 * 6  # 6 小时缓存
_LOCAL_DB_PATH = DEFAULT_DATA_DIR / ".music_db.json"


# 保留旧名兼容
_load_song_version_db = load_song_version_db
_parse_music_db = parse_music_db
_is_new_song = is_new_song


# ============================================================
# Session 认证
# ============================================================

def _create_session(username: str) -> str:
    session_id = secrets.token_hex(32)
    _sessions[session_id] = time.time()
    _session_username[session_id] = username
    return session_id


def _get_session_user(request: Request) -> Optional[str]:
    session_id = request.cookies.get(_COOKIE_NAME)
    if not session_id:
        return None
    created = _sessions.get(session_id)
    if created is None:
        return None
    if time.time() - created > _SESSION_TTL:
        _sessions.pop(session_id, None)
        _session_username.pop(session_id, None)
        return None
    return _session_username.get(session_id)


def _require_login(request: Request) -> str:
    user = _get_session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    return user


# ============================================================
# 数据合并辅助函数 — 已迁移至 maimai_sync.utils.score_merge
# ============================================================

_merge_score_data = merge_score_data


# ============================================================
# JSON 后端辅助函数
# ============================================================

def _get_data_dir(username: Optional[str] = None) -> Path:
    if username:
        d = DEFAULT_DATA_DIR / "users" / username
    else:
        d = DEFAULT_DATA_DIR
    if not d.exists():
        raise HTTPException(status_code=404, detail=f"数据目录不存在: {d}")
    return d


def _load_score_file(filepath: Path) -> dict:
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


# ============================================================
# 认证 API
# ============================================================

@app.post("/api/auth/login")
async def api_login(request: Request):
    body = await request.json()
    username = body.get("username", "").strip()
    password = body.get("password", "")

    if not username or not password:
        raise HTTPException(status_code=400, detail="用户名和密码不能为空")

    mgr = _get_account_mgr()
    try:
        await mgr.login(username, password)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))

    session_id = _create_session(username)
    marker = DEFAULT_DATA_DIR / ".current_user"
    marker.write_text(username, encoding="utf-8")

    response = JSONResponse({"ok": True, "username": username})
    response.set_cookie(
        key=_COOKIE_NAME,
        value=session_id,
        max_age=_SESSION_TTL,
        httponly=True,
        samesite="lax",
    )
    return response


@app.post("/api/auth/register")
async def api_register(request: Request):
    body = await request.json()
    username = body.get("username", "").strip()
    password = body.get("password", "")

    if not username or not password:
        raise HTTPException(status_code=400, detail="用户名和密码不能为空")
    if len(username) > 32:
        raise HTTPException(status_code=400, detail="用户名不能超过 32 个字符")

    mgr = _get_account_mgr()
    try:
        await mgr.register(username, password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    session_id = _create_session(username)
    marker = DEFAULT_DATA_DIR / ".current_user"
    marker.write_text(username, encoding="utf-8")

    response = JSONResponse({"ok": True, "username": username})
    response.set_cookie(
        key=_COOKIE_NAME,
        value=session_id,
        max_age=_SESSION_TTL,
        httponly=True,
        samesite="lax",
    )
    return response


@app.post("/api/auth/logout")
async def api_logout(request: Request):
    session_id = request.cookies.get(_COOKIE_NAME)
    if session_id:
        _sessions.pop(session_id, None)
        _session_username.pop(session_id, None)

    response = JSONResponse({"ok": True})
    response.delete_cookie(key=_COOKIE_NAME)
    return response


@app.get("/api/auth/me")
async def api_auth_me(request: Request):
    user = _get_session_user(request)
    if user:
        return JSONResponse({"logged_in": True, "username": user})
    return JSONResponse({"logged_in": False, "username": None})


@app.get("/api/auth/profile")
async def api_auth_profile_get(request: Request):
    user = _require_login(request)

    if _use_db():
        return await _db_get_profile(user)

    mgr = _get_account_mgr()
    profile = await mgr.get_user(user)
    if not profile:
        raise HTTPException(status_code=404, detail="用户不存在")

    return JSONResponse({
        "username": profile.username,
        "nickname": profile.nickname,
        "is_admin": profile.is_admin,
        "df_import_token": profile.df_import_token,
        "note": profile.note,
        "has_df_token": profile.df_import_token is not None and len(profile.df_import_token) > 0,
    })


@app.post("/api/auth/profile")
async def api_auth_profile_update(request: Request):
    user = _require_login(request)
    body = await request.json()

    mgr = _get_account_mgr()
    try:
        updates = {}
        for key in ("df_import_token", "nickname", "note"):
            if key in body:
                val = body[key]
                updates[key] = val if val else None

        if not updates:
            raise HTTPException(status_code=400, detail="没有需要更新的字段")

        profile = await mgr.update_profile(user, **updates)
        return JSONResponse({
            "ok": True,
            "username": profile.username,
            "nickname": profile.nickname,
            "has_df_token": profile.df_import_token is not None and len(profile.df_import_token) > 0,
        })
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============================================================
# 数据库后端辅助函数
# ============================================================

async def _db_get_profile(username: str) -> JSONResponse:
    """从数据库获取用户配置"""
    from maimai_sync.db.engine import get_session_factory
    from maimai_sync.db.crud import db_get_user_by_username

    async with get_session_factory()() as session:
        user = await db_get_user_by_username(session, username)
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        return JSONResponse(user.to_profile_dict())


async def _db_get_user_id(username: str) -> int:
    """获取用户 ID"""
    from maimai_sync.db.engine import get_session_factory
    from maimai_sync.db.crud import db_get_user_by_username

    async with get_session_factory()() as session:
        user = await db_get_user_by_username(session, username)
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        return user.id


# ============================================================
# 同步 API（上传 / 下载）
# ============================================================

@app.post("/api/sync/upload")
async def api_sync_upload(request: Request):
    user = _require_login(request)
    body = await request.json()
    token_override = body.get("df_import_token") or None

    mgr = _get_account_mgr()
    profile = await mgr.get_user(user)
    if not profile:
        raise HTTPException(status_code=404, detail="用户不存在")

    import_token = token_override or profile.df_import_token
    if not import_token:
        return JSONResponse({"ok": False, "error": "未配置水鱼 Import Token"}, status_code=400)

    from maimai_sync.storage import get_storage
    from maimai_sync.uploader import DivingFishUploader

    storage = get_storage(username=user)
    uploader = DivingFishUploader()

    try:
        data = await storage.load_latest()
    except FileNotFoundError:
        return JSONResponse({"ok": False, "error": "没有本地缓存，请先取分"}, status_code=400)

    upload_data = data.get("scores_upload", [])
    if not upload_data:
        return JSONResponse({"ok": False, "error": "缓存中没有可上传的成绩"}, status_code=400)

    ok = await uploader.upload(upload_data, import_token)
    if ok:
        return JSONResponse({"ok": True, "uploaded": len(upload_data)})
    else:
        return JSONResponse({"ok": False, "error": "上传失败"}, status_code=500)


@app.post("/api/sync/download")
async def api_sync_download(request: Request):
    """从水鱼下载成绩 → 存本地数据库"""
    user = _require_login(request)
    body = await request.json()
    token_override = body.get("df_import_token") or None

    mgr = _get_account_mgr()
    profile = await mgr.get_user(user)
    if not profile:
        raise HTTPException(status_code=404, detail="用户不存在")

    import_token = token_override or profile.df_import_token
    if not import_token:
        return JSONResponse(
            {"ok": False, "error": "未配置水鱼 Import Token"},
            status_code=400,
        )

    from maimai_sync.downloader import DivingFishDownloader
    from maimai_sync.downloader.divingfish import save_downloaded_scores

    downloader = DivingFishDownloader()

    try:
        result = await downloader.download(import_token)
    except ValueError as e:
        return JSONResponse(
            {"ok": False, "error": str(e)},
            status_code=400,
        )

    if not result:
        return JSONResponse(
            {"ok": False, "error": "从水鱼下载失败（网络错误或 API 异常）"},
            status_code=500,
        )

    records = result.get("records", [])
    rating = result.get("rating", 0)

    if not records:
        return JSONResponse({
            "ok": True,
            "downloaded": 0,
            "rating": rating,
            "message": "水鱼上没有成绩记录",
        })

    saved = await save_downloaded_scores(user, records, rating)
    if saved:
        return JSONResponse({
            "ok": True,
            "downloaded": len(records),
            "rating": rating,
        })
    else:
        return JSONResponse(
            {"ok": False, "error": "保存成绩到数据库失败（可能5分钟内已下载过相同数据）"},
            status_code=500,
        )


# ============================================================
# 数据 API（需认证）— 自动路由 JSON / DB 后端
# ============================================================

@app.get("/api/users")
async def api_users(request: Request):
    _require_login(request)
    mgr = _get_account_mgr()
    users = await mgr.list_users()
    current = _get_session_user(request)
    return JSONResponse({"users": users, "current": current})


@app.get("/api/scores")
async def api_scores(
    request: Request,
    username: Optional[str] = Query(None),
    file: Optional[str] = Query(None),
):
    """获取成绩数据"""
    logged_in = _require_login(request)
    user = username or logged_in
    if not user:
        raise HTTPException(status_code=400, detail="未指定用户")

    if _use_db():
        return await _db_api_scores(user, file)

    # JSON 后端
    data_dir = _get_data_dir(user)
    if file:
        filepath = data_dir / file
        if not filepath.exists():
            raise HTTPException(status_code=404, detail=f"文件不存在: {file}")
    else:
        json_files = sorted(data_dir.glob("scores_*.json"), reverse=True)
        if not json_files:
            raise HTTPException(status_code=404, detail="没有找到成绩文件")
        filepath = json_files[0]

    data = _load_score_file(filepath)
    merged = _merge_score_data(data)

    return JSONResponse({
        "username": user,
        "filename": filepath.name,
        "timestamp": data.get("timestamp"),
        "rating": data.get("rating"),
        "score_count": data.get("score_count"),
        "scores": merged,
    })


async def _db_api_scores(user: str, file: Optional[str]) -> JSONResponse:
    """数据库后端的 scores API"""
    from maimai_sync.db.engine import get_session_factory
    from maimai_sync.db.crud import (
        db_get_user_by_username,
        db_get_latest_snapshot,
        db_get_snapshot_by_id,
        db_get_scores_by_snapshot,
    )

    async with get_session_factory()() as session:
        db_user = await db_get_user_by_username(session, user)
        if not db_user:
            raise HTTPException(status_code=404, detail="用户不存在")

        if file and file.startswith("snapshot_"):
            snapshot_id = int(file.replace("snapshot_", ""))
            snapshot = await db_get_snapshot_by_id(session, snapshot_id)
            if not snapshot or snapshot.user_id != db_user.id:
                raise HTTPException(status_code=404, detail="快照不存在")
        else:
            snapshot = await db_get_latest_snapshot(session, db_user.id)

        if not snapshot:
            raise HTTPException(status_code=404, detail="没有找到成绩数据")

        records = await db_get_scores_by_snapshot(session, snapshot.id, db_user.id)
        scores = [r.to_dict() for r in records]

        return JSONResponse({
            "username": user,
            "filename": f"snapshot_{snapshot.id}",
            "timestamp": to_utc_iso(snapshot.snapshot_time),
            "rating": snapshot.rating,
            "score_count": snapshot.score_count,
            "scores": scores,
        })


@app.get("/api/score-files")
async def api_score_files(
    request: Request,
    username: Optional[str] = Query(None),
):
    """获取成绩文件/快照列表"""
    logged_in = _require_login(request)
    user = username or logged_in
    if not user:
        raise HTTPException(status_code=400, detail="未指定用户")

    if _use_db():
        return await _db_api_score_files(user)

    # JSON 后端
    data_dir = _get_data_dir(user)
    json_files = sorted(data_dir.glob("scores_*.json"), reverse=True)
    files = []
    for f in json_files:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                d = json.load(fh)
            files.append({
                "filename": f.name,
                "timestamp": d.get("timestamp"),
                "rating": d.get("rating"),
                "score_count": d.get("score_count"),
            })
        except Exception:
            files.append({"filename": f.name, "error": True})

    return JSONResponse({"username": user, "files": files})


async def _db_api_score_files(user: str) -> JSONResponse:
    """数据库后端的 score-files API"""
    from maimai_sync.db.engine import get_session_factory
    from maimai_sync.db.crud import db_get_user_by_username, db_get_snapshots

    async with get_session_factory()() as session:
        db_user = await db_get_user_by_username(session, user)
        if not db_user:
            raise HTTPException(status_code=404, detail="用户不存在")

        snapshots = await db_get_snapshots(session, db_user.id)
        files = [
            {
                "filename": f"snapshot_{s.id}",
                "timestamp": to_utc_iso(s.snapshot_time),
                "rating": s.rating,
                "score_count": s.score_count,
            }
            for s in snapshots
        ]

        return JSONResponse({"username": user, "files": files})


@app.delete("/api/score-files/{filename}")
async def api_delete_score_file(
    request: Request,
    filename: str,
):
    """删除指定的成绩文件/快照"""
    logged_in = _require_login(request)
    if not logged_in:
        raise HTTPException(status_code=401, detail="未登录")

    if _use_db():
        return await _db_api_delete_score_file(logged_in, filename)

    # JSON 后端：删除文件
    data_dir = _get_data_dir(logged_in)
    target = data_dir / filename
    if not target.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    # 安全检查：确保在数据目录内
    if not str(target.resolve()).startswith(str(data_dir.resolve())):
        raise HTTPException(status_code=403, detail="非法路径")
    target.unlink()
    return JSONResponse({"ok": True, "deleted": filename})


async def _db_api_delete_score_file(user: str, filename: str) -> JSONResponse:
    """数据库后端：删除快照及其成绩"""
    from maimai_sync.db.engine import get_session_factory
    from maimai_sync.db.crud import db_get_user_by_username, db_delete_snapshot

    async with get_session_factory()() as session:
        db_user = await db_get_user_by_username(session, user)
        if not db_user:
            raise HTTPException(status_code=404, detail="用户不存在")

        if not filename.startswith("snapshot_"):
            raise HTTPException(status_code=400, detail="无效的快照标识")

        snapshot_id = int(filename.replace("snapshot_", ""))
        deleted = await db_delete_snapshot(session, user_id=db_user.id, snapshot_id=snapshot_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="快照不存在")
        return JSONResponse({"ok": True, "deleted": filename})

@app.get("/api/leaderboard")
async def api_leaderboard(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
):
    """Rating 排行榜"""
    _require_login(request)

    if not _use_db():
        # JSON 后端无法高效排行，返回空
        return JSONResponse({"leaderboard": [], "backend": "json"})

    from maimai_sync.db.engine import get_session_factory
    from maimai_sync.db.crud import db_get_leaderboard

    async with get_session_factory()() as session:
        lb = await db_get_leaderboard(session, limit)
        return JSONResponse({"leaderboard": lb, "backend": "mysql"})


@app.get("/api/leaderboard/{username}/b50")
async def api_leaderboard_b50(
    request: Request,
    username: str,
):
    """查看某个用户的 B50 详情"""
    _require_login(request)

    if not _use_db():
        return JSONResponse({"error": "排行榜功能需要 MySQL 后端"}, status_code=400)

    from maimai_sync.db.engine import get_session_factory
    from maimai_sync.db.crud import db_get_user_by_username, db_get_user_b50

    async with get_session_factory()() as session:
        db_user = await db_get_user_by_username(session, username)
        if not db_user:
            raise HTTPException(status_code=404, detail="用户不存在")

        b50 = await db_get_user_b50(session, db_user.id)
        b50["username"] = username
        return JSONResponse(b50)


@app.get("/api/rating-history")
async def api_rating_history(
    request: Request,
    username: Optional[str] = Query(None),
):
    """获取用户 Rating 变化历史（用于折线图）"""
    logged_in = _require_login(request)
    user = username or logged_in
    if not user:
        raise HTTPException(status_code=400, detail="未指定用户")

    if not _use_db():
        return JSONResponse({"history": [], "username": user})

    from maimai_sync.db.engine import get_session_factory
    from maimai_sync.db.crud import db_get_user_by_username, db_get_snapshots

    async with get_session_factory()() as session:
        db_user = await db_get_user_by_username(session, user)
        if not db_user:
            raise HTTPException(status_code=404, detail="用户不存在")

        snapshots = await db_get_snapshots(session, db_user.id)
        history = [
            {
                "timestamp": to_utc_iso(s.snapshot_time),
                "rating": s.rating,
                "score_count": s.score_count,
            }
            for s in reversed(snapshots)
        ]

        return JSONResponse({
            "username": user,
            "history": history,
        })


# ============================================================
# 歌曲详情 API
# ============================================================

# 缓存 music_db 原始歌曲数据
_music_db_songs: Optional[dict] = None

def _get_music_db() -> dict:
    """加载 music_db.json，返回 {song_id_str: song_obj}"""
    global _music_db_songs
    if _music_db_songs is not None:
        return _music_db_songs
    db_path = DEFAULT_DATA_DIR / ".music_db.json"
    if not db_path.exists():
        _music_db_songs = {}
        return _music_db_songs
    try:
        with open(db_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        songs = raw.get("songs", []) if isinstance(raw, dict) else raw
        _music_db_songs = {}
        for s in songs:
            sid = str(s.get("id", ""))
            _music_db_songs[sid] = s
        return _music_db_songs
    except Exception:
        _music_db_songs = {}
        return _music_db_songs


# 缓存水鱼 chart_stats 数据
_chart_stats_cache: Optional[dict] = None
_chart_stats_ts: float = 0.0
_CHART_STATS_TTL = 3600 * 6  # 6 小时


async def _get_chart_stats() -> dict:
    """获取水鱼拟合定数等统计数据，带缓存"""
    global _chart_stats_cache, _chart_stats_ts
    now = time.time()
    if _chart_stats_cache is not None and (now - _chart_stats_ts) < _CHART_STATS_TTL:
        return _chart_stats_cache

    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://www.diving-fish.com/api/maimaidxprober/chart_stats",
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    _chart_stats_cache = data.get("charts", {}) if isinstance(data, dict) else {}
                    _chart_stats_ts = now
                    logger.info("chart_stats 已更新，共 %d 首歌曲", len(_chart_stats_cache))
                    return _chart_stats_cache
    except Exception as e:
        logger.warning("获取 chart_stats 失败: %s", e)
        return _chart_stats_cache or {}

    return _chart_stats_cache or {}


@app.get("/api/aliases")
async def api_aliases(request: Request):
    """获取所有歌曲别名（缓存 6h，前端搜索用）"""
    _require_login(request)
    try:
        from maimai_sync.lxns import fetch_aliases
        alias_map = await fetch_aliases()
        return JSONResponse({"aliases": alias_map})
    except Exception as e:
        return JSONResponse({"aliases": {}, "error": str(e)})


@app.get("/api/song/{song_id}")
async def api_song_detail(request: Request, song_id: str):
    """获取歌曲元数据 + 拟合定数等统计数据（LXNS 优先，水鱼兜底）"""
    _require_login(request)

    # 尝试 LXNS 获取歌曲详情
    lxns_song = None
    try:
        from maimai_sync.lxns import fetch_song
        lxns_song = await fetch_song(int(song_id))
    except Exception:
        pass

    # 水鱼 music_db 作为兜底
    db = _get_music_db()
    df_song = db.get(song_id)

    # 如果两个都没有，404
    if not lxns_song and not df_song:
        raise HTTPException(status_code=404, detail="歌曲不存在")

    # ---- 元数据：LXNS 优先 ----
    title = ""
    song_type = ""
    category = ""
    version_str = ""
    artist = ""
    bpm = 0
    is_new = False
    aliases = []

    if lxns_song:
        title = lxns_song.get("title", "")
        song_type = lxns_song.get("type", "")  # standard/dx/utage
        category = lxns_song.get("genre", "")
        version_str = str(lxns_song.get("version", ""))
        artist = lxns_song.get("artist", "")
        bpm = lxns_song.get("bpm", 0)
    elif df_song:
        basic = df_song.get("basic_info", {}) or {}
        title = df_song.get("title", "")
        song_type = df_song.get("type", "")
        category = basic.get("genre", "") if isinstance(basic, dict) else ""
        version_str = basic.get("from", "") if isinstance(basic, dict) else ""
        artist = basic.get("artist", "") if isinstance(basic, dict) else ""
        bpm = basic.get("bpm", 0) if isinstance(basic, dict) else 0
        is_new = basic.get("is_new", False) if isinstance(basic, dict) else False

    # 别名（LXNS ID 体系：DX 歌曲不加 10000，需同时查原始 ID 和 %10000）
    try:
        from maimai_sync.lxns import fetch_aliases
        alias_map = await fetch_aliases()
        aliases = alias_map.get(song_id, [])
        if not aliases:
            # 尝试去掉 DX 偏移（水鱼 DX 歌曲 ID = 标准 ID + 10000，LXNS 不加）
            raw_id = int(song_id)
            if raw_id > 10000:
                aliases = alias_map.get(str(raw_id % 10000), [])
    except Exception:
        pass

    # 曲绘 URL（水鱼）
    mid = int(song_id)
    cover_id = mid - 10000 if 10000 < mid <= 11000 else mid
    cover_url = f"https://www.diving-fish.com/covers/{cover_id:05d}.png"

    # ---- 拟合数据：固定用水鱼 chart_stats（样本量更大） ----
    chart_stats = await _get_chart_stats()
    stats_entries = chart_stats.get(song_id, []) or []
    stats_by_label = {}
    for se in stats_entries:
        if isinstance(se, dict):
            stats_by_label[str(se.get("diff", ""))] = se

    # ---- 谱面数据：LXNS 元数据 + 水鱼拟合 ----
    charts_detail = []

    if lxns_song:
        diffs = lxns_song.get("difficulties", {}) or {}
        std_diffs = diffs.get("standard", []) or []
        dx_diffs = diffs.get("dx", []) or []
        chart_diffs = dx_diffs if song_type == "dx" else std_diffs
        if not chart_diffs:
            chart_diffs = dx_diffs or std_diffs

        for ch in chart_diffs:
            lv_idx = ch.get("difficulty", 0) if "difficulty" in ch else ch.get("level_index", 0)
            lv_label = ch.get("level", "")
            # 水鱼拟合数据（样本量更大）
            df_stats = stats_by_label.get(lv_label)
            fitted = df_stats.get("fit_diff") if df_stats else None
            avg_ach = df_stats.get("avg") if df_stats else None
            std_dev = df_stats.get("std_dev") if df_stats else None
            cnt = df_stats.get("cnt") if df_stats else None

            charts_detail.append({
                "level_index": lv_idx,
                "level_label": lv_label,
                "constant": ch.get("level_value"),
                "fitted_constant": round(fitted, 4) if fitted is not None else None,
                "avg_achievements": round(avg_ach, 2) if avg_ach is not None else None,
                "std_dev": round(std_dev, 4) if std_dev is not None else None,
                "sample_count": cnt,
                "charter": ch.get("note_designer", ""),
                "notes": ch.get("notes", {}).get("total", 0) if isinstance(ch.get("notes"), dict) else 0,
                "tap": ch.get("notes", {}).get("tap", 0) if isinstance(ch.get("notes"), dict) else (ch.get("tap_num", 0)),
                "hold": ch.get("notes", {}).get("hold", 0) if isinstance(ch.get("notes"), dict) else (ch.get("hold_num", 0)),
                "slide": ch.get("notes", {}).get("slide", 0) if isinstance(ch.get("notes"), dict) else (ch.get("slide_num", 0)),
                "touch": ch.get("notes", {}).get("touch", 0) if isinstance(ch.get("notes"), dict) else (ch.get("touch_num", 0)),
                "break_n": ch.get("notes", {}).get("break", 0) if isinstance(ch.get("notes"), dict) else (ch.get("break_num", 0)),
            })
    elif df_song:
        # 水鱼兜底：和之前一样
        ds_list = df_song.get("ds", [])
        level_list = df_song.get("level", [])
        charts_list = df_song.get("charts", [])

        chart_stats = await _get_chart_stats()
        stats_entries = chart_stats.get(song_id, []) or []
        stats_by_label = {}
        for se in stats_entries:
            if isinstance(se, dict):
                label = str(se.get("diff", ""))
                stats_by_label[label] = se

        basic = df_song.get("basic_info", {}) or {}
        for i in range(min(len(ds_list), len(level_list), 5)):
            chart_info = charts_list[i] if i < len(charts_list) else {}
            notes = chart_info.get("notes", []) if isinstance(chart_info, dict) else []
            lv_label = str(level_list[i]) if i < len(level_list) else ""
            stats = stats_by_label.get(lv_label)
            fitted = stats.get("fit_diff") if stats else None
            avg_ach = stats.get("avg") if stats else None
            std_dev = stats.get("std_dev") if stats else None
            cnt = stats.get("cnt") if stats else None

            charts_detail.append({
                "level_index": i,
                "level_label": lv_label,
                "constant": float(ds_list[i]) if i < len(ds_list) and ds_list[i] > 0 else None,
                "fitted_constant": round(fitted, 4) if fitted is not None else None,
                "avg_achievements": round(avg_ach, 2) if avg_ach is not None else None,
                "std_dev": round(std_dev, 4) if std_dev is not None else None,
                "sample_count": cnt,
                "charter": chart_info.get("charter", "") if isinstance(chart_info, dict) else "",
                "notes": sum(notes) if notes else 0,
                "tap": notes[0] if len(notes) > 0 else 0,
                "hold": notes[1] if len(notes) > 1 else 0,
                "slide": notes[2] if len(notes) > 2 else 0,
                "touch": notes[3] if len(notes) > 3 and df_song.get("type") == "DX" else 0,
                "break_n": notes[4] if len(notes) > 4 else 0,
            })

    return JSONResponse({
        "song_id": song_id,
        "title": title,
        "type": song_type,
        "cover_url": cover_url,
        "category": category,
        "version": version_str,
        "artist": artist,
        "bpm": bpm,
        "aliases": aliases,
        "is_new": is_new,
        "charts": charts_detail,
    })


# ============================================================
# 前端页面
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = _STATIC_DIR / "index.html"
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>maimai-sync Web</h1><p>前端页面未找到</p>")


# ============================================================
# 启动入口
# ============================================================

def main():
    import uvicorn
    port = int(os.environ.get("MAIMAI_WEB_PORT", "8765"))
    print(f"maimai-sync Web 启动中...")
    print(f"   后端: {'MySQL' if _use_db() else 'JSON 文件'}")
    print(f"   访问 http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
