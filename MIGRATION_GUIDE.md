# 快速参考视频生成 (Fast Reference Video Generation) 实施指南

## 1. 概述

### 1.1 背景

即梦 (Dreamina/CapCut) 国际版的 API 逆向接口持续收紧，现有 `jimeng_service` 通过 Node.js 中间件调用的方式面临签名算法频繁变更、风控升级等问题。为保证视频生成能力的持续可用性，需要引入**浏览器自动化**方案作为补充路径。

### 1.2 功能定义

"快速参考视频生成" (Fast Reference) 是一种通过 Patchright 浏览器自动化直接操作 Dreamina Web 端完成视频生成的模式。核心流程：

1. 注入已有账号的 `session_id` Cookie 到浏览器
2. 导航到 Dreamina 视频生成页面
3. 上传参考素材 + 填写 prompt
4. 拦截网络请求捕获 `task_id` / `history_id`
5. 通过直接 HTTP 轮询获取生成结果视频 URL

### 1.3 与现有系统的关系

本功能**不是**独立系统，而是 `ContentGenerationService` 的一个新 `function_mode`：

```
ContentGenerationJob.function_mode = "fast_reference"
```

复用现有的：
- `Account` 模型（`gen_enabled` / `gen_locked_until` / `gen_auto_disabled_reason`）
- `ContentGenerationJob` 模型（扩展字段）
- `ContentGenerationService` 的 Worker 池和队列
- `BrowserStealth` + `HumanBehavior` + `ProxyPoolManager`

---

## 2. 架构设计

### 2.1 集成架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                     ContentGenerationService                     │
│                                                                  │
│  Queue ──► Worker Pool (asyncio)                                │
│              │                                                   │
│              ├── function_mode != "fast_reference"               │
│              │     └──► JimengClient (现有 API 路径)             │
│              │                                                   │
│              └── function_mode == "fast_reference"               │
│                    │                                             │
│                    ▼                                             │
│           ┌─────────────────────┐                               │
│           │ FastReferenceBrowser │◄── asyncio.Semaphore(N)      │
│           │     Executor        │    (FAST_MAX_BROWSERS)        │
│           └────────┬────────────┘                               │
│                    │                                             │
│         ┌──────────┼──────────┐                                 │
│         ▼          ▼          ▼                                  │
│   BrowserStealth  HumanBehavior  ProxyPool                     │
│   (Patchright)    (鼠标/键盘)    (Mihomo)                       │
│         │                                                        │
│         ▼                                                        │
│   Dreamina Web ──► 网络拦截 ──► task_id/history_id              │
│                                                                  │
│   FastReferencePoller ──► /mweb/v1/get_history_by_ids           │
│         │                    (双签名策略: 11ac → 1e67)           │
│         ▼                                                        │
│   video_url ──► 下载 ──► 本地存储                                │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 模块职责

| 模块 | 文件路径 | 职责 |
|------|----------|------|
| `FastReferenceBrowserExecutor` | `services/fast_reference_executor.py` | 浏览器自动化：Cookie 注入、页面操作、网络拦截、task_id 捕获 |
| `FastReferencePoller` | `services/fast_reference_poller.py` | 视频轮询：双签名策略、区域降级、视频 URL 提取 |
| `ReferenceAssetService` | `services/reference_asset_service.py` | 素材库 CRUD、@mention 解析、别名管理 |
| `ContentGenerationService` (扩展) | `services/content_generation.py` | 新增 `_run_fast_reference_job()` 分发 |
| `FastReference.tsx` | `frontend/src/pages/FastReference.tsx` | 独立前端页面 |

### 2.3 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 账号模型 | 复用 `Account` | 避免数据冗余，`gen_enabled` / `gen_locked_until` 已有完整池管理 |
| 任务模型 | 复用 `ContentGenerationJob` | `function_mode` 字段已存在，扩展字段即可 |
| 调度器 | 扩展 `ContentGenerationService` | 避免重复实现 Worker 池、队列、轮询 |
| 素材存储 | DB 表 (非 JSON 文件) | 支持搜索、别名、关联查询，比 ShukeAI 的 `library.json` 更可靠 |
| 浏览器并发 | `asyncio.Semaphore` | 浏览器实例资源密集，需独立于 Worker 数量控制 |
| 账号消费策略 | 可配置 | `reusable` / `one_time` / `disable_on_low_credit` 三种模式 |

---

## 3. 数据模型

