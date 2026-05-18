"""管理员后台 — 独立 FastAPI 应用，端口 15500

用法:
    python -m maimai_sync.web.admin_app
"""

import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from maimai_sync.account.manager import AccountManager
from maimai_sync.config import DEFAULT_DATA_DIR
from maimai_sync.utils import to_utc_iso

logger = logging.getLogger("maimai_sync.admin")

admin_app = FastAPI(title="maimai-sync Admin", version="0.3.0")

_STATIC_DIR = Path(__file__).parent / "static"
_account_mgr: Optional[AccountManager] = None

def _get_account_mgr() -> AccountManager:
    global _account_mgr
    if _account_mgr is None:
        _account_mgr = AccountManager()
    return _account_mgr

def _use_db() -> bool:
    return True

# Admin session: token -> {"username": str, "created": float}
_admin_sessions: dict[str, dict] = {}
_ADMIN_SESSION_TTL = 3600 * 8


def _require_admin(request: Request) -> str:
    """验证管理员登录，返回用户名"""
    token = request.cookies.get("maimai_admin_session")
    if not token:
        raise HTTPException(status_code=401, detail="未登录")
    session = _admin_sessions.get(token)
    if not session or time.time() - session["created"] > _ADMIN_SESSION_TTL:
        _admin_sessions.pop(token, None)
        raise HTTPException(status_code=401, detail="登录已过期")
    return session["username"]


# ============================================================
# 管理员登录
# ============================================================

@admin_app.post("/api/admin/login")
async def admin_login(request: Request):
    body = await request.json()
    username = body.get("username", "").strip()
    password = body.get("password", "")

    if not username or not password:
        raise HTTPException(status_code=400, detail="用户名和密码不能为空")

    mgr = _get_account_mgr()
    try:
        profile = await mgr.login(username, password)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))

    if not profile.is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")

    token = secrets.token_hex(32)
    _admin_sessions[token] = {"username": username, "created": time.time()}

    response = JSONResponse({"ok": True, "username": username})
    response.set_cookie(
        key="maimai_admin_session",
        value=token,
        max_age=_ADMIN_SESSION_TTL,
        httponly=True,
        samesite="lax",
    )
    return response


@admin_app.post("/api/admin/logout")
async def admin_logout(request: Request):
    token = request.cookies.get("maimai_admin_session")
    if token:
        _admin_sessions.pop(token, None)
    response = JSONResponse({"ok": True})
    response.delete_cookie(key="maimai_admin_session")
    return response


@admin_app.get("/api/admin/me")
async def admin_me(request: Request):
    try:
        username = _require_admin(request)
        return JSONResponse({"logged_in": True, "username": username})
    except HTTPException:
        return JSONResponse({"logged_in": False, "username": None})


# ============================================================
# 用户管理 API
# ============================================================

@admin_app.get("/api/admin/users")
async def admin_users(request: Request):
    _require_admin(request)

    if not _use_db():
        mgr = _get_account_mgr()
        users = await mgr.list_users()
        result = []
        for u in users:
            p = await mgr.get_user(u)
            result.append({
                "username": p.username,
                "nickname": p.nickname,
                "is_admin": p.is_admin,
                "has_df_token": bool(p.df_import_token),
                "note": p.note,
                "snapshot_count": 0,
                "latest_rating": 0,
            })
        return JSONResponse({"users": result})

    from maimai_sync.db.engine import get_session_factory
    from sqlalchemy import select, func

    async with get_session_factory()() as session:
        from maimai_sync.db.models import User, ScoreSnapshot

        result = await session.execute(select(User).order_by(User.id))
        users = result.scalars().all()

        user_list = []
        for u in users:
            snap_result = await session.execute(
                select(func.count(ScoreSnapshot.id), func.max(ScoreSnapshot.rating))
                .where(ScoreSnapshot.user_id == u.id)
            )
            cnt, max_r = snap_result.one()
            user_list.append({
                "id": u.id,
                "username": u.username,
                "nickname": u.nickname,
                "is_admin": u.is_admin,
                "has_df_token": bool(u.df_import_token),
                "note": u.note,
                "snapshot_count": cnt or 0,
                "latest_rating": max_r or 0,
                "created_at": to_utc_iso(u.created_at),
                "last_login_at": to_utc_iso(u.last_login_at),
            })

        return JSONResponse({"users": user_list})


@admin_app.post("/api/admin/users/batch-delete")
async def admin_batch_delete_users(request: Request):
    admin_user = _require_admin(request)
    body = await request.json()
    users: list[dict] = body.get("users", [])

    if not users:
        raise HTTPException(status_code=400, detail="请提供要删除的用户列表")

    if not _use_db():
        raise HTTPException(status_code=400, detail="JSON 后端不支持此操作")

    from maimai_sync.db.engine import get_session_factory
    from maimai_sync.db.models import User, ScoreSnapshot, ScoreRecord
    from sqlalchemy import select, delete

    deleted = []
    failed = []

    async with get_session_factory()() as session:
        async with session.begin():
            for uinfo in users:
                user = None
                # 优先用 ID 查，再用用户名查
                uid = uinfo.get("id")
                username = uinfo.get("username", "")
                if uid:
                    user = await session.get(User, uid)
                if not user and username:
                    u_result = await session.execute(
                        select(User).where(User.username == username)
                    )
                    user = u_result.scalar_one_or_none()
                if not user:
                    failed.append({"id": uid, "username": username, "reason": "用户不存在"})
                    continue
                if user.username == admin_user:
                    failed.append({"id": user.id, "username": user.username, "reason": "不能删除自己"})
                    continue

                await session.execute(delete(ScoreRecord).where(ScoreRecord.user_id == user.id))
                await session.execute(delete(ScoreSnapshot).where(ScoreSnapshot.user_id == user.id))
                await session.delete(user)
                deleted.append(user.username)

    return JSONResponse({"ok": True, "deleted": deleted, "failed": failed})


