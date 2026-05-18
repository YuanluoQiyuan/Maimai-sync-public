"""CLI 入口

用法:
    # 账号管理
    python -m maimai_sync account register <用户名>
    python -m maimai_sync account login <用户名>
    python -m maimai_sync account whoami
    python -m maimai_sync account config --df-import <Token> --proxy <Proxy>
    python -m maimai_sync account list
    python -m maimai_sync account delete <用户名>

    # 同步
    python -m maimai_sync upload --df-import <Token>     # 从缓存上传
    python -m maimai_sync download --df-import <Token>   # 从水鱼下载

    # 查看
    python -m maimai_sync cache --list
"""

import argparse
import asyncio
import getpass
import logging
import sys
import os

# 修复 Windows 终端编码问题
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from maimai_sync.account import AccountManager, UserProfile
from maimai_sync.storage import JsonScoreStorage
from maimai_sync.uploader import DivingFishUploader


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(name)s: %(message)s",
    )


# ============================================================
# 账号命令
# ============================================================

def _prompt_password(prompt: str = "密码: ") -> str:
    """安全输入密码"""
    return getpass.getpass(prompt)


async def cmd_account_register(args):
    """注册新用户"""
    mgr = AccountManager()
    username = args.username
    password = args.password or _prompt_password("设置密码: ")
    password2 = args.password and args.password or _prompt_password("确认密码: ")
    if not args.password and password != password2:
        print("两次密码不一致")
        return 1
    try:
        profile = await mgr.register(username, password)
        print(f"注册成功: {profile.username}")
    except ValueError as e:
        print(f"注册失败: {e}")
        return 1


async def cmd_account_login(args):
    """登录"""
    mgr = AccountManager()
    username = args.username
    password = args.password or _prompt_password("密码: ")
    try:
        profile = await mgr.login(username, password)
        # 设置当前登录用户
        _set_current_user(username)
        print(f"登录成功: {username}")
        _print_profile(profile)
    except ValueError as e:
        print(f"登录失败: {e}")
        return 1


async def cmd_account_whoami(args):
    """查看当前登录用户"""
    username = _get_current_user()
    if not username:
        print("未登录")
        return 1
    mgr = AccountManager()
    profile = await mgr.get_user(username)
    if not profile:
        print(f"用户 '{username}' 不存在（可能已被删除）")
        _clear_current_user()
        return 1
    print(f"当前用户: {username}")
    _print_profile(profile)


async def cmd_account_config(args):
    """修改当前用户配置"""
    username = _get_current_user()
    if not username:
        print("请先登录: python -m maimai_sync account login <用户名>")
        return 1
    mgr = AccountManager()
    kwargs = {}
    if args.df_import is not None:
        kwargs["df_import_token"] = args.df_import
    if args.note is not None:
        kwargs["note"] = args.note

    if not kwargs:
        print("没有指定要修改的配置，用 --df-import / --note")
        return 1

    try:
        profile = await mgr.update_profile(username, **kwargs)
        print("配置已更新")
        _print_profile(profile)
    except ValueError as e:
        print(f"更新失败: {e}")
        return 1


async def cmd_account_list(args):
    """列出所有用户"""
    mgr = AccountManager()
    users = await mgr.list_users()
    if not users:
        print("没有注册用户")
        return
    print(f"注册用户 ({len(users)} 个):")
    current = _get_current_user()
    for name in users:
        marker = " ← 当前" if name == current else ""
        profile = await mgr.get_user(name)
        token_flag = " [已设Token]" if profile and profile.df_import_token else ""
        print(f"  - {name}{token_flag}{marker}")


async def cmd_account_delete(args):
    """删除用户"""
    mgr = AccountManager()
    username = args.username
    password = args.password or _prompt_password(f"确认删除 '{username}'，输入密码: ")
    try:
        await mgr.delete(username, password)
        print(f"用户 '{username}' 已删除")
        # 如果删除的是当前用户，清除登录状态
        if _get_current_user() == username:
            _clear_current_user()
    except ValueError as e:
        print(f"删除失败: {e}")
        return 1


async def cmd_account_passwd(args):
    """修改密码"""
    username = _get_current_user()
    if not username:
        print("请先登录")
        return 1
    mgr = AccountManager()
    old = _prompt_password("旧密码: ")
    new = _prompt_password("新密码: ")
    new2 = _prompt_password("确认新密码: ")
    if new != new2:
        print("两次密码不一致")
        return 1
    try:
        await mgr.change_password(username, old, new)
        print("密码已修改")
    except ValueError as e:
        print(f"修改失败: {e}")
        return 1


# ============================================================
# 登录状态持久化
# ============================================================

# 简单的登录状态：存一个 .current_user 文件
# 对于本地工具足够用，不是 Web 服务不需要 session

_CURRENT_USER_FILE = ".current_user"


