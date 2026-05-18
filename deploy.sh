#!/bin/bash
# maimai-sync 宝塔面板部署脚本
# 适用于 Ubuntu 24 + 宝塔面板
# 用法: bash deploy.sh

set -e

echo "========================================="
echo "  maimai-sync 部署脚本"
echo "========================================="

# ---- 配置区 ----
APP_NAME="maimai-sync"
APP_DIR="/www/wwwroot/${APP_NAME}"
WEB_PORT=8765
ADMIN_PORT=15500
DB_NAME="maimai_sync"
DB_USER="maimai"
DB_PASS=""  # 留空则自动生成

# ---- 检查宝塔 ----
if ! command -v bt &>/dev/null; then
    echo "未检测到宝塔面板，请先安装宝塔"
    echo "   安装命令: wget -O install.sh https://download.bt.cn/install/install_lts.sh && sudo bash install.sh edus9n"
    exit 1
fi
echo "已检测到宝塔面板"

# ---- 生成数据库密码 ----
if [ -z "$DB_PASS" ]; then
    DB_PASS=$(openssl rand -base64 16 | tr -d '/+=' | head -c 20)
    echo "已自动生成数据库密码"
fi

# ---- 安装 Python 依赖 ----
echo ""
echo "安装 Python 依赖..."
if ! command -v python3 &>/dev/null; then
    apt update && apt install -y python3 python3-pip
fi
echo "Python: $(python3 --version)"

pip install --break-system-packages -e . -q 2>/dev/null || pip install -e . -q
echo "依赖安装完成"

# ---- 创建数据库 ----
echo ""
echo "创建 MySQL 数据库..."
if command -v mysql &>/dev/null; then
    # 获取宝塔 MySQL root 密码
    BT_MYSQL_PASS=$(cat /www/server/panel/default.db 2>/dev/null | grep -oP 'mysql_root.*?\K\S+' || true)
    if [ -z "$BT_MYSQL_PASS" ]; then
        BT_MYSQL_PASS=$(bt default 2>/dev/null | grep -oP 'password: \K\S+' || true)
    fi

    if [ -n "$BT_MYSQL_PASS" ]; then
        mysql -uroot -p"${BT_MYSQL_PASS}" \
            -e "CREATE DATABASE IF NOT EXISTS \`${DB_NAME}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;" 2>/dev/null && \
        mysql -uroot -p"${BT_MYSQL_PASS}" \
            -e "CREATE USER IF NOT EXISTS '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASS}';" 2>/dev/null && \
        mysql -uroot -p"${BT_MYSQL_PASS}" \
            -e "GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO '${DB_USER}'@'localhost';" 2>/dev/null && \
        mysql -uroot -p"${BT_MYSQL_PASS}" \
            -e "FLUSH PRIVILEGES;" 2>/dev/null
        echo "数据库创建成功"
        echo "   数据库名: ${DB_NAME}"
        echo "   用户名: ${DB_USER}"
        echo "   密码: ${DB_PASS}"
    else
        echo "无法自动获取 MySQL root 密码，请在宝塔面板中手动创建数据库"
        echo "   数据库名: ${DB_NAME}"
    fi
else
    echo "MySQL 未安装，请在宝塔面板中安装 MySQL"
fi

# ---- 部署项目 ----
echo ""
echo "部署项目..."
mkdir -p ${APP_DIR}

if [ -d ".git" ]; then
    if [ -d "${APP_DIR}/.git" ]; then
        cd ${APP_DIR} && git pull 2>/dev/null || true
    else
        cp -r . ${APP_DIR}/
    fi
else
    cp -r . ${APP_DIR}/
fi

# ---- 安装依赖到服务器 ----
cd ${APP_DIR}
pip install --break-system-packages -e . 2>/dev/null || pip install -e .

# ---- 配置环境变量 ----
DB_URL="mysql+aiomysql://${DB_USER}:${DB_PASS}@127.0.0.1:3306/${DB_NAME}"

cat > .env <<EOF
# maimai-sync 环境配置
MAIMAI_DB_URL=${DB_URL}
MAIMAI_WEB_PORT=${WEB_PORT}
MAIMAI_ADMIN_PORT=${ADMIN_PORT}
EOF

