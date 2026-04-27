# Dreamina Auto Register — 项目技术白皮书

> 本文档面向其他 AI 系统 / Agent / 自动化编排器，提供本项目的完整技术介绍。
> 生成时间：2026-04-27 | 分析来源：Claude (后端架构) + Gemini (前端/UX)

---

## 一、项目定位与核心能力

**Dreamina Auto Register** 是即梦 (Dreamina / CapCut) 国际版的全自动化账号注册与 AI 内容生成系统。

对外部 AI 系统而言，本项目是一个"**开箱即用的账号池与 AI 算力中间件**"：

- **向下**：屏蔽了浏览器指纹对抗、代理轮换、Cloudflare 邮箱验证、验证码处理、设备风控等复杂反爬机制
- **向上**：提供标准化 RESTful API（60 个端点）+ OpenAI 兼容接口，供其他 AI Agent 直接调用

**核心能力矩阵**：

| 能力 | 说明 |
|------|------|
| 批量自动注册 | 全程无人值守，支持 Cloudflare 泛域名邮箱 + Outlook 邮箱双源 |
| 反检测浏览器自动化 | Patchright (Playwright fork) + 4 种设备画像 + WebGL/Canvas/Audio 指纹加固 |
| 智能代理池 | Mihomo 多端口隔离 + Clash API + 外部代理，least_used/round_robin 调度 |
| 账号生命周期管理 | 自动签到、积分收集、Session 健康检查、批量导出 |
| AI 内容生成 | 文生图 / 图生图 / 视频生成，通过 OpenAI 兼容接口对外暴露 |
| 可视化管理面板 | React 9 页面 GUI，WebSocket 实时日志，中英文国际化 |

---

## 二、系统架构总览

### 2.1 模块拓扑

```
┌─────────────────────────────────────────────────────────────────┐
│                    dreamina-auto-register                       │
├──────────────┬──────────────┬──────────┬───────────┬───────────┤
│ backend/app  │jimeng_service│ frontend │ cf-worker │dreamina2api│
│ Python/FastAPI│ Node.js/Koa │React/Vite│ CF Worker │ 独立部署版 │
│  :8005       │   :5105      │  :5175   │ Edge      │ Docker/   │
│              │              │          │           │ Vercel    │
└──────┬───────┴──────┬───────┴────┬─────┴─────┬─────┴───────────┘
       │              │            │           │
       ▼              ▼            ▼           ▼
   SQLite DB    Dreamina API   Browser    Cloudflare KV
   (本地)       (逆向调用)     (用户)     (验证码存储)
```

### 2.2 数据流全链路

```
用户/AI Agent
    │
    ▼ REST API (:8005)
┌─────────────────────────────────────────────┐
│           FastAPI 后端主控                    │
│  TaskScheduler → RegisterEngine              │
│       │                │                     │
│       ▼                ▼                     │
│  ProxyPool ──→ Patchright Browser            │
│  (Mihomo)      (反检测指纹)                   │
│                    │                         │
│                    ▼                         │
│            Dreamina 目标站                    │
│            (注册表单填写)                      │
│                    │                         │
│                    ▼ 触发验证邮件              │
│         Cloudflare Worker (Edge)             │
│         提取验证码 → KV 存储                  │
│                    │                         │
│                    ▼ 轮询 KV                  │
│           后端获取验证码 → 填入               │
│                    │                         │
│                    ▼                         │
│         Session 提取 → 账号入库               │
│         自动签到 → 积分收集                   │
└─────────────────────────────────────────────┘
    │
    ▼ WebSocket 实时日志推送
  前端面板 / AI Agent
    │
    ▼ OpenAI 兼容接口 (:5105)
  Jimeng API → AI 内容生成
```

---

## 三、后端核心 (Python/FastAPI) — 端口 8005

### 3.1 注册引擎 (`RegisterEngine`)

10 步完整注册流程：

```
create_browser → proxy_preflight → navigate → login_page_trigger
→ click_continue_email → switch_to_signup → fill_register_form
→ wait_verification_code → fill_birth_date → extract_session
```

**关键设计**：
- 每次注册启动独立浏览器进程 + 独立 BrowserContext + 独立指纹，零交叉污染
- 代理预检 3 次重试，5 个探测端点（Cloudflare/ip-api/ipinfo/Dreamina/静态资源）
- 真实 IP 国家代码自动映射区域标签，驱动 timezone/locale/Accept-Language 一致性
- 任务中断检测：关键步骤前查询 DB 状态，支持 pause/cancel

### 3.2 反检测子系统

#### BrowserStealth — 浏览器指纹伪装

