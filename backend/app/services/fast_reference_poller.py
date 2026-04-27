"""
快速参考视频生成 — 视频轮询器
"""

import asyncio
import hashlib
import logging
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlencode, urlparse

import httpx

from app.core.config import settings, BASE_DIR

logger = logging.getLogger(__name__)

REGION_PROFILES = [
    {"region": "TW", "name": "Taiwan"},
    {"region": "HK", "name": "Hong Kong"},
    {"region": "TH", "name": "Thailand"},
]

API_BASE = "https://mweb-api-sg.capcut.com"
APP_ID = 513641
WEB_VERSION = "7.5.0"
DA_VERSION = "3.3.12"
APP_VERSION = "8.4.0"

STATUS_SUCCESS = {10, 50}
STATUS_FAILED = {30}


def _generate_did() -> str:
    return "".join([str(random.randint(0, 9)) for _ in range(19)])


def _build_api_url(path: str, region: str) -> str:
    did = _generate_did()
    params = {
        "aid": APP_ID,
        "device_platform": "web",
        "region": region,
        "did": did,
        "da_version": DA_VERSION,
        "os": "windows",
        "web_component_open_flag": "1",
        "commerce_with_input_video": "1",
        "web_version": WEB_VERSION,
    }
    return f"{API_BASE}{path}?{urlencode(params)}"


def _sign_request(url: str) -> Dict[str, str]:
    pathname = urlparse(url).path
    device_time = str(int(time.time()))
    raw = f"9e2c|{pathname}|web|{APP_VERSION}|{device_time}||1e67"
    sign_value = hashlib.md5(raw.encode()).hexdigest()
    return {
        "sign": sign_value,
        "sign-ver": "1",
        "device-time": device_time,
    }


@dataclass
class PollResult:
    success: bool = False
    video_url: Optional[str] = None
    status_code: Optional[int] = None
    error: Optional[str] = None
    region_used: Optional[str] = None
    local_path: Optional[str] = None


class FastReferencePoller:

    def __init__(self, session_id: str, proxy_url: Optional[str] = None):
        self.session_id = session_id
        self.proxy_url = proxy_url

    async def poll_until_done(
        self,
        task_id: str,
        max_polls: int = 60,
        interval: int = 0,
        regions: Optional[List[str]] = None,
    ) -> PollResult:
        if interval <= 0:
            interval = settings.fast_poll_interval
        if regions is None:
            regions = [r["region"] for r in REGION_PROFILES]

        for attempt in range(max_polls):
            result = await self._poll_once(task_id, regions)

            if result.success and result.video_url:
                logger.info(
                    f"[Poller] video ready after {attempt + 1} polls "
                    f"(region={result.region_used})"
                )
                return result

            if result.status_code and result.status_code in STATUS_FAILED:
                logger.warning(f"[Poller] generation failed: status={result.status_code}")
                return PollResult(
                    success=False,
                    status_code=result.status_code,
                    error="remote generation failed",
                    region_used=result.region_used,
                )

            await asyncio.sleep(interval)

        return PollResult(success=False, error=f"timeout after {max_polls} polls")

    async def _poll_once(
        self, task_id: str, regions: List[str]
    ) -> PollResult:
        for region in regions:
            url = _build_api_url("/mweb/v1/get_history_by_ids", region)
            sign_headers = _sign_request(url)

            headers = {
                "Cookie": f"sessionid={self.session_id}",
                "Content-Type": "application/json",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                "Origin": "https://dreamina.capcut.com",
                "Referer": "https://dreamina.capcut.com/",
                "app-sdk-version": "48.0.0",
                "appid": str(APP_ID),
                "appvr": APP_VERSION,
                "pf": "7",
                **sign_headers,
            }

            payload = {
                "history_ids": [task_id],
                "submit_ids": [task_id],
            }

            try:
                async with httpx.AsyncClient(
                    timeout=30.0, proxy=self.proxy_url
                ) as client:
                    resp = await client.post(url, json=payload, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()

                if data.get("ret") == "0" or data.get("status_code") == 0:
                    video_url, status_code = self._extract_result(data)
                    if video_url:
                        return PollResult(
                            success=True,
                            video_url=video_url,
                            status_code=status_code,
                            region_used=region,
                        )
                    return PollResult(
                        success=False,
                        status_code=status_code,
                        region_used=region,
                    )
            except Exception as e:
                logger.debug(f"[Poller] region {region} failed: {e}")
                continue

        return PollResult(success=False, error="all regions failed")

    @staticmethod
    def _extract_result(data: dict) -> tuple:
        try:
            histories = data.get("data", {}).get("histories", [])
            if not histories:
                return None, None

            history = histories[0]
            item_list = history.get("item_list", [])
            status = history.get("status")

            if not item_list:
                return None, status

            finish_time = history.get("finish_time", 0)
            if not finish_time:
                return None, status

            video_item = item_list[0]
            video_data = video_item.get("video", {})

            transcoded = video_data.get("transcoded_video", {})
            origin = transcoded.get("origin", {})
            video_url = origin.get("video_url")

            if not video_url:
                video_url = video_data.get("video_url")

            if not video_url:
                video_url = video_data.get("play_url")

            return video_url, status
        except (KeyError, IndexError, TypeError) as e:
            logger.warning(f"[Poller] extract failed: {e}")
            return None, None

    @staticmethod
    async def download_video(
        video_url: str, dest_name: Optional[str] = None
    ) -> Optional[str]:
        out_dir = BASE_DIR / "data" / "outputs" / "fast_reference"
        out_dir.mkdir(parents=True, exist_ok=True)

        if not dest_name:
            dest_name = f"fast_{int(time.time())}_{random.randint(1000,9999)}.mp4"

        dest_path = out_dir / dest_name

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.get(video_url)
                resp.raise_for_status()
                dest_path.write_bytes(resp.content)
            logger.info(f"[Poller] video downloaded: {dest_path}")
            return str(dest_path.relative_to(BASE_DIR)).replace("\\", "/")
        except Exception as e:
            logger.error(f"[Poller] download failed: {e}")
            return None