def _get_current_user() -> str | None:
    """获取当前登录用户"""
    from maimai_sync.config import DEFAULT_DATA_DIR
    marker = DEFAULT_DATA_DIR / _CURRENT_USER_FILE
    if marker.exists():
        return marker.read_text(encoding="utf-8").strip() or None
    return None


def _set_current_user(username: str):
    """设置当前登录用户"""
    from maimai_sync.config import DEFAULT_DATA_DIR
    DEFAULT_DATA_DIR.mkdir(parents=True, exist_ok=True)
    marker = DEFAULT_DATA_DIR / _CURRENT_USER_FILE
    marker.write_text(username, encoding="utf-8")


def _clear_current_user():
    """清除当前登录状态"""
    from maimai_sync.config import DEFAULT_DATA_DIR
    marker = DEFAULT_DATA_DIR / _CURRENT_USER_FILE
    if marker.exists():
        marker.unlink()


# ============================================================
# 上传 / 下载命令
# ============================================================

async def cmd_upload(args):
    """从缓存上传到水鱼"""
    username = _get_current_user()
    if not username:
        print("请先登录: python -m maimai_sync account login <用户名>")
        return 1

    mgr = AccountManager()
    profile = await mgr.get_user(username)
    if not profile:
        print(f"用户 '{username}' 不存在")
        return 1

    # Token 优先级：命令行 > 用户配置
    import_token = args.df_import or profile.df_import_token
    if not import_token:
        print("请提供水鱼 Import Token: --df-import <Token>")
        print("或先配置到账号: python -m maimai_sync account config --df-import <Token>")
        return 1

    storage = JsonScoreStorage(username=username)
    uploader = DivingFishUploader()

    # 加载缓存
    if args.from_cache:
        cache_path = None if args.from_cache == "__latest__" else args.from_cache
        if cache_path:
            data = storage.load(cache_path)
            print(f"[缓存] 加载: {cache_path}")
        else:
            data = storage.load_latest()
            print(f"[缓存] 加载最新缓存")
    else:
        # 默认用最新缓存
        data = storage.load_latest()
        print(f"[缓存] 加载最新缓存")

    print(f"    时间: {data['timestamp']}")
    print(f"    Rating: {data['rating']}")
    print(f"    成绩数: {data['score_count']}")

    # 上传
    print("\n[3] 上传到水鱼查分器...")
    ok = await uploader.upload(data.get("scores_upload", []), import_token)
    if ok:
        print("    上传成功!")
    else:
        print("    上传失败")
        return 1


async def cmd_download(args):
    """从水鱼下载成绩 → 存本地"""
    username = _get_current_user()
    if not username:
        print("请先登录: python -m maimai_sync account login <用户名>")
        return 1

    mgr = AccountManager()
    profile = await mgr.get_user(username)
    if not profile:
        print(f"用户 '{username}' 不存在")
        return 1

    import_token = args.df_import or profile.df_import_token
    if not import_token:
        print("请提供水鱼 Import Token: --df-import <Token>")
        print("或先配置: python -m maimai_sync account config --df-import <Token>")
        return 1

    from maimai_sync.downloader import DivingFishDownloader
    from maimai_sync.downloader.divingfish import save_downloaded_scores

    downloader = DivingFishDownloader()

    print("[1] 从水鱼下载成绩...")
    try:
        result = await downloader.download(import_token)
    except ValueError as e:
        print(f"    下载失败: {e}")
        return 1

    if not result:
        print("    下载失败（网络错误或 API 返回异常）")
        return 1

    records = result.get("records", [])
    rating = result.get("rating", 0)
    print(f"    下载成功: {len(records)} 条记录, Rating={rating}")

    if not records:
        print("    水鱼上没有成绩记录")
        return 0

    print("\n[2] 保存到本地...")
    success = await save_downloaded_scores(username, records, rating)
    if success:
        print(f"    已保存 {len(records)} 条成绩")
        return 0
    else:
        print("    保存失败（可能5分钟内已下载过相同数据）")
        return 1


def cmd_cache(args):
    """查看缓存"""
    username = _get_current_user()
    storage = JsonScoreStorage(username=username) if username else JsonScoreStorage()
    files = storage.list_files()
    if not files:
        print("没有本地缓存文件")
        return
    user_tag = f" (用户: {username})" if username else ""
    print(f"本地缓存{user_tag} ({len(files)} 个):\n")
    for f in files:
        if f.get("error"):
            print(f"  {f['filename']}  [格式错误]")
            continue
        tag = "[可上传]" if f.get("has_upload_data") else "[仅查看]"
        print(f"  {f['filename']}  {tag}")
        print(f"    {f['timestamp']}  Rating {f['rating']}  {f['score_count']}条")


def cmd_web(args):
    """启动 Web 可视化"""
    try:
        import uvicorn
        from maimai_sync.web.app import app
    except ImportError:
        print("需要安装 fastapi 和 uvicorn: pip install fastapi uvicorn")
        return 1

    port = args.port
    host = args.host
    print(f"🎵 maimai-sync Web 启动中...")
    print(f"   访问 http://localhost:{port}")
    uvicorn.run(app, host=host, port=port)