| 层级 | 技术 | 细节 |
|------|------|------|
| 浏览器引擎 | Patchright | Playwright 反检测分支，底层抹除 webdriver 特征 |
| TLS/JA3 | 系统 Chrome | `channel="chrome"` 优先使用系统安装的 Chrome，TLS 指纹与手动浏览一致 |
| WebRTC | 启动参数 | `--webrtc-ip-handling-policy=disable_non_proxied_udp` 防止真实 IP 泄露 |
| 设备画像 | 4 种 Archetype | Windows-HighEnd-AMD / Mac-NVIDIA / Windows-Intel / Laptop-Intel-Xe |
| 地域矩阵 | 9 个区域 | US/UK/JP/KR/SG/HK/TW/DE/FR，timezone + locale + languages 三元组 |
| WebGL | JS 注入 | 覆写 `getParameter(37445/37446)` 返回画像中的 vendor/renderer |
| Canvas | JS 注入 | `toDataURL` 注入确定性微噪声（10 像素点 XOR 1），context 生命周期内稳定 |
| Audio | JS 注入 | `getChannelData` 末尾采样点 +1e-7 扰动 |
| Navigator | JS 注入 | 固定 vendor/platform/hardwareConcurrency/deviceMemory |
| 流量拦截 | route 拦截 | 屏蔽 6 个打点域名 + media 资源，保留 font |

#### HumanBehavior — 人类行为模拟

| 行为 | 实现 |
|------|------|
| 鼠标移动 | 15-30 步贝塞尔缓动轨迹 + ±2px Jitter + 5-20ms 不均匀步间延迟 |
| 悬停 | 自然移动到目标 + 1-3 次 ±1px 微抖动 |
| 点击 | 悬停 → 50-150ms 等待 → 中心 ±5px 偏移点击 |
| 打字 | 逐字符输入，10-30ms 随机间隔 |
| 表单填写 | 前后随机延迟 300-800ms |
| 阅读停顿 | 0.3-1.0s 随机等待 |

### 3.3 代理池架构

三层代理体系：

```
┌─────────────────────────────────────────┐
│ ProxyPoolManager (调度层)                │
│ - acquire_proxy(strategy)               │
│ - release_proxy()                       │
│ - validate_all_proxies()                │
│ 策略: least_used / round_robin          │
│ 自愈: 无空闲时对 3 个不健康节点快速预检   │
├─────────────────────────────────────────┤
│ ProxyPoolRunner (隔离层)                 │
│ - 启动独立 Mihomo 进程                   │
│ - 每节点独立本地端口 (20000+)            │
│ - IN-PORT 规则路由                       │
│ - HTTPDNS 反 Fake-IP 污染               │
├─────────────────────────────────────────┤
│ ClashManager (接口层)                    │
│ - Clash API 节点切换                     │
│ - 连接检查                               │
│ - 桌面 Clash Verge 兼容                  │
└─────────────────────────────────────────┘
```

**代理加载来源**：Clash 配置文件 / Clash API / 外部代理列表（支持 4 种格式）/ 外部代理文件

**健康检测**：并发度 5，多级降级探测（Cloudflare → ip-api → ipinfo），52 国地域关键词 fallback 映射

### 3.4 Session 提取 (`SessionIdExtractor`)

5 路并行提取，确保最大覆盖：

| 路径 | 目标 |
|------|------|
| Cookie | 7 个候选名：sessionid, ttwid, msToken, passport_csrf_token 等 |
| LocalStorage | 6 个候选键：sessionId, token, access_token 等 |
| SessionStorage | 同上 6 个键 |
| JS 全局变量 | `window.__INITIAL_STATE__` |
| 网络请求 Header | 捕获含 session/token/auth 的请求头 |

最多轮询 15 秒等待 Session 出现。

### 3.5 任务调度器 (`TaskScheduler`)

- Worker 池模式，可配置最大并发数 (`MAX_CONCURRENCY`)
- Job 队列 + 异步 Worker 消费
- 代理申请失败自动重新入队 + 5s 避让
- 支持 pause/cancel 中断

### 3.6 数据模型 (SQLite + SQLAlchemy 2.0 异步)

| 表 | 核心字段 | 说明 |
|----|----------|------|
| `accounts` | email, password, session_id, cookies, fingerprint_json, credits_*, region, status | 注册账号全生命周期 |
| `task_records` | task_id, status, target_count, success_count, domain_ids, proxy_strategy, email_source | 注册任务配置与进度 |
| `email_domains` | domain, usage_count, max_usage, is_enabled | Cloudflare 泛域名 |
| `proxy_nodes` | name, host, port, protocol, region_tag, latency, is_healthy, usage_count | 代理节点 |
| `outlook_mailboxes` | email, is_enabled, usage_count, last_used_at | Outlook 邮箱资源 |
| `content_generation_jobs` | job_type, prompt, model, ratio, status, result_urls, account_id | AI 内容生成任务 |

