"""
Dreamina Auto Register - CF Mail Worker 验证码获取服务
通过 CF Mail Worker 的 HTTP API 创建邮箱、轮询邮件、提取验证码
"""
import asyncio
import re
import httpx
from typing import Optional, Dict, Any, Set
from datetime import datetime
from app.core.config import settings
import logging

logger = logging.getLogger(__name__)

# 验证码提取正则: 6 位大写字母+数字
CODE_PATTERN_SUBJECT = re.compile(r"\b([A-Z0-9]{6})\b")
CODE_PATTERN_BODY = re.compile(r"[A-Za-z0-9]{6}")


class CloudflareMailClient:
    """CF Mail Worker HTTP 客户端"""

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        # 邮箱 JWT 缓存: email -> jwt
        self._jwt_cache: Dict[str, str] = {}
        # 聚合轮询相关
        self._pending_emails: Set[str] = set()
        self._results_cache: Dict[str, str] = {}  # email -> code
        self._poller_task: Optional[asyncio.Task] = None
        self._notify_events: Dict[str, asyncio.Event] = {}
        self._lock = asyncio.Lock()

    @property
    def base_url(self) -> str:
        url = (settings.cf_mail_worker_url or "").rstrip("/")
        return url

    @property
    def admin_password(self) -> str:
        return settings.cf_mail_admin_password or ""

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and self.admin_password)

    async def get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    # ── Mailbox Management ──────────────────────────────────

    async def create_mailbox(self, email: str) -> Optional[str]:
        """
        在 CF Mail Worker 上创建邮箱，返回 JWT。
        如果已有缓存 JWT 则直接返回。
        """
        if email in self._jwt_cache:
            return self._jwt_cache[email]

        local_part, domain = email.rsplit("@", 1)
        client = await self.get_client()

        try:
            resp = await client.post(
                f"{self.base_url}/admin/new_address",
                json={"name": local_part, "domain": domain},
                headers={
                    "Content-Type": "application/json",
                    "x-admin-auth": self.admin_password,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                jwt = data.get("jwt")
                if jwt:
                    self._jwt_cache[email] = jwt
                    logger.info(f"邮箱已创建: {email}")
                    return jwt
                else:
                    logger.error(f"创建邮箱成功但未返回 JWT: {data}")
            else:
                logger.error(f"创建邮箱失败 [{resp.status_code}]: {resp.text}")
        except Exception as e:
            logger.error(f"创建邮箱异常: {e}")

        return None

    # ── Mail Polling ────────────────────────────────────────

    async def _fetch_mails(self, email: str) -> Optional[list]:
        """获取邮箱中的邮件列表"""
        jwt = self._jwt_cache.get(email)
        if not jwt:
            return None

        client = await self.get_client()
        try:
            resp = await client.get(
                f"{self.base_url}/api/mails",
                params={"limit": 5},
                headers={"Authorization": f"Bearer {jwt}"},
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("results", [])
            elif resp.status_code == 401:
                logger.warning(f"JWT 已过期: {email}, 尝试重新创建")
                self._jwt_cache.pop(email, None)
                new_jwt = await self.create_mailbox(email)
                if new_jwt:
                    resp2 = await client.get(
                        f"{self.base_url}/api/mails",
                        params={"limit": 5},
                        headers={"Authorization": f"Bearer {new_jwt}"},
                    )
                    if resp2.status_code == 200:
                        return resp2.json().get("results", [])
            else:
                logger.warning(f"获取邮件失败 [{resp.status_code}]: {resp.text}")
        except Exception as e:
            logger.error(f"获取邮件异常 ({email}): {e}")

        return None

    @staticmethod
    def _extract_code_from_mail(mail: dict) -> Optional[str]:
        """从单封邮件中提取 6 位验证码"""
        # 优先从 subject 提取
        subject = mail.get("subject", "")
        match = CODE_PATTERN_SUBJECT.search(subject)
        if match:
            return match.group(1)

        # 从 raw 正文提取
        raw = mail.get("raw", "")
        # 先尝试精确匹配大写
        match = CODE_PATTERN_SUBJECT.search(raw)
        if match:
            return match.group(1)

        # 兜底: 任意大小写 6 字符
        all_matches = CODE_PATTERN_BODY.findall(raw)
        if all_matches:
            return all_matches[0].upper()

        return None

    async def _check_email_for_code(self, email: str) -> Optional[str]:
        """检查某个邮箱是否收到包含验证码的邮件"""
        mails = await self._fetch_mails(email)
        if not mails:
            return None

        for mail in mails:
            code = self._extract_code_from_mail(mail)
            if code:
                return code

        return None

    # ── Aggregated Poller ───────────────────────────────────

    async def _poller_loop(self):
        """后台聚合轮询循环"""
        logger.info("CF Mail 聚合轮询器已启动")
        while True:
            try:
                if not self._pending_emails:
                    await asyncio.sleep(1.0)
                    continue

                current_emails = list(self._pending_emails)

                for email in current_emails:
                    try:
                        code = await self._check_email_for_code(email)
                        if code:
                            self._results_cache[email] = code
                            if email in self._notify_events:
                                self._notify_events[email].set()
                            async with self._lock:
                                self._pending_emails.discard(email)
                            logger.info(f"聚合轮询: 成功获取验证码 {email} -> {code}")
                    except Exception as e:
                        logger.error(f"轮询邮箱 {email} 失败: {e}")

            except Exception as e:
                logger.error(f"Mail 轮询循环异常: {e}")

            await asyncio.sleep(settings.kv_poll_interval)

    async def start_poller(self):
        if self._poller_task is None or self._poller_task.done():
            self._poller_task = asyncio.create_task(self._poller_loop())

    async def stop_poller(self):
        if self._poller_task:
            self._poller_task.cancel()
            try:
                await self._poller_task
            except (asyncio.CancelledError, Exception):
                pass
            self._poller_task = None

    async def poll_verification_code(
        self, email: str, timeout: Optional[int] = None
    ) -> Optional[str]:
        """
        等待验证码 (聚合版)
        调用前必须先 create_mailbox() 确保邮箱和 JWT 存在。
        """
        if timeout is None:
            timeout = settings.kv_poll_timeout

        # 确保邮箱已创建 (幂等)
        if email not in self._jwt_cache:
            jwt = await self.create_mailbox(email)
            if not jwt:
                logger.error(f"无法为 {email} 创建邮箱，放弃轮询")
                return None

        event = asyncio.Event()
        async with self._lock:
            self._pending_emails.add(email)
            self._notify_events[email] = event
            await self.start_poller()

        try:
            if email in self._results_cache:
                return self._results_cache.pop(email)

            await asyncio.wait_for(event.wait(), timeout=timeout)
            return self._results_cache.pop(email, None)

        except asyncio.TimeoutError:
            logger.warning(f"验证码等待超时: {email}")
            async with self._lock:
                self._pending_emails.discard(email)
                self._notify_events.pop(email, None)
            return None

    # ── Lifecycle ───────────────────────────────────────────

    async def test_connection(self) -> Dict[str, Any]:
        """测试 Worker 连接"""
        try:
            client = await self.get_client()
            resp = await client.get(f"{self.base_url}/healthz")
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "success": True,
                    "message": f"Worker 连接正常, domain={data.get('emailDomain')}",
                }
            else:
                return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def close(self):
        """关闭客户端"""
        await self.stop_poller()
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        self._jwt_cache.clear()


# 全局实例 (保持 cf_kv_client 名称以兼容现有 import)
cf_kv_client = CloudflareMailClient()