### 3.1 新增表：`reference_assets` (参考素材库)

```python
class ReferenceAsset(Base):
    """参考素材库"""
    __tablename__ = "reference_assets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), unique=True, nullable=False,
                  comment="素材名称 (用于 @mention)")
    alias = Column(String(255), comment="别名，逗号分隔")
    asset_type = Column(String(20), default="image",
                        comment="类型: image/video")
    file_path = Column(String(512), nullable=False, comment="本地文件路径")
    file_url = Column(String(1024), comment="远程 URL (可选)")
    thumbnail_path = Column(String(512), comment="缩略图路径")
    file_size = Column(Integer, comment="文件大小 (bytes)")
    description = Column(Text, comment="描述")
    tags = Column(String(512), comment="标签，逗号分隔")
    usage_count = Column(Integer, default=0, comment="使用次数")

    created_at = Column(DateTime, default=get_beijing_time)
    updated_at = Column(DateTime, default=get_beijing_time,
                        onupdate=get_beijing_time)
```

### 3.2 新增表：`content_job_references` (任务-素材关联)

```python
class ContentJobReference(Base):
    """内容生成任务与参考素材的关联表"""
    __tablename__ = "content_job_references"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, ForeignKey("content_generation_jobs.id"),
                    nullable=False)
    asset_id = Column(Integer, ForeignKey("reference_assets.id"),
                      nullable=False)
    position = Column(Integer, default=0, comment="素材在任务中的顺序")

    job = relationship("ContentGenerationJob")
    asset = relationship("ReferenceAsset")
```

### 3.3 ContentGenerationJob 扩展字段

在 `db_migration.py` 中新增以下字段：

```python
async def ensure_fast_reference_fields(conn):
    """确保 fast_reference 相关字段存在"""
    new_columns = {
        "retry_count": "INTEGER DEFAULT 0",
        "max_retry": "INTEGER DEFAULT 10",
        "video_url": "VARCHAR(1024)",
        "browser_session_log": "TEXT",
        "polling_region": "VARCHAR(20)",
    }
    for col_name, col_type in new_columns.items():
        try:
            await conn.execute(
                text(f"ALTER TABLE content_generation_jobs "
                     f"ADD COLUMN {col_name} {col_type}")
            )
        except Exception:
            pass  # 字段已存在
```

---

## 4. 签名算法

### 4.1 双签名策略

系统采用双签名降级策略，优先使用现有 `jimeng_service` 的签名，失败时降级到直接 HTTP 签名：

| 策略 | 签名盐值 | 平台标识 | 路径处理 | 使用场景 |
|------|----------|----------|----------|----------|
| 主签名 (11ac) | `11ac` | `7` | `uri.slice(-7)` | 通过 jimeng_service 代理 |
| 备用签名 (1e67) | `1e67` | `web` | 完整 `pathname` | 直接 HTTP 请求 |

### 4.2 主签名 (11ac) — 通过 jimeng_service

现有 `jimeng_service` 中的签名算法：

```javascript
// jimeng_service/src/lib/sign.ts
function sign(uri) {
    const deviceTime = Math.floor(Date.now() / 1000).toString();
    const raw = `9e2c|${uri.slice(-7)}|7|8.4.0|${deviceTime}||11ac`;
    return {
        sign: md5(raw),
        'sign-ver': '1',
        'device-time': deviceTime,
    };
}
```

### 4.3 备用签名 (1e67) — 直接 HTTP

ShukeAI 逆向的签名算法，用于直接发起 HTTP 请求：

```python
import hashlib
import time
from urllib.parse import urlparse

def sign_request_1e67(url: str) -> dict:
    """
    ShukeAI 逆向签名算法 (1e67 盐值)

    签名公式: md5("9e2c|{pathname}|web|8.4.0|{device_time}||1e67")
    与 11ac 的区别:
      - 使用完整 pathname 而非 uri.slice(-7)
      - platform = "web" 而非 "7"
      - salt = "1e67" 而非 "11ac"
    """
    pathname = urlparse(url).path
    device_time = str(int(time.time()))

    raw = f"9e2c|{pathname}|web|8.4.0|{device_time}||1e67"
    sign_value = hashlib.md5(raw.encode()).hexdigest()

    return {
        "sign": sign_value,
        "sign-ver": "1",
        "device-time": device_time,
    }
```

### 4.4 签名选择逻辑