---

## 四、Jimeng API 服务 (Node.js/Koa) — 端口 5105

### 4.1 定位

即梦 AI 的逆向 API 中间件，将私有 API 包装为 **OpenAI 兼容接口**。

### 4.2 核心能力

| 能力 | 说明 |
|------|------|
| Token 多区域路由 | 6 个 Profile (CN/US/HK/JP/SG/TW)，自动匹配 API 域名和 Referer |
| 图片生成 | 文生图 / 图生图 / 多图混合，3 档分辨率 × 8 比例 |
| 视频生成 | first_last_frames / omni_reference 两种模式 |
| 异步任务系统 | SQLite 持久化 + SmartPoller 智能轮询（状态感知、自适应间隔） |
| CDN 上传 | ImageX 4 步流程 + AWS4 签名 |
| 请求签名 | Cookie 伪造 + 代理 + 重试机制 |
| 模型映射 | 3 套映射表 × 图片+视频，支持 OpenAI 模型名到即梦内部模型的转换 |

### 4.3 OpenAI 兼容接口

外部 AI Agent 可直接使用 OpenAI SDK 对接：

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:5105/v1",
    api_key="your-jimeng-session-id"
)

# 图片生成
response = client.images.generate(
    model="jimeng-2.1",
    prompt="a cat in space",
    size="1024x1024"
)
```

### 4.4 路由端点

| 路由 | 方法 | 功能 |
|------|------|------|
| `/v1/images/generations` | POST | 图片生成（OpenAI 兼容） |
| `/v1/videos/generations` | POST | 视频生成 |
| `/v1/tasks/:id` | GET | 异步任务状态查询 |
| `/v1/token/info` | POST | Token 信息与积分查询 |
| `/v1/models` | GET | 可用模型列表 |
| `/ping` | GET | 健康检查 |

---

## 五、前端管理面板 (React/Vite) — 端口 5175

### 5.1 技术栈

React 18 + Vite 5 + TailwindCSS + Radix UI + react-i18next (中英文)

### 5.2 九大页面功能

| 页面 | 功能 | 核心交互 |
|------|------|----------|
| Dashboard | 全景统计仪表盘 | 账号总数/存活率/今日产出/代理可用率 |
| Tasks | 任务管理 | 创建/暂停/恢复/取消，配置并发数+资源池 |
| Accounts | 账号资产库 | 高性能数据表格，批量导出/签到/健康检查 |
| Proxies | 代理节点管理 | 连通性测试、延迟展示、启用/禁用 |
| Domains | Cloudflare 域名 | 泛域名配置、接码状态、使用统计 |
| OutlookMailboxes | Outlook 邮箱池 | 批量导入、封控状态标记 |
| ContentGeneration | AI 内容生成 | 文生图/图生图/视频，异步轮询+结果预览 |
| Logs | 实时日志终端 | WebSocket 推送，终端风格深色主题 |
| Settings | 系统配置 | 环境变量、API Keys、阈值配置 |

### 5.3 API 层 (`api.ts`)

统一 axios 封装，模块化分组：

```typescript
// 主要 API 模块
taskApi      // 任务 CRUD + 启停控制 (7 端点)
accountApi   // 账号管理 + 签到 + 健康检查 (13 端点)
proxyApi     // 代理节点管理 (13 端点)
domainApi    // 域名管理 (6 端点)
contentApi   // 内容生成 (12 端点)
settingsApi  // 系统配置 (2 端点)
dashboardApi // 仪表盘 (1 端点)
outlookApi   // Outlook 邮箱 (4 端点)
wsApi        // WebSocket 日志 (2 端点)
```

### 5.4 用户交互流程（完整闭环）

```
资源准备 → 任务编排 → 实时监控 → 资产管理 → 内容消费

1. Proxies 配置代理池
2. Domains 绑定 Cloudflare 域名 / OutlookMailboxes 导入邮箱
3. Tasks 创建注册任务（配置并发数、目标量、资源池）
4. Logs 实时监控注册进度（WebSocket）
5. Accounts 查看注册结果，批量签到保活
6. ContentGeneration 消耗账号额度生成 AI 内容
```

---

## 六、Cloudflare Worker — Edge 部署

### 6.1 职责

接收 Cloudflare Email Routing 的邮件，提取 6 位验证码，存储到 KV。

### 6.2 工作流

```
Dreamina 发送验证邮件
    → Cloudflare Email Routing (Catch-all)
    → Worker 触发
    → 正则提取验证码 (Subject 优先 → Body fallback)
    → KV.put(email, code, TTL=600s)
    → 后端轮询 KV.get(email) 获取验证码
