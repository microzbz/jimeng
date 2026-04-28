"""
FastReferencePoller — 视频生成状态轮询与下载
"""

import hashlib
import logging
import random
import time
from pathlib import Path
from typing import Optional, Tuple

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

BASE_URL = "https://mweb-api-sg.capcut.com"
POLL_PATH = "/mweb/v1/get_history_by_ids"
AID = "513641"
PLATFORM_CODE = "7"
VERSION_CODE = "8.4.0"


def _sign_11ac(pathname: str) -> Tuple[str, str]:
    device_time = str(int(time.time()))
    raw = f"9e2c|{pathname[-7:]}|{PLATFORM_CODE}|{VERSION_CODE}|{device_time}||11ac"
    sign = hashlib.md5(raw.encode()).hexdigest()
    return sign, device_time


def _sign_1e67(pathname: str) -> Tuple[str, str]:
    device_time = str(int(time.time()))
    raw = f"9e2c|{pathname}|web|{VERSION_CODE}|{device_time}||1e67"
    sign = hashlib.md5(raw.encode()).hexdigest()
    return sign, device_time


class FastReferencePoller:

    @staticmethod
    async def poll_video_status(
        session_id: str,
        history_id: str,
        region: str = "SG",
    ) -> Optional[dict]:
        did = str(random.randint(10**18, 10**19 - 1))
        params = {
            "aid": AID,
            "device_platform": "web",
            "region": region,
            "did": did,
        }

        sign, device_time = _sign_11ac(POLL_PATH)
        headers = {
            "Content-Type": "application/json",
            "Cookie": f"sessionid={session_id}",
            "Device-Time": device_time,
            "Sign": sign,
            "Sign-Ver": "1",
            "Origin": "https://dreamina.capcut.com",
            "Referer": "https://dreamina.capcut.com",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
            "Appvr": VERSION_CODE,
            "Pf": PLATFORM_CODE,
        }

        body = {
            "history_ids": [history_id],
            "submit_ids": [history_id],
        }

        url = f"{BASE_URL}{POLL_PATH}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.post(
                    url, json=body, params=params, headers=headers
                )
                resp.raise_for_status()
                data = resp.json()
                result = FastReferencePoller._extract_result(data)
                if result is not None:
                    return result
                logger.warning("11ac poll returned empty, trying 1e67")
            except Exception as exc:
                logger.warning("11ac poll failed, trying 1e67: %s", exc)

            sign2, device_time2 = _sign_1e67(POLL_PATH)
            headers["Sign"] = sign2
            headers["Device-Time"] = device_time2
            try:
                resp = await client.post(
                    url, json=body, params=params, headers=headers
                )
                resp.raise_for_status()
                data = resp.json()
                return FastReferencePoller._extract_result(data)
            except Exception as exc:
                logger.error("1e67 poll also failed: %s", exc)
                return None

    @staticmethod
    def _extract_result(data: dict) -> Optional[dict]:
        history_list = data.get("data", {}).get("history_list", [])
        if not history_list:
            return None

        item = history_list[0]
        finish_time = item.get("finish_time", 0)
        status = item.get("status", 0)

        if status == 30:
            return {"status": "failed", "error": "remote_generation_failed"}

        if finish_time == 0 and status not in (10, 50):
            return {"status": "processing"}

        item_list = item.get("item_list", [])
        if not item_list:
            return {"status": "processing"}

        video_item = item_list[0]
        video_url = None

        try:
            video_url = (
                video_item.get("video", {})
                .get("transcoded_video", {})
                .get("origin", {})
                .get("video_url")
            )
        except Exception:
            pass

        if not video_url:
            try:
                video_url = video_item.get("video", {}).get("video_url")
            except Exception:
                pass

        if video_url:
            return {"status": "success", "video_url": video_url}

        return {"status": "processing"}

    @staticmethod
    async def poll_with_region_degradation(
        session_id: str,
        history_id: str,
        primary_region: Optional[str] = None,
    ) -> Tuple[Optional[dict], Optional[str]]:
        regions = []
        if primary_region:
            regions.append(primary_region.upper())
        for r in ["TW", "HK", "TH", "SG"]:
            if r not in regions:
                regions.append(r)

        for region in regions:
            result = await FastReferencePoller.poll_video_status(
                session_id, history_id, region
            )
            if result and result.get("status") != "processing":
                return result, region
            if result:
                return result, region

        return None, None

    @staticmethod
    async def download_video(
        video_url: str, job_id: int
    ) -> Optional[str]:
        output_dir = Path(settings.data_dir) / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"job_{job_id}_0.mp4"

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.get(video_url)
                resp.raise_for_status()
                output_path.write_bytes(resp.content)
                logger.info("Video downloaded: %s", output_path)

            try:
                import imageio.v3 as iio
                from PIL import Image

                frames = iio.imread(str(output_path), plugin="pyav")
                if len(frames) > 0:
                    thumb = Image.fromarray(frames[0])
                    thumb.thumbnail((320, 320))
                    thumb_path = output_dir / f"job_{job_id}_thumb.jpg"
                    thumb.save(str(thumb_path), "JPEG", quality=80)
            except Exception as exc:
                logger.warning("Thumbnail extraction failed: %s", exc)

            return str(output_path)
        except Exception as exc:
            logger.error("Video download failed: %s", exc)
            return None