```python
async def poll_with_dual_sign(
    self, task_id: str, session_id: str, region: str
) -> dict:
    """双签名轮询策略"""
    # 策略 1: 通过 jimeng_service 代理 (11ac 签名)
    try:
        result = await self._poll_via_jimeng_service(task_id, session_id)
        if result and result.get("status_code") != 403:
            return result
    except Exception as e:
        logger.warning(f"jimeng_service 轮询失败: {e}, 降级到直接 HTTP")

    # 策略 2: 直接 HTTP (1e67 签名)
    return await self._poll_direct_http(task_id, session_id, region)
```

---

## 5. 浏览器执行器

### 5.1 FastReferenceBrowserExecutor 核心设计

文件位置：`backend/app/services/fast_reference_executor.py`

```python
from dataclasses import dataclass
from typing import Optional, List, Dict
from patchright.async_api import async_playwright, BrowserContext, Page

from app.models import Account
from app.services.browser_stealth import BrowserStealth
from app.services.human_behavior import HumanBehavior


@dataclass
class FastReferenceResult:
    task_id: Optional[str] = None
    history_id: Optional[str] = None
    success: bool = False
    error: Optional[str] = None
    updated_cookies: Optional[str] = None


class FastReferenceBrowserExecutor:
    """
    快速参考视频生成 - 浏览器自动化执行器

    职责:
    1. Cookie 注入 (session_id → .capcut.com)
    2. 导航到视频生成页面
    3. 上传参考素材
    4. 填写 prompt
    5. 拦截网络请求捕获 task_id / history_id
    6. 点击生成按钮
    """

    TARGET_URL = "https://dreamina.capcut.com/ai-tool/video/generate"
    GENERATE_API_PATTERN = "**/mweb/v1/aigc_draft/generate"

    def __init__(
        self,
        account: Account,
        proxy_config: Optional[Dict[str, str]] = None,
    ):
        self.account = account
        self.proxy_config = proxy_config
        self.captured_task_id: Optional[str] = None
        self.captured_history_id: Optional[str] = None
```

### 5.2 完整执行流程

```python
async def execute(
    self,
    prompt: str,
    reference_assets: List[str],  # 本地文件路径列表
    model: str = "Dreamina Seedance 1.0 Mini",
    duration: int = 5,
    resolution: str = "1080p",
    ratio: str = "1:1",
) -> FastReferenceResult:
    """
    完整执行流程

    Returns:
        FastReferenceResult(task_id, history_id, success, error)
    """
    async with async_playwright() as p:
        stealth = BrowserStealth(p)
        region_tag = self.account.region or "TW"

        # 1. 创建浏览器上下文 (带反检测 + 代理)
        context = await stealth.create_context(
            region_tag=region_tag,
            proxy=self.proxy_config,
        )

        # 2. Cookie 注入
        await self._inject_cookies(context)

        # 3. 创建页面 (带流量拦截)
        page = await stealth.create_page(context)
        human = HumanBehavior(page)

        # 4. 设置网络拦截器 (捕获 generate 响应)
        await self._setup_network_interceptor(page)

        try:
            # 5. 导航到生成页面
            await page.goto(
                self.TARGET_URL,
                wait_until="networkidle",
                timeout=30000,
            )
            await human.random_delay(1000, 2000)

            # 6. 关闭可能的弹窗
            await BrowserStealth.dismiss_error_modal(page)
            await human.close_popup_if_exists()

            # 7. 上传参考素材
            await self._upload_references(page, human, reference_assets)

            # 8. 填写 prompt
            await self._fill_prompt(page, human, prompt)

            # 9. 点击生成按钮
            await self._click_generate(page, human)

            # 10. 等待网络拦截器捕获 task_id
            await self._wait_for_task_id(timeout=15000)

            return FastReferenceResult(
                task_id=self.captured_task_id,
                history_id=self.captured_history_id,
                success=True,
            )
        except Exception as e:
            logger.error(f"浏览器执行失败: {e}")
            return FastReferenceResult(success=False, error=str(e))
        finally:
            await context.close()
            browser = context.browser
            if browser:
                await browser.close()
```

### 5.3 Cookie 注入

```python
async def _inject_cookies(self, context: BrowserContext):
    """
    注入 session_id Cookie 到浏览器上下文

    关键点:
    - name 必须是 "sessionid" (小写)
    - domain 必须是 ".capcut.com"
    - path 必须是 "/"
    - 必须在 page.goto() 之前调用
    """
    await context.add_cookies([
        {
            "name": "sessionid",
            "value": self.account.session_id,
            "domain": ".capcut.com",
            "path": "/",
            "httpOnly": True,
            "secure": True,
            "sameSite": "None",
        }
    ])
```