# ============================================================
# 工具函数
# ============================================================

def _print_profile(profile: UserProfile):
    """打印用户配置（隐藏密码和 Token 详情）"""
    print(f"    用户名: {profile.username}")
    if profile.df_import_token:
        print(f"    水鱼 Token: {profile.df_import_token[:8]}...（已配置）")
    else:
        print(f"    水鱼 Token: 未配置")
    if profile.note:
        print(f"    备注: {profile.note}")


# ============================================================
# 参数解析
# ============================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="maimai_sync",
        description="maimai-sync - 舞萌DX 机台取分同步工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="详细日志")

    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # --- account ---
    acct = subparsers.add_parser("account", aliases=["a"], help="账号管理")
    acct_sub = acct.add_subparsers(dest="action", help="账号操作")

    # account register
    reg = acct_sub.add_parser("register", aliases=["reg"], help="注册新用户")
    reg.add_argument("username", help="用户名")
    reg.add_argument("-p", "--password", help="密码（不提供则交互输入）")

    # account login
    login = acct_sub.add_parser("login", help="登录")
    login.add_argument("username", help="用户名")
    login.add_argument("-p", "--password", help="密码（不提供则交互输入）")

    # account whoami
    acct_sub.add_parser("whoami", help="查看当前登录用户")

    # account config
    cfg = acct_sub.add_parser("config", help="修改用户配置")
    cfg.add_argument("--df-import", dest="df_import", help="水鱼 Import Token")
    cfg.add_argument("--note", help="备注")

    # account list
    acct_sub.add_parser("list", aliases=["ls"], help="列出所有用户")

    # account delete
    del_p = acct_sub.add_parser("delete", aliases=["del"], help="删除用户")
    del_p.add_argument("username", help="用户名")
    del_p.add_argument("-p", "--password", help="密码（不提供则交互输入）")

    # account passwd
    acct_sub.add_parser("passwd", help="修改密码")

    # --- upload ---
    upload_p = subparsers.add_parser("upload", aliases=["up"], help="从缓存上传水鱼")
    upload_p.add_argument("--df-import", dest="df_import", help="水鱼 Import Token（覆盖账号配置）")
    upload_p.add_argument("--from-cache", nargs="?", const="__latest__",
                          help="指定缓存文件（不指定则用最新的）")

    # --- download ---
    download_p = subparsers.add_parser("download", aliases=["dl"], help="从水鱼下载成绩 → 存本地")
    download_p.add_argument("--df-import", dest="df_import", help="水鱼 Import Token（覆盖账号配置）")

    # --- cache ---
    cache_p = subparsers.add_parser("cache", help="查看本地缓存")
    cache_p.add_argument("--list", action="store_true", help="列出缓存文件")

    # --- web ---
    web_p = subparsers.add_parser("web", help="启动 Web 可视化")
    web_p.add_argument("--port", type=int, default=8765, help="端口号（默认 8765）")
    web_p.add_argument("--host", default="0.0.0.0", help="监听地址（默认 0.0.0.0）")

    return parser


# ============================================================
# 命令路由
# ============================================================

COMMAND_MAP = {
    "account": {
        "register": lambda args: cmd_account_register(args),
        "reg": lambda args: cmd_account_register(args),
        "login": lambda args: cmd_account_login(args),
        "whoami": lambda args: cmd_account_whoami(args),
        "config": lambda args: cmd_account_config(args),
        "list": lambda args: cmd_account_list(args),
        "ls": lambda args: cmd_account_list(args),
        "delete": lambda args: cmd_account_delete(args),
        "del": lambda args: cmd_account_delete(args),
        "passwd": lambda args: cmd_account_passwd(args),
    },
    "a": None,  # alias for account, handled below
}


def main():
    parser = build_parser()
    args = parser.parse_args()
    setup_logging(args.verbose)

    cmd = args.command

    if not cmd:
        parser.print_help()
        return

    # account / a 子命令
    if cmd in ("account", "a"):
        action = getattr(args, "action", None)
        if not action:
            parser.parse_args(["account", "--help"])
            return
        # "a" 是 "account" 的别名，共用同一套 action
        handler = COMMAND_MAP["account"].get(action)
        if handler:
            ret = asyncio.run(handler(args))
            sys.exit(ret if ret else 0)
        else:
            print(f"未知操作: {action}")
            return

    # 异步命令
    if cmd in ("upload", "up"):
        ret = asyncio.run(cmd_upload(args))
    elif cmd in ("download", "dl"):
        ret = asyncio.run(cmd_download(args))
    elif cmd == "cache":
        ret = cmd_cache(args)
    elif cmd == "web":
        ret = cmd_web(args)
    else:
        print(f"未知命令: {cmd}")
        ret = 1

    sys.exit(ret if ret else 0)


if __name__ == "__main__":
    main()
