"""配置常量"""

from pathlib import Path

# 默认数据存储根目录
DEFAULT_DATA_DIR = Path.cwd() / "maimai_data"

# 水鱼 API 地址
DIVINGFISH_API_BASE = "https://www.diving-fish.com/api/maimaidxprober"
DIVINGFISH_UPLOAD_URL = f"{DIVINGFISH_API_BASE}/player/update_records"
DIVINGFISH_DOWNLOAD_URL = f"{DIVINGFISH_API_BASE}/player/records"

# 默认请求超时（秒）
DEFAULT_TIMEOUT = 30

# 账号数据库文件名（存储在 DEFAULT_DATA_DIR 下）
ACCOUNTS_FILENAME = "accounts.json"