### 5.4 网络拦截器 — 捕获 task_id

```python
async def _setup_network_interceptor(self, page: Page):
    """
    监听 /mweb/v1/aigc_draft/generate 的响应
    从中提取 history_record_id 作为后续轮询的 task_id
    """
    async def on_response(response):
        url = response.url
        if "/mweb/v1/aigc_draft/generate" in url:
            try:
                body = await response.json()
                data = body.get("data", {})
                aigc_data = data.get("aigc_data", {})
                task_node = aigc_data.get("task", {})

                # 优先从 task_node 提取
                self.captured_history_id = (
                    data.get("history_record_id")
                    or task_node.get("submit_id")
                )
                self.captured_task_id = self.captured_history_id

                logger.info(
                    f"网络拦截成功: history_id={self.captured_history_id}"
                )
            except Exception as e:
                logger.warning(f"解析 generate 响应失败: {e}")

    page.on("response", on_response)
```

### 5.5 素材上传

```python
async def _upload_references(
    self, page: Page, human: HumanBehavior, file_paths: List[str]
):
    """
    上传参考素材文件

    策略: 定位 input[type=file]，通过 set_input_files 注入
    """
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

    # 等待上传完成 (检测预览缩略图出现)
    await page.wait_for_selector(
        '[class*="upload-preview"],'
        '[class*="reference-thumb"],'
        'img[class*="uploaded"]',
        state="visible",
        timeout=15000,
    )
    logger.info(f"素材上传完成: {len(file_paths)} 个文件")
```

### 5.6 Prompt 填写与生成触发

```python
async def _fill_prompt(self, page: Page, human: HumanBehavior, prompt: str):
    """填写生成提示词"""
    prompt_selectors = [
        'textarea[placeholder*="describe"]',
        'textarea[placeholder*="Describe"]',
        'textarea[class*="prompt"]',
        '[contenteditable="true"][class*="prompt"]',
        '[data-testid="prompt-input"]',
    ]

    for selector in prompt_selectors:
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
    """点击生成按钮"""
    generate_selectors = [
        'button:has-text("Generate")',
        'button:has-text("生成")',
        'button[class*="generate"]',
        '[data-testid="generate-btn"]',
    ]

    for selector in generate_selectors:
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
    """等待网络拦截器捕获 task_id"""
    elapsed = 0
    interval = 500
    while elapsed < timeout:
        if self.captured_task_id:
            return
        await asyncio.sleep(interval / 1000)
        elapsed += interval

    raise Exception(f"等待 task_id 超时 ({timeout}ms)")
```

---

## 6. 视频轮询

### 6.1 轮询策略

视频生成通常需要 30-120 秒，轮询器需要：
- 每 5 秒查询一次远端状态
- 支持区域降级（TW → HK → TH）
- 正确映射状态码
- 提取最终视频 URL

### 6.2 区域降级配置

```python
# 区域轮询顺序 (ShukeAI: DEFAULT_REGION_PROFILES)
REGION_PROFILES = [
    {"region": "TW", "name": "Taiwan"},
    {"region": "HK", "name": "Hong Kong"},
    {"region": "TH", "name": "Thailand"},
]

# API 常量
API_BASE = "https://mweb-api-sg.capcut.com"
APP_ID = 513641
WEB_VERSION = "7.5.0"
DA_VERSION = "3.3.12"
APP_VERSION = "8.4.0"
```

### 6.3 轮询请求构造

```python
import random
import hashlib
import time
import httpx
from urllib.parse import urlparse, urlencode


def _generate_did() -> str:
    """生成 19 位随机数字 device_id"""
    return "".join([str(random.randint(0, 9)) for _ in range(19)])


def _build_api_url(path: str, region: str) -> str:
    """构造完整 API URL (含 query 参数)"""
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


def _sign_request(url: str) -> dict:
    """1e67 签名算法"""
    pathname = urlparse(url).path
    device_time = str(int(time.time()))
    raw = f"9e2c|{pathname}|web|{APP_VERSION}|{device_time}||1e67"
    sign_value = hashlib.md5(raw.encode()).hexdigest()
    return {
        "sign": sign_value,
        "sign-ver": "1",
        "device-time": device_time,
    }
```

### 6.4 轮询执行