```

### 6.3 HTTP 查询接口

```
GET /code?email=user@example.com
→ { "email": "...", "code": "123456" }
```

---

## 七、REST API 端点概览 (60 个)

| 路由前缀 | 端点数 | 核心功能 |
|----------|--------|----------|
| `/api/tasks` | 7 | 任务 CRUD、启动/暂停/取消 |
| `/api/accounts` | 13 | 账号列表/导出/签到/健康检查/内容生成池 |
| `/api/proxies` | 13 | 代理节点 CRUD/健康检测/批量导入/启用禁用 |
| `/api/domains` | 6 | Cloudflare 域名 CRUD |
| `/api/settings` | 2 | 系统配置读写 |
| `/api/dashboard` | 1 | 仪表盘统计 |
| `/api/content` | 12 | 内容生成任务提交/查询/重试/模型列表 |
| `/api/outlook-mailboxes` | 4 | Outlook 邮箱 CRUD |
| `/ws` | 2 | WebSocket 实时日志 |

---

## 八、技术栈与依赖

### 后端 (Python)

| 包 | 用途 |
|----|------|
| FastAPI + Uvicorn | Web 框架 |
| SQLAlchemy 2.0 + aiosqlite | 异步 ORM + SQLite |
| Patchright | 反检测浏览器自动化 |
| playwright-stealth | 浏览器指纹伪装 |
| httpx + httpx-socks | HTTP 客户端 (SOCKS 代理) |
| pydantic-settings | 配置管理 |
| Pillow + imageio | 缩略图提取 |

### Jimeng API (Node.js)

| 包 | 用途 |
|----|------|
| Koa 2.15 | HTTP 框架 |
| better-sqlite3 | 异步任务持久化 |
| tsup | TypeScript 构建 |

### 前端 (React)

| 包 | 用途 |
|----|------|
| React 18 + Vite 5 | UI 框架 + 构建 |
| TailwindCSS 3.4 | 样式 |
| Radix UI | 无障碍 Headless 组件 |
| react-i18next | 国际化 |
| axios | HTTP 客户端 |

---

## 九、部署与运行

### 环境要求

- Windows 10/11
- Python 3.11+
- Node.js 18+
- Clash Verge (代理服务)

### 端口分配

| 端口 | 服务 | 用途 |
|------|------|------|
| 5175 | Vite Dev Server | 前端面板 |
| 8005 | FastAPI | 后端主控 API + WebSocket |
| 5105 | Koa | Jimeng API (OpenAI 兼容) |

### 一键启动

```bash
start.bat
```

### 手动启动

```bash
# 后端
cd backend && python -m uvicorn app.main:app --host 0.0.0.0 --port 8005

# Jimeng API
cd backend/jimeng_service && npm run dev

# 前端
cd frontend && npm run dev
```

### 关键配置 (`backend/.env`)

| 配置组 | 必填项 |
|--------|--------|
| Clash 代理 | `CLASH_CONTROLLER_URL`, `CLASH_SECRET` |
| Cloudflare | `CF_ACCOUNT_ID`, `CF_KV_NAMESPACE_ID`, `CF_API_TOKEN` |
| Dreamina | `DREAMINA_URL`, `JIMENG_API_URL` |
| 任务 | `MAX_CONCURRENCY`, `MAX_RETRY_COUNT` |

---

## 十、外部 AI 集成指南

### 方案 A：数据级集成（推荐用于编排器/Agent）

直接调用 `:8005` 的 REST API：

```python
import httpx

# 创建注册任务
resp = httpx.post("http://localhost:8005/api/tasks", json={
    "target_count": 10,
    "domain_ids": [1, 2],
    "proxy_strategy": "least_used",
    "max_concurrency": 3
})

# 获取注册成功的账号
accounts = httpx.get("http://localhost:8005/api/accounts?status=active").json()
```

### 方案 B：算力级集成（推荐用于对话 Agent）

使用 OpenAI SDK 对接 `:5105` 的 Jimeng API：

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:5105/v1",
    api_key="<account-session-id>"
)

# 生成图片
result = client.images.generate(
    model="jimeng-2.1",
    prompt="a futuristic city at sunset",
    size="1024x1024"
)
print(result.data[0].url)
```

### 方案 C：全自动闭环

```
注册账号 (REST :8005)
    → 提取 Session Token
    → 注入 Jimeng API (:5105)
    → OpenAI 兼容接口生成内容
    → 全自动 AI 工作流
```

---

*本文档由 Claude + Gemini 多模型协作分析生成，基于项目源码的完整扫描。*