echo ".env 已生成"
echo "   MAIMAI_DB_URL=mysql+aiomysql://${DB_USER}:****@127.0.0.1:3306/${DB_NAME}"

# ---- 初始化数据库表 ----
echo ""
echo "初始化数据库..."
python3 -c "
import os, asyncio
os.environ['MAIMAI_DB_URL'] = '${DB_URL}'
from maimai_sync.db.engine import init_db
asyncio.run(init_db())
print('数据库表创建完成')
" 2>/dev/null || echo "数据库初始化跳过（表可能已存在）"

# ---- 创建 systemd 服务 ----
echo ""
echo "创建 systemd 服务..."

# 主 Web 服务
cat > /etc/systemd/system/${APP_NAME}.service <<EOF
[Unit]
Description=Maimai Sync Web Service
After=network.target mysql.service

[Service]
Type=simple
User=root
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=python3 -m uvicorn maimai_sync.web.app:app --host 0.0.0.0 --port ${WEB_PORT}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# 管理后台服务
cat > /etc/systemd/system/${APP_NAME}-admin.service <<EOF
[Unit]
Description=Maimai Sync Admin Panel
After=network.target mysql.service

[Service]
Type=simple
User=root
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=python3 -m uvicorn maimai_sync.web.admin_app:admin_app --host 0.0.0.0 --port ${ADMIN_PORT}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable ${APP_NAME}
systemctl enable ${APP_NAME}-admin
systemctl restart ${APP_NAME}
systemctl restart ${APP_NAME}-admin

# ---- 防火墙 ----
echo ""
echo "配置防火墙..."
if command -v ufw &>/dev/null; then
    ufw allow ${WEB_PORT}/tcp 2>/dev/null || true
    ufw allow ${ADMIN_PORT}/tcp 2>/dev/null || true
    echo "防火墙已放行端口 ${WEB_PORT}, ${ADMIN_PORT}"
fi

# ---- 等待启动 ----
sleep 3

echo ""
if systemctl is-active --quiet ${APP_NAME}; then
    echo "主服务运行中 (端口 ${WEB_PORT})"
else
    echo "主服务启动失败，查看日志: journalctl -u ${APP_NAME} -n 50"
fi

if systemctl is-active --quiet ${APP_NAME}-admin; then
    echo "管理后台运行中 (端口 ${ADMIN_PORT})"
else
    echo "管理后台启动失败，查看日志: journalctl -u ${APP_NAME}-admin -n 50"
fi

# ---- 完成 ----
echo ""
echo "========================================="
echo "  部署完成！"
echo "========================================="
echo ""
echo "  主站:      http://127.0.0.1:${WEB_PORT}"
echo "  管理后台:  http://127.0.0.1:${ADMIN_PORT}"
echo ""
echo "  数据库连接串: ${DB_URL}"
echo ""
echo "  接下来在宝塔面板操作："
echo "  1. 网站 → 添加站点 → 输入域名"
echo "  2. SSL → Let's Encrypt → 申请证书"
echo "  3. 反向代理 → http://127.0.0.1:${WEB_PORT}"
echo ""
echo "  首次使用设置管理员："
echo "  cd ${APP_DIR} && MAIMAI_DB_URL='${DB_URL}' python3 -c \""
echo "  import asyncio; from maimai_sync.db.engine import get_session_factory"
echo "  from sqlalchemy import select; from maimai_sync.db.models import User"
echo "  async def f():"
echo "    async with get_session_factory()() as s:"
echo "      async with s.begin():"
echo "        u = (await s.execute(select(User).where(User.username=='你的用户名'))).scalar_one()"
echo "        u.is_admin = True; print('已设置管理员:', u.username)"
echo "  asyncio.run(f())\""
echo ""
echo "  服务管理:"
echo "  systemctl start|stop|restart ${APP_NAME}"
echo "  systemctl start|stop|restart ${APP_NAME}-admin"
echo "  journalctl -u ${APP_NAME} -f"
echo ""