```python
async def poll_video_status(
    self,
    task_id: str,
    session_id: str,
    regions: List[str] = None,
) -> Optional[str]:
    """
    轮询视频生成状态，返回 video_url 或 None

    区域降级: 依次尝试 TW → HK → TH
    """
    if regions is None:
        regions = [r["region"] for r in REGION_PROFILES]

    for region in regions:
        url = _build_api_url("/mweb/v1/get_history_by_ids", region)
        sign_headers = _sign_request(url)

        headers = {
            "Cookie": f"sessionid={session_id}",
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
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()

            if data.get("ret") == "0" or data.get("status_code") == 0:
                return self._extract_result(data)
        except Exception as e:
            logger.warning(f"区域 {region} 轮询失败: {e}")
            continue

    return None
```

### 6.5 状态码映射

```python
# Dreamina 远端状态码
STATUS_SUCCESS = [10, 50]       # 生成完成
STATUS_FAILED = [30]            # 生成失败
STATUS_PROCESSING = [10, 20, 42, 45]  # 仍在处理中

# 注意: status=10 同时出现在 SUCCESS 和 PROCESSING 中
# 判定逻辑: 如果 finish_time != 0 且有 item_list → 成功
#           如果 finish_time == 0 → 仍在处理
```

### 6.6 视频 URL 提取

```python
def _extract_result(self, data: dict) -> Optional[str]:
    """
    从轮询响应中提取视频 URL

    提取路径 (优先级):
    1. item_list[0]["video"]["transcoded_video"]["origin"]["video_url"]
    2. item_list[0]["video"]["video_url"]  (fallback)
    """
    try:
        histories = data.get("data", {}).get("histories", [])
        if not histories:
            return None

        history = histories[0]
        item_list = history.get("item_list", [])
        if not item_list:
            return None

        # 检查是否真正完成
        finish_time = history.get("finish_time", 0)
        if not finish_time:
            return None  # 仍在处理中

        video_item = item_list[0]
        video_data = video_item.get("video", {})

        # 优先: transcoded_video.origin.video_url
        transcoded = video_data.get("transcoded_video", {})
        origin = transcoded.get("origin", {})
        video_url = origin.get("video_url")

        # Fallback: video.video_url
        if not video_url:
            video_url = video_data.get("video_url")

        return video_url
    except (KeyError, IndexError, TypeError) as e:
        logger.warning(f"视频 URL 提取失败: {e}")
        return None
```

---

## 7. 并发控制

### 7.1 浏览器实例限制

浏览器实例是重资源（每个约 200-500MB 内存），必须独立于 Worker 数量进行限制：

```python
# ContentGenerationService.__init__ 中新增
self.browser_semaphore = asyncio.Semaphore(
    int(os.getenv("FAST_MAX_BROWSERS", "3"))
)
```

在 `_run_fast_reference_job()` 中使用：

```python
async def _run_fast_reference_job(self, job_id: int, worker_id: int):
    """fast_reference 模式的任务执行"""
    async with self.browser_semaphore:
        executor = FastReferenceBrowserExecutor(
            account=account,
            proxy_config=proxy_config,
        )
        result = await executor.execute(
            prompt=payload.prompt,
            reference_assets=asset_paths,
        )
        # ... 处理结果
```

### 7.2 账号租约原子性

现有 `_acquire_account()` 已实现基于 `gen_locked_until` 的乐观锁，但存在并发竞争窗口。改为条件 UPDATE：

```python
async def _acquire_account_atomic(self, db) -> Optional[Account]:
    """原子性账号租约 - 避免并发竞争"""
    now = datetime.now()
    lock_until = now + timedelta(minutes=10)

    # SQLite 兼容方案: 先查后锁 + 二次条件检查
    candidates = (
        await db.execute(
            select(Account)
            .where(
                Account.gen_enabled == True,
                Account.session_id.isnot(None),
                Account.health_status == "healthy",
            )
            .order_by(
                Account.gen_last_used_at.asc().nullsfirst(),
                Account.id.asc(),
            )
        )
    ).scalars().all()

    for account in candidates:
        locked = account.__dict__.get("gen_locked_until")
        if locked and locked > now:
            continue

        result = await db.execute(
            update(Account)
            .where(
                Account.id == account.id,
                # 二次检查: 防止并发窗口内被其他 Worker 锁定
                (Account.gen_locked_until.is_(None))
                | (Account.gen_locked_until <= now),
            )
            .values(gen_locked_until=lock_until, gen_last_used_at=now)
        )
        await db.commit()

        if result.rowcount > 0:
            await db.refresh(account)
            return account

    return None
```

