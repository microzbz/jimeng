"""
FastReferenceBrowserExecutor — 浏览器自动化提交视频生成任务
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from patchright.async_api import async_playwright, Page, BrowserContext, Browser, Playwright, Response

from app.core.config import settings
from app.services.browser_stealth import BrowserStealth
from app.services.human_behavior import HumanBehavior

logger = logging.getLogger(__name__)

TARGET_URL = "https://dreamina.capcut.com/ai-tool/video/generate"
GENERATE_INTERCEPT = "/mweb/v1/aigc_draft/generate"
MENTION_RE = re.compile(r"@[A-Za-z0-9_\-一-鿿]+")


@dataclass
class FastReferenceResult:
    success: bool = False
    history_id: Optional[str] = None
    task_id: Optional[str] = None
    error: Optional[str] = None
    browser_session_log: str = ""
    submitted_evidence: bool = False


class FastReferenceBrowserExecutor:

    def __init__(
        self,
        session_id: str,
        prompt: str,
        region: Optional[str] = None,
        reference_files: Optional[List[str]] = None,
        proxy_url: Optional[str] = None,
    ):
        self.session_id = session_id
        self.prompt = prompt
        self.region = region
        self.reference_files = reference_files or []
        self.proxy_url = proxy_url
        self._log_lines: list = []
        self._captured_history_id: Optional[str] = None
        self._captured_task_id: Optional[str] = None

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        self._log_lines.append(line)
        logger.info("FastRef: %s", msg)

    async def execute(self) -> FastReferenceResult:
        pw: Optional[Playwright] = None
        browser: Optional[Browser] = None
        context: Optional[BrowserContext] = None
        page: Optional[Page] = None
        result = FastReferenceResult()

        try:
            proxy = None
            if self.proxy_url:
                proxy = {"server": self.proxy_url}

            pw = await async_playwright().start()
            stealth = BrowserStealth(pw)

            context = await stealth.create_context(
                region_tag=self.region,
                proxy=proxy,
                headless=settings.fast_headless,
            )
            browser = context.browser

            await self._inject_cookies(context)
            page = await context.new_page()
            human = HumanBehavior(page)
            self._setup_interceptor(page)

            self._log(f"Navigating to {TARGET_URL}")
            await page.goto(TARGET_URL, wait_until="networkidle", timeout=30000)

            await BrowserStealth.dismiss_error_modal(page)
            await human.close_popup_if_exists()

            if self.reference_files:
                await self._upload_references(page)

            await self._fill_prompt(page, human)
            await self._click_generate(page, human)

            history_id = await self._wait_for_task_id()

            if history_id:
                result.success = True
                result.history_id = self._captured_history_id
                result.task_id = self._captured_task_id
                self._log(f"Task captured: history_id={history_id}")
            else:
                has_evidence = await self._detect_submission_evidence(page)
                result.submitted_evidence = has_evidence
                if has_evidence:
                    result.error = "ambiguous_submission"
                    self._log("Submission evidence detected but no history_id captured")
                else:
                    result.error = "no_task_id_captured"
                    self._log("No submission evidence, task likely not submitted")

        except asyncio.TimeoutError:
            result.error = "browser_timeout"
            self._log("Browser execution timed out")
        except Exception as exc:
            result.error = str(exc)
            self._log(f"Error: {exc}")
            if page:
                try:
                    await page.screenshot(
                        path=f"data/screenshots/fast_ref_error_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
                    )
                except Exception:
                    pass
        finally:
            for closeable in [page, context, browser]:
                if closeable:
                    try:
                        await asyncio.wait_for(closeable.close(), 5)
                    except Exception:
                        pass
            if pw:
                try:
                    await pw.stop()
                except Exception:
                    pass
            result.browser_session_log = "\n".join(self._log_lines)

        return result

    async def _inject_cookies(self, context: BrowserContext):
        self._log("Injecting session cookie")
        await context.add_cookies(
            [
                {
                    "name": "sessionid",
                    "value": self.session_id,
                    "domain": ".capcut.com",
                    "path": "/",
                    "httpOnly": True,
                    "secure": True,
                    "sameSite": "None",
                }
            ]
        )

    def _setup_interceptor(self, page: Page):
        async def on_response(response: Response):
            if GENERATE_INTERCEPT not in response.url:
                return
            try:
                data = await response.json()
                body = data.get("data", {})
                history_id = body.get("history_record_id")
                if not history_id:
                    task = body.get("aigc_data", {}).get("task", {})
                    history_id = task.get("submit_id")
                if history_id:
                    self._captured_history_id = str(history_id)
                    self._log(f"Intercepted history_id: {history_id}")
                task_data = body.get("aigc_data", {}).get("task", {})
                if task_data.get("task_id"):
                    self._captured_task_id = str(task_data["task_id"])
            except Exception as exc:
                self._log(f"Interceptor parse error: {exc}")

        page.on("response", on_response)

    async def _upload_references(self, page: Page):
        self._log(f"Uploading {len(self.reference_files)} reference files")
        try:
            file_input = await page.wait_for_selector(
                "input[type=file]", timeout=10000
            )
            if file_input:
                await file_input.set_input_files(self.reference_files)
                await page.wait_for_timeout(2000)
                self._log("Reference files uploaded")
        except Exception as exc:
            self._log(f"Upload failed: {exc}")

    async def _fill_prompt(self, page: Page, human: HumanBehavior):
        clean_prompt = MENTION_RE.sub("", self.prompt).strip()
        clean_prompt = re.sub(r"\s{2,}", " ", clean_prompt)
        if not clean_prompt:
            clean_prompt = self.prompt
        self._log("Filling prompt")
        selectors = [
            'textarea[placeholder*="describe"]',
            'textarea[class*="prompt"]',
            '[contenteditable="true"][class*="prompt"]',
            "textarea",
        ]
        for sel in selectors:
            try:
                el = await page.wait_for_selector(sel, timeout=5000)
                if el:
                    await el.click()
                    await human.type_like_human(sel, clean_prompt)
                    self._log(f"Prompt filled via {sel}")
                    return
            except Exception:
                continue
        raise Exception("Could not find prompt input element")

    async def _click_generate(self, page: Page, human: HumanBehavior):
        self._log("Clicking generate button")
        selectors = [
            'button:has-text("Generate")',
            'button:has-text("生成")',
            'button[class*="generate"]',
        ]
        for sel in selectors:
            try:
                btn = await page.wait_for_selector(sel, timeout=5000)
                if btn and await btn.is_enabled():
                    await human.click_like_human(sel)
                    self._log(f"Generate clicked via {sel}")
                    return
            except Exception:
                continue
        raise Exception("Could not find or click generate button")

    async def _wait_for_task_id(self, timeout: float = 15.0) -> Optional[str]:
        self._log("Waiting for task_id from interceptor")
        elapsed = 0.0
        while elapsed < timeout:
            if self._captured_history_id:
                return self._captured_history_id
            await asyncio.sleep(0.5)
            elapsed += 0.5
        return None

    async def _detect_submission_evidence(self, page: Page) -> bool:
        try:
            loading = await page.query_selector(
                'button[class*="generate"][disabled], button[class*="loading"]'
            )
            return loading is not None
        except Exception:
            return False
