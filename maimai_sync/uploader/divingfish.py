"""水鱼查分器上传"""

import logging

import httpx

from maimai_sync.config import DIVINGFISH_UPLOAD_URL, DEFAULT_TIMEOUT

logger = logging.getLogger(__name__)


class DivingFishUploader:
    """上传成绩到水鱼查分器

    通过 Import Token 认证，将成绩数据 POST 给水鱼 API。

    Usage::

        uploader = DivingFishUploader()
        ok = await uploader.upload(upload_data, "your_import_token")
    """

    def __init__(self, timeout: int = DEFAULT_TIMEOUT):
        """
        Args:
            timeout: 请求超时时间（秒）
        """
        self.timeout = timeout

    async def upload(self, scores_upload: list[dict], import_token: str) -> bool:
        """上传成绩到水鱼

        Args:
            scores_upload: 水鱼 API 格式的成绩列表（来自 JsonScoreStorage 缓存）
            import_token: 水鱼 Import Token

        Returns:
            是否上传成功
        """
        if not scores_upload:
            logger.warning("没有可上传的成绩")
            return False

        logger.info("上传到水鱼: %d 条成绩, token=%s...",
                     len(scores_upload), import_token[:8])

        headers = {"Import-Token": import_token}

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                DIVINGFISH_UPLOAD_URL,
                headers=headers,
                json=scores_upload,
            )

        if resp.status_code == 200:
            logger.info("上传成功")
            return True
        else:
            logger.error("上传失败: HTTP %d - %s", resp.status_code, resp.text[:200])
            return False