### 7.3 账号消费策略

通过环境变量 `FAST_ACCOUNT_STRATEGY` 配置：

| 策略 | 值 | 行为 |
|------|-----|------|
| 可复用 | `reusable` | 任务完成后释放锁，账号可继续使用 |
| 一次性 | `one_time` | 任务完成后设置 `gen_enabled=False` |
| 低积分停用 | `disable_on_low_credit` | 积分低于阈值时自动停用 |

```python
async def _handle_account_after_job(self, account, db, strategy):
    if strategy == "one_time":
        await self._auto_disable_account(
            account, db, "fast_reference_one_time_used"
        )
    elif strategy == "disable_on_low_credit":
        credits = await self._check_credits(account)
        threshold = int(os.getenv("FAST_CREDIT_THRESHOLD", "10"))
        if credits.get("total", 0) < threshold:
            await self._auto_disable_account(
                account, db, "insufficient_credits"
            )
    # reusable: 仅释放锁，不做额外操作
```

---

## 8. 素材库管理

### 8.1 @mention 正则

```python
import re

MENTION_PATTERN = re.compile(r"@([A-Za-z0-9_\-一-鿿]+)")

def extract_mentions(prompt: str) -> List[str]:
    """从 prompt 中提取所有 @mention 引用名"""
    return MENTION_PATTERN.findall(prompt)
```

示例：`"一只猫 @cat_ref 在跑步 @背景1"` -> `["cat_ref", "背景1"]`

### 8.2 别名解析

```python
async def resolve_mention(self, mention_name: str, db):
    """
    解析 @mention 到素材记录
    查找顺序: 1. name 精确匹配  2. alias 包含匹配
    """
    asset = (
        await db.execute(
            select(ReferenceAsset)
            .where(ReferenceAsset.name == mention_name)
        )
    ).scalar_one_or_none()

    if asset:
        return asset

    all_assets = (await db.execute(select(ReferenceAsset))).scalars().all()
    for a in all_assets:
        if a.alias:
            aliases = [x.strip() for x in a.alias.split(",")]
            if mention_name in aliases:
                return a
    return None
```

### 8.3 素材库 API 端点

| 端点 | 方法 | 功能 |
|------|------|------|
| `/api/fast-reference/assets` | GET | 获取素材列表 |
| `/api/fast-reference/assets` | POST | 上传素材 (multipart/form-data) |
| `/api/fast-reference/assets/{id}` | PUT | 更新素材信息/替换文件 |
| `/api/fast-reference/assets/{id}` | DELETE | 删除素材 |
| `/api/fast-reference/assets/resolve` | POST | 解析 prompt 中的 @mention |

---

## 9. 前端设计

### 9.1 页面结构

新增独立页面 `FastReference.tsx`，路由 `/fast-reference`。

```
+-----------------------------------------------------+
|  Fast Reference 视频生成                    [筛选栏] |
+-----------------------------------------------------+
|                                                      |
|  [卡片]  [卡片]  [卡片]  [卡片]  [卡片]             |
|  (视频)  (生成中) (完成)  (失败)  (排队)             |
|                                                      |
|  ... VirtuosoGrid 虚拟滚动 ...                       |
|                                                      |
+-----------------------------------------------------+
|  +-- 底部浮动面板 (glass-morphism) ----------------+ |
|  |                                                  | |
|  |  [素材库] [@mention 编辑器 / Prompt 输入]  [生成]| |
|  |                                                  | |
|  |  模型: [Seedance 1.0 Mini v]  时长: [5s v]      | |
|  |  分辨率: [1080p v]  比例: [1:1 v]               | |
|  +--------------------------------------------------+ |
+-----------------------------------------------------+
```

### 9.2 Glass-morphism 底部面板

延续 `ContentGeneration.tsx` 的设计语言：

```tsx
{/* 底部浮动输入面板 */}
<div className={cn(
  "fixed bottom-0 left-[var(--sidebar-width)] right-0",
  "bg-white/70 dark:bg-zinc-900/70",
  "backdrop-blur-xl border-t border-white/20",
  "shadow-[0_-4px_30px_rgba(0,0,0,0.1)]",
  "transition-all duration-300 ease-out",
  isExpanded ? "pb-6 pt-4" : "pb-4 pt-3",
)}>
  {/* Prompt 输入区 + @mention 支持 */}
  {/* 参数选择器行 */}
  {/* 素材库 Drawer 触发按钮 */}
</div>
```

