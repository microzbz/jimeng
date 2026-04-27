"""
快速参考视频生成 — 浏览器自动化执行器
"""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict

from patchright.async_api import async_playwright, BrowserContext, Page, Browser

from app.core.config import settings
from app.services.browser_stealth import BrowserStealth
from app.services.human_behavior import HumanBehavior

logger = logging.getLogger(__name__)


@dataclass
class FastReferenceResult:
    task_id: Optional[str] = None
    history_id: Optional[str] = None
    success: bool = False
    error: Optional[str] = None
    browser_session_log: str = ""


class FastReferenceBrowserExecutor:
    TARGET_URL = "https://dreamina.capcut.com/ai-tool/video/generate"

    def __init__(
        self,
        session_id: str,
        region_tag: str = "TW",
        proxy_config: Optional[Dict[str, str]] = None,
    ):
        self.session_id = session_id
        self.region_tag = region_tag
        self.proxy_config = proxy_config
        self.captured_task_id: Optional[str] = None
        self.captured_history_id: Optional[str] = None
        self._log_lines: List[str] = []

    def _log(self, msg: str):
        self._log_lines.append(msg)
        logger.info(f"[FastRef] {msg}")

    async def execute(
        self,
        prompt: str,
        reference_assets: Optional[List[str]] = None,
        model: str = "Dreamina Seedance 2.0 Fast",
        duration: int = 5,
        resolution: str = "720p",
        ratio: str = "16:9",
    ) -> FastReferenceResult:
        browser: Optional[Browser] = None
        context: Optional[BrowserContext] = None
        page: Optional[Page] = None

        try:
            async with async_playwright() as p:
                stealth = BrowserStealth(p)
                self._log("launching browser")

                context = await stealth.create_context(
                    region_tag=self.region_tag,
                    proxy=self.proxy_config,
                    headless=settings.fast_headless,
                )
                browser = context.browser

                await self._inject_cookies(context)
                self._log("cookies injected")

                page = await stealth.create_page(context)
                human = HumanBehavior(page)

                await self._setup_network_interceptor(page)

                self._log(f"navigating to {self.TARGET_URL}")
                await page.goto(
                    self.TARGET_URL,
                    wait_until="networkidle",
                    timeout=30000,
                )
                await human.random_delay(1000, 2000)

                await BrowserStealth.dismiss_error_modal(page)
                await human.close_popup_if_exists()
                self._log("page loaded, modals dismissed")

                if reference_assets:
                    await self._upload_references(page, human, reference_assets)
                    self._log(f"uploaded {len(reference_assets)} assets")

                await self._fill_prompt(page, human, prompt)
                self._log("prompt filled")

                await self._click_generate(page, human)
                self._log("generate clicked, waiting for task_id")

                await self._wait_for_task_id(timeout=15000)
                self._log(f"captured history_id={self.captured_history_id}")

                return FastReferenceResult(
                    task_id=self.captured_task_id,
                    history_id=self.captured_history_id,
                    success=True,
                    browser_session_log="\n".join(self._log_lines),
                )

        except Exception as e:
            self._log(f"execution failed: {e}")
            if page:
                try:
                    from app.core.config import BASE_DIR
                    ss_path = BASE_DIR / "data" / "screenshots" / f"fast_ref_error_{id(self)}.png"
                    await page.screenshot(path=str(ss_path))
                    self._log(f"error screenshot saved: {ss_path}")
                except Exception:
                    pass
            return FastReferenceResult(
                success=False,
                error=str(e),
                browser_session_log="\n".join(self._log_lines),
            )
        finally:
            try:
                if page and not page.is_closed():
                    await page.close()
            except Exception:
                pass
            try:
                if context:
                    await context.close()
            except Exception:
                pass
            try:
                if browser and browser.is_connected():
                    await browser.close()
            except Exception:
                pass

    async def _inject_cookies(self, context: BrowserContext):
        await context.add_cookies([
            {
                "name": "sessionid",
                "value": self.session_id,
                "domain": ".capcut.com",
                "path": "/",
                "httpOnly": True,
                "secure": True,
                "sameSite": "None",
            }
        ])

    async def _setup_network_interceptor(self, page: Page):
        async def on_response(response):
            if "/mweb/v1/aigc_draft/generate" not in response.url:
                return
            try:
                body = await response.json()
                data = body.get("data", {})
                aigc_data = data.get("aigc_data", {})
                task_node = aigc_data.get("task", {})

                self.captured_history_id = (
                    data.get("history_record_id")
                    or task_node.get("submit_id")
                )
                self.captured_task_id = self.captured_history_id
                self._log(f"intercepted history_id={self.captured_history_id}")
            except Exception as e:
                self._log(f"failed to parse generate response: {e}")

        page.on("response", on_response)

    async def _upload_references(
        self, page: Page, human: HumanBehavior, file_paths: List[str]
    ):
        upload_input = await page.wait_for_selector(
            'input[type="file"][accept*="image"],'
            'input[type="file"][accept*="video"],'
            'input[id*="reference-upload"]',
            state="attached",
            timeout=10000,
        )
        if not upload_input:
            raise Exception("未找到文件上传入口")

        await upload_input.set_input_files(file_paths)
        await human.random_delay(1000, 2000)

        await page.wait_for_selector(
            '[class*="upload-preview"],'
            '[class*="reference-thumb"],'
            'img[class*="uploaded"]',
            state="visible",
            timeout=15000,
        )

    async def _fill_prompt(self, page: Page, human: HumanBehavior, prompt: str):
        selectors = [
            'textarea[placeholder*="describe"]',
            'textarea[placeholder*="Describe"]',
            'textarea[class*="prompt"]',
            '[contenteditable="true"][class*="prompt"]',
            '[data-testid="prompt-input"]',
        ]
        for selector in selectors:
            try:
                el = await page.wait_for_selector(
                    selector, state="visible", timeout=3000
                )
                if el:
                    await human.type_like_human(selector, prompt)
                    return
            except Exception:
                continue
        raise Exception("未找到 prompt 输入框")

    async def _click_generate(self, page: Page, human: HumanBehavior):
        selectors = [
            'button:has-text("Generate")',
            'button:has-text("生成")',
            'button[class*="generate"]',
            '[data-testid="generate-btn"]',
        ]
        for selector in selectors:
            try:
                btn = await page.wait_for_selector(
                    selector, state="visible", timeout=3000
                )
                if btn and await btn.is_enabled():
                    await human.click_like_human(selector)
                    return
            except Exception:
                continue
        raise Exception("未找到可用的生成按钮")

    async def _wait_for_task_id(self, timeout: int = 15000):
        elapsed = 0
        interval = 500
        while elapsed < timeout:
            if self.captured_task_id:
                return
            await asyncio.sleep(interval / 1000)
            elapsed += interval
        raise Exception(f"等待 task_id 超时 ({timeout}ms)")
