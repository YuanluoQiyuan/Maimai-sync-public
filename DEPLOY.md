# maimai-sync 部署指南

## 环境要求

- Ubuntu 24.04 LTS
- 宝塔面板（已安装）
- MySQL 5.7+ 或 8.0（宝塔一键安装）
- Python 3.10+
- 域名（可选，推荐用于 HTTPS）

## 一键部署

```bash
# 1. 克隆仓库到本地
git clone https://github.com/your-username/maimai-sync.git
cd maimai-sync

# 2. 上传项目到服务器
scp -r . root@your-server:/www/wwwroot/maimai-sync

# 3. SSH 登录服务器
ssh root@your-server

# 4. 运行部署脚本
cd /www/wwwroot/maimai-sync
bash deploy.sh
```

## 手动部署步骤

如果一键脚本不适用，按以下步骤操作：

### 1. 宝塔安装 MySQL

宝塔面板 → 软件商店 → MySQL 8.0 → 安装

### 2. 创建数据库

宝塔面板 → 数据库 → 添加数据库
- 数据库名：`maimai_sync`
- 用户名：`maimai`
- 密码：自动生成（记下来）

### 3. 安装 Python

```bash
apt update
apt install -y python3.12 python3.12-venv python3-pip
```

### 4. 部署项目

```bash
cd /www/wwwroot
git clone https://github.com/your-username/maimai-sync.git
cd maimai-sync

# 虚拟环境（可选）
python3 -m venv venv
source venv/bin/activate

# 安装依赖
pip install -e .
pip install aiomysql uvicorn
```

### 5. 配置环境变量

```bash
cat > .env <<EOF
MAIMAI_DB_URL=mysql+aiomysql://maimai:你的密码@127.0.0.1:3306/maimai_sync
MAIMAI_WEB_PORT=8765
MAIMAI_ADMIN_PORT=15500
EOF
```

### 6. 初始化数据库表

```bash
source .env
python3 -c "
import os, asyncio
from maimai_sync.db.engine import init_db
asyncio.run(init_db())
print('数据库表创建完成')
"
```

### 7. 创建 systemd 服务

主 Web 服务 (端口 8765)：

```bash
cat > /etc/systemd/system/maimai-sync.service <<EOF
[Unit]
Description=maimai-sync Web
After=network.target mysql.service

[Service]
Type=simple
User=root
WorkingDirectory=/www/wwwroot/maimai-sync
EnvironmentFile=/www/wwwroot/maimai-sync/.env
ExecStart=python3 -m uvicorn maimai_sync.web.app:app --host 0.0.0.0 --port 8765
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

管理后台 (端口 15500)：

```bash
cat > /etc/systemd/system/maimai-sync-admin.service <<EOF
[Unit]
Description=maimai-sync Admin Panel
After=network.target mysql.service

[Service]
Type=simple
User=root
WorkingDirectory=/www/wwwroot/maimai-sync
EnvironmentFile=/www/wwwroot/maimai-sync/.env
ExecStart=python3 -m uvicorn maimai_sync.web.admin_app:admin_app --host 0.0.0.0 --port 15500
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable maimai-sync maimai-sync-admin
systemctl start maimai-sync maimai-sync-admin
```

### 8. 宝塔配置域名 + HTTPS + 反代

1. **网站** → 添加站点 → 输入域名 → 提交
2. **网站** → 点击域名 → SSL → Let's Encrypt → 申请证书
3. **网站** → 点击域名 → 反向代理 → 添加反向代理：
   - 代理名称：`maimai-sync`
   - 目标URL：`http://127.0.0.1:8765`
   - 发送域名：`$host`

### 9. 验证

浏览器访问 `https://你的域名` → 注册账号 → 使用

## 本机开发

不设置 `MAIMAI_DB_URL` 时，自动使用 SQLite（零配置）：

```bash
pip install -e .
python -m uvicorn maimai_sync.web.app:app --port 8765
```

数据存储在 `maimai_data/maimai.db`（SQLite）和 `maimai_data/users/`（JSON 兼容）。

## 数据库连接串格式

| 数据库 | 格式 |
|--------|------|
| MySQL | `mysql+aiomysql://user:pass@host:3306/dbname` |
| SQLite | `sqlite+aiosqlite:///./maimai_data/maimai.db` |

## 服务管理

```bash
# 启动
systemctl start maimai-sync
systemctl start maimai-sync-admin

# 停止
systemctl stop maimai-sync
systemctl stop maimai-sync-admin

# 重启
systemctl restart maimai-sync
systemctl restart maimai-sync-admin

# 查看状态
systemctl status maimai-sync
systemctl status maimai-sync-admin

# 查看日志
journalctl -u maimai-sync -f
journalctl -u maimai-sync-admin -f
```

## 更新部署

```bash
cd /www/wwwroot/maimai-sync
git pull
pip install -e .
systemctl restart maimai-sync maimai-sync-admin
```

如果更新涉及数据库表结构变更（如新增列、修改列类型），需要手动执行 SQL：

```bash
mysql -uroot -p maimai_sync -e "ALTER TABLE score_snapshots MODIFY COLUMN upload_data_json MEDIUMTEXT;"
```

## 设置管理员

```bash
cd /www/wwwroot/maimai-sync
source .env
python3 -c "
import os, asyncio
from maimai_sync.db.engine import get_session_factory
from sqlalchemy import select
from maimai_sync.db.models import User

async def f():
    async with get_session_factory()() as s:
        async with s.begin():
            u = (await s.execute(select(User).where(User.username=='你的用户名'))).scalar_one()
            u.is_admin = True
            print('已设置管理员:', u.username)

asyncio.run(f())
"
```

## 常见问题

### Q: 启动报 `ModuleNotFoundError: No module named 'aiomysql'`
A: 安装 MySQL 驱动：`pip install aiomysql`

### Q: 连接 MySQL 报 `Access denied`
A: 检查 `.env` 中的用户名和密码，确认 MySQL 用户有权限访问数据库

### Q: 宝塔反代后 WebSocket 不通
A: 在宝塔反代配置中添加 WebSocket 支持（Nginx 配置加 `proxy_set_header Upgrade`）

### Q: 本机开发用 SQLite，服务器用 MySQL，数据怎么同步？
A: 两边是独立的。本机开发测试用 SQLite，服务器生产用 MySQL。可通过水鱼作为中转：本机上传到水鱼，服务器从水鱼下载。

### Q: 上传时提示 `Data too long for column 'upload_data_json'`
A: 成绩数量较多（>1000条）时，原始 JSON 可能超过 TEXT 类型 65KB 限制。需将 `upload_data_json` 改为 MEDIUMTEXT：
```sql
ALTER TABLE score_snapshots MODIFY COLUMN upload_data_json MEDIUMTEXT;
```

### Q: 从水鱼下载后 Rating 趋势图没有数据
A: 下载功能会将快照和成绩存入数据库，刷新页面后 Rating 趋势图应自动显示。如仍无数据，检查 `score_snapshots` 表是否有记录。