### 9.3 素材库 Drawer

点击底部面板的"素材库"按钮，从右侧滑出 Drawer：

```tsx
<Sheet>
  <SheetTrigger asChild>
    <Button variant="outline" size="sm">
      <ImageIcon className="w-4 h-4 mr-1" />
      素材库
    </Button>
  </SheetTrigger>
  <SheetContent side="right" className="w-[400px]">
    {/* 素材网格: 缩略图 + 名称 + @alias */}
    {/* 上传按钮 */}
    {/* 拖拽上传区域 */}
  </SheetContent>
</Sheet>
```

### 9.4 @mention 编辑器

Prompt 输入框支持 `@` 触发自动补全：

```tsx
const handlePromptChange = (value: string) => {
  setPrompt(value)
  const lastAt = value.lastIndexOf("@")
  if (lastAt >= 0) {
    const query = value.slice(lastAt + 1)
    setMentionCandidates(
      assets.filter(a =>
        a.name.includes(query) ||
        a.alias?.split(",").some(al => al.trim().includes(query))
      )
    )
    setShowMentionPopup(true)
  }
}
```

### 9.5 一键生成工作流

用户视角只需一步操作（底层自动执行 draft -> prepare -> start）：

```tsx
const handleGenerate = async () => {
  // 1. 创建任务 (draft)
  const job = await fastRefApi.createJob({
    prompt, model, duration, resolution, ratio,
  })

  // 2. 自动 prepare (解析 @mention + 绑定素材)
  const prepResult = await fastRefApi.prepareJob(job.id)
  if (prepResult.missing_assets?.length > 0) {
    toast.error(`缺少素材: ${prepResult.missing_assets.join(", ")}`)
    return
  }

  // 3. 自动 start (入队)
  await fastRefApi.startJob(job.id)
  toast.success("任务已提交")
  fetchJobs({ force: true })
}
```

### 9.6 路由注册

```tsx
// frontend/src/config/routes.tsx
{
  path: "/fast-reference",
  element: <FastReference />,
  icon: <Zap className="w-4 h-4" />,
  label: "快速参考",
}
```

---

## 10. 配置项

### 10.1 新增环境变量

在 `backend/.env` 中添加：

```bash
# ===== Fast Reference 视频生成 =====

# 最大并发浏览器实例数 (每个约 200-500MB 内存)
FAST_MAX_BROWSERS=3

# 账号消费策略: reusable / one_time / disable_on_low_credit
FAST_ACCOUNT_STRATEGY=reusable

# 低积分停用阈值 (仅 disable_on_low_credit 策略生效)
FAST_CREDIT_THRESHOLD=10

# 单任务最大重试次数
FAST_MAX_RETRY=10

# 轮询间隔 (秒)
FAST_POLL_INTERVAL=5

# 任务超时 (秒)
FAST_TASK_TIMEOUT=300

# 浏览器无头模式 (生产环境建议 true)
FAST_HEADLESS=true

# 素材存储目录
FAST_ASSETS_DIR=data/fast_reference/assets
```

### 10.2 config.py 扩展

```python
# app/core/config.py - Settings 类新增
fast_max_browsers: int = 3
fast_account_strategy: str = "reusable"
fast_credit_threshold: int = 10
fast_max_retry: int = 10
fast_poll_interval: int = 5
fast_task_timeout: int = 300
fast_headless: bool = True
fast_assets_dir: str = "data/fast_reference/assets"
```

---

## 11. 实施计划

### Phase 1: 数据层 (预计 1 天)

- [ ] 新增 `ReferenceAsset` 模型 (`models/reference_asset.py`)
- [ ] 新增 `ContentJobReference` 模型 (`models/content_job_reference.py`)
- [ ] 编写 `db_migration.py` 迁移函数 (`ensure_fast_reference_fields`)
- [ ] 在 `models/__init__.py` 注册新模型
- [ ] 在 `config.py` 添加新配置项

### Phase 2: 素材库服务 (预计 1 天)

- [ ] 实现 `ReferenceAssetService` (`services/reference_asset_service.py`)
- [ ] @mention 正则提取 + 别名解析
- [ ] 素材文件上传/存储/缩略图生成
- [ ] 新增 API 路由 (`api/routers/fast_reference_assets.py`)

