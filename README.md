# maimai-sync

舞萌DX 成绩同步工具 — 上传水鱼 / 从水鱼下载

Web 可视化面板（亮暗双主题 + 手机端适配）、多用户系统、歌曲详情弹窗、管理后台。

## 功能

- **Web 可视化**: 成绩概览（Rating 趋势折线图、统计图表）、成绩列表（按歌曲合并/筛选/排序/别名搜索）、Rating 分析（B35/B15 表格+等级分析）、排行榜（B50 详情）
- **歌曲详情弹窗**: 曲绘封面、曲师/类别/BPM/版本、官方定数+拟合定数（水鱼大样本）、谱师+物量分项、平均达成率、别名、玩家成绩（FC/FS 徽章）
- **亮暗双主题**: 一键切换，localStorage 记忆，跟随系统偏好，手机/桌面全适配
- **手机端适配**: 全部页面卡片式响应式布局，触控友好
- **多用户系统**: 注册、登录、个人配置（水鱼 Token / 昵称）
- **水鱼传分**: 上传至[水鱼查分器](https://www.diving-fish.com/maimaidx/prober/)
- **水鱼下载**: 从水鱼拉取成绩回本地数据库
- **管理后台**: 独立端口 15500，用户管理、快照管理、批量删除
- **数据库后端**: MySQL 生产模式，SQLite 开发模式（零配置）

## 快速开始

### 安装

```bash
pip install -e .
```

### Web 模式（推荐）

```bash
# 开发模式（自动使用 SQLite）
python -m uvicorn maimai_sync.web.app:app --port 8765

# 生产模式（MySQL）
export MAIMAI_DB_URL=mysql+aiomysql://user:pass@127.0.0.1:3306/maimai_sync
python -m uvicorn maimai_sync.web.app:app --host 0.0.0.0 --port 8765
```

浏览器打开 `http://localhost:8765`，注册账号即可使用。

成绩可通过水鱼下载获取，或通过 CLI 上传本地缓存到水鱼/落雪。

### CLI 模式

```bash
# 注册
python -m maimai_sync a register <用户名> -p <密码>

# 登录
python -m maimai_sync a login <用户名> -p <密码>

# 配置水鱼 Token
python -m maimai_sync a config --df-import <你的ImportToken>

# 从缓存上传到水鱼
python -m maimai_sync upload

# 从水鱼下载成绩
python -m maimai_sync download
```

### 管理后台

```bash
python -m maimai_sync.web.admin_app
# 默认端口 15500
```

## Web API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/auth/login` | POST | 登录 |
| `/api/auth/register` | POST | 注册 |
| `/api/auth/me` | GET | 当前用户信息 |
| `/api/auth/profile` | GET/POST | 查看/修改个人配置（水鱼 Token / 昵称） |
| `/api/sync/upload` | POST | 上传成绩到水鱼 |
| `/api/sync/download` | POST | 从水鱼下载成绩 |
| `/api/scores` | GET | 查询成绩列表 |
| `/api/song/{id}` | GET | 歌曲详情（定数/拟合/谱师/别名/FC分布） |
| `/api/aliases` | GET | 所有歌曲别名（前端搜索用） |
| `/api/leaderboard` | GET | 排行榜 |
| `/api/leaderboard/{username}/b50` | GET | 用户 Best 50 |
| `/api/rating-history` | GET | Rating 变化历史 |

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `MAIMAI_DB_URL` | 数据库连接串（不设则用 JSON 文件模式） | 无 |
| `MAIMAI_WEB_PORT` | Web 服务端口 | 8765 |
| `MAIMAI_ADMIN_PORT` | 管理后台端口 | 15500 |

## 数据库

| 数据库 | 连接串格式 |
|--------|-----------|
| MySQL | `mysql+aiomysql://user:pass@127.0.0.1:3306/maimai_sync` |
| SQLite | `sqlite+aiosqlite:///./maimai_data/maimai.db` |
| 不设置 | JSON 文件模式 |

### 数据库表结构

- **users**: 用户账号、密码哈希、水鱼 Token、昵称、管理员标记、注册/登录时间
- **score_snapshots**: 每次同步的快照元信息（Rating、成绩数、时间、原始上传数据）
- **score_records**: 单条成绩记录（歌曲、达成率、DX分数、FC/FS、定数等）

## 项目结构

```
maimai_sync/
├── __init__.py
├── __main__.py             # CLI 入口
├── config.py               # 全局配置
├── models.py               # 公共数据模型
├── lxns.py                 # 落雪 API 客户端（歌曲/别名）
├── account/                # 账号模块
│   ├── auth.py             # 密码哈希（PBKDF2-SHA256）
│   └── manager.py          # 账号管理（JSON/DB 双后端）
├── db/                     # 数据库模块
│   ├── engine.py           # 连接引擎
│   ├── models.py           # ORM 模型
│   └── crud.py             # CRUD 操作
├── storage/                # 存储模块
│   ├── json_store.py       # JSON 存储
│   └── db_store.py         # 数据库存储
├── uploader/               # 上传模块
│   └── divingfish.py       # 水鱼上传
├── downloader/             # 下载模块
│   └── divingfish.py       # 水鱼下载
├── utils/                  # 工具函数
│   ├── score_merge.py      # 成绩合并/定数计算
│   └── __init__.py         # 时间格式化等
└── web/                    # Web 模块
    ├── app.py              # 主站（端口 8765）
    ├── admin_app.py        # 管理后台（端口 15500）
    └── static/
        ├── index.html      # 主站前端（亮暗主题 + 手机适配）
        └── admin.html      # 管理后台前端
```

## 数据源

| 数据 | 来源 |
|------|------|
| 歌曲元数据（曲师/类别/BPM/谱师/物量） | 落雪 LXNS 公开 API |
| 别名 | 落雪 LXNS 公开 API |
| 拟合定数 / 平均达成率 / FC分布 | 水鱼 chart_stats |
| 曲绘封面 | 水鱼 CDN |
| 官方定数 | 水鱼 music_db.json |
| FC/FS | 自有数据库 |

## 依赖

- Python >= 3.10
- httpx, aiohttp, fastapi, uvicorn, sqlalchemy
- aiomysql（MySQL 模式）
- aiosqlite（SQLite 模式）
- pytz（时区处理）

## 部署

详见 [deploy.sh](deploy.sh)（Ubuntu 24 + 宝塔面板 + MySQL）和 [DEPLOY.md](DEPLOY.md)。

## License

MIT