@admin_app.post("/api/admin/users/{username}")
async def admin_update_user(request: Request, username: str):
    _require_admin(request)
    body = await request.json()

    if _use_db():
        from maimai_sync.db.engine import get_session_factory
        from maimai_sync.db.crud import db_get_user_by_username

        async with get_session_factory()() as session:
            async with session.begin():
                user = await db_get_user_by_username(session, username)
                if not user:
                    raise HTTPException(status_code=404, detail="用户不存在")

                if "nickname" in body:
                    user.nickname = body["nickname"] or None
                if "is_admin" in body:
                    user.is_admin = bool(body["is_admin"])
                if "note" in body:
                    user.note = body["note"] or None

                return JSONResponse({"ok": True, "username": username})
    else:
        mgr = _get_account_mgr()
        profile = await mgr.get_user(username)
        if not profile:
            raise HTTPException(status_code=404, detail="用户不存在")
        # JSON 后端只支持更新 nickname
        if "nickname" in body:
            await mgr.update_profile(username, nickname=body["nickname"] or None)
        return JSONResponse({"ok": True, "username": username})


@admin_app.delete("/api/admin/users/{username}")
async def admin_delete_user(request: Request, username: str):
    admin_user = _require_admin(request)
    if username == admin_user:
        raise HTTPException(status_code=400, detail="不能删除自己")

    if _use_db():
        from maimai_sync.db.engine import get_session_factory
        from maimai_sync.db.models import User, ScoreSnapshot, ScoreRecord
        from sqlalchemy import select, delete

        async with get_session_factory()() as session:
            async with session.begin():
                u_result = await session.execute(
                    select(User).where(User.username == username)
                )
                user = u_result.scalar_one_or_none()
                if not user:
                    raise HTTPException(status_code=404, detail="用户不存在")

                await session.execute(delete(ScoreRecord).where(ScoreRecord.user_id == user.id))
                await session.execute(delete(ScoreSnapshot).where(ScoreSnapshot.user_id == user.id))
                await session.delete(user)

        return JSONResponse({"ok": True, "deleted": username})
    else:
        raise HTTPException(status_code=400, detail="JSON 后端不支持此操作")


@admin_app.get("/api/admin/users/{username}/snapshots")
async def admin_user_snapshots(request: Request, username: str):
    _require_admin(request)

    if not _use_db():
        return JSONResponse({"snapshots": []})

    from maimai_sync.db.engine import get_session_factory
    from maimai_sync.db.crud import db_get_user_by_username, db_get_snapshots

    async with get_session_factory()() as session:
        user = await db_get_user_by_username(session, username)
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")

        snapshots = await db_get_snapshots(session, user.id)
        return JSONResponse({
            "username": username,
            "snapshots": [
                {"id": s.id, "snapshot_time": to_utc_iso(s.snapshot_time),
                 "rating": s.rating, "score_count": s.score_count}
                for s in snapshots
            ]
        })


@admin_app.delete("/api/admin/snapshots/{snapshot_id}")
async def admin_delete_snapshot(request: Request, snapshot_id: int):
    _require_admin(request)

    if not _use_db():
        raise HTTPException(status_code=400, detail="JSON 后端不支持此操作")

    from maimai_sync.db.engine import get_session_factory
    from sqlalchemy import select, delete
    from maimai_sync.db.models import ScoreSnapshot, ScoreRecord

    async with get_session_factory()() as session:
        async with session.begin():
            snap = await session.get(ScoreSnapshot, snapshot_id)
            if not snap:
                raise HTTPException(status_code=404, detail="快照不存在")

            await session.execute(delete(ScoreRecord).where(ScoreRecord.snapshot_id == snapshot_id))
            await session.execute(delete(ScoreSnapshot).where(ScoreSnapshot.id == snapshot_id))

        return JSONResponse({"ok": True, "deleted": snapshot_id})


# ============================================================
# 前端页面
# ============================================================

@admin_app.get("/", response_class=HTMLResponse)
async def admin_index():
    html_path = _STATIC_DIR / "admin.html"
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Admin</h1><p>管理页面未找到</p>")


def main():
    import uvicorn
    port = int(os.environ.get("MAIMAI_ADMIN_PORT", "15500"))
    print(f"maimai-sync 管理后台启动中...")
    print(f"   访问 http://localhost:{port}")
    uvicorn.run(admin_app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