### Phase 3: 浏览器执行器 (预计 2 天)

- [ ] 实现 `FastReferenceBrowserExecutor` (`services/fast_reference_executor.py`)
- [ ] Cookie 注入 + 页面导航
- [ ] 网络拦截器 (捕获 task_id)
- [ ] 素材上传 + Prompt 填写 + 生成触发
- [ ] 集成 BrowserStealth + HumanBehavior + ProxyPool

### Phase 4: 轮询器 (预计 1 天)

- [ ] 实现 `FastReferencePoller` (`services/fast_reference_poller.py`)
- [ ] 双签名策略 (11ac -> 1e67)
- [ ] 区域降级 (TW -> HK -> TH)
- [ ] 视频 URL 提取 + 本地下载

### Phase 5: 服务集成 (预计 1 天)

- [ ] 扩展 `ContentGenerationService._run_job()` 添加 `fast_reference` 分发
- [ ] 实现 `_run_fast_reference_job()` 方法
- [ ] 集成 `browser_semaphore` 并发控制
- [ ] 集成账号消费策略
- [ ] 新增 API 路由 (`api/routers/fast_reference.py`)
- [ ] 在 `main.py` 注册新路由

### Phase 6: 前端页面 (预计 2 天)

- [ ] 新增 `FastReference.tsx` 页面
- [ ] Glass-morphism 底部面板 + 参数选择器
- [ ] 素材库 Drawer (上传/管理/预览)
- [ ] @mention 自动补全编辑器
- [ ] VirtuosoGrid 任务卡片网格
- [ ] 一键生成工作流 (draft -> prepare -> start)
- [ ] 注册路由 + 侧边栏菜单项
- [ ] API 客户端封装 (`services/api.ts` 扩展)

### Phase 7: 测试与调优 (预计 1 天)

- [ ] 端到端测试：创建任务 -> 浏览器执行 -> 轮询 -> 视频下载
- [ ] 并发测试：多任务同时执行，验证 Semaphore 限制
- [ ] 异常测试：Cookie 过期、网络超时、素材缺失
- [ ] 选择器适配：验证 Dreamina 页面当前版本的 CSS 选择器

---

## 附录 A: ShukeAI 参考项目关键差异

| 维度 | ShukeAI | 本项目方案 |
|------|---------|-----------|
| 浏览器 | 原版 Playwright | Patchright (反检测分支) |
| 指纹伪装 | 无 | BrowserStealth (WebGL/Canvas/Audio/Navigator) |
| 行为模拟 | `time.sleep()` | HumanBehavior (贝塞尔鼠标/随机打字) |
| 代理 | 无 | ProxyPool (Mihomo 多端口隔离) |
| 账号模型 | 独立 `JimengFastAccount` 表 | 复用 `Account` (gen_enabled 池管理) |
| 任务模型 | 独立 `JimengFastReferenceTask` 表 | 复用 `ContentGenerationJob` (function_mode) |
| 素材存储 | `library.json` 文件 | DB 表 (`reference_assets`) |
| 调度器 | 独立线程池 | 扩展 `ContentGenerationService` Worker 池 |
| 签名 | 仅 1e67 | 双策略: 11ac (jimeng_service) -> 1e67 (fallback) |
| 账号选择 | `random.choice()` | `gen_locked_until` 条件 UPDATE (原子租约) |

## 附录 B: 关键文件路径索引

```
backend/app/
  models/
    reference_asset.py              # 新增: 参考素材模型
    content_job_reference.py        # 新增: 任务-素材关联模型
  services/
    fast_reference_executor.py      # 新增: 浏览器执行器
    fast_reference_poller.py        # 新增: 视频轮询器
    reference_asset_service.py      # 新增: 素材库服务
    content_generation.py           # 修改: 新增 fast_reference 分发
    browser_stealth.py              # 复用: 反检测配置
    human_behavior.py               # 复用: 行为模拟
    proxy_pool.py                   # 复用: 代理池
    db_migration.py                 # 修改: 新增迁移函数
  api/routers/
    fast_reference.py               # 新增: 任务 API
    fast_reference_assets.py        # 新增: 素材库 API
  core/
    config.py                       # 修改: 新增配置项
  main.py                           # 修改: 注册新路由

frontend/src/
  pages/
    FastReference.tsx               # 新增: 前端页面
  config/
    routes.tsx                      # 修改: 注册路由
  services/
    api.ts                          # 修改: 新增 API 客户端
```
