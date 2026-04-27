# Dreamina Auto Register System

即梦 (Dreamina) 自动注册系统 —— 全自动化的账号批量注册工具。

![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)
![Node.js](https://img.shields.io/badge/Node.js-18+-green.svg)
![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey.svg)

## ✨ 功能特性

- **多源邮箱注册**：支持自定义 Cloudflare 泛域名，也可直接集成批量 Outlook 邮箱资源进行业务转化
- **全栈自动化**：自动接管验证码提交流程、用户数据拼装及验证，全程无人值守注册
- **高可用智能代理池**：无缝对接 Clash/Mihomo 核心，支持多策略路由轮询，自带节点连通性拨测与异常秒级踢出重试,同时支持代理IP负载均衡对接
- **精准出网追踪**：调用地理位置 API 侦测节点真实出网 IP，100% 自动对齐账号地域与浏览器时区/语言，防止“地理跳跃”风控
- **硬核反指纹浏览器**：架构级集成 **Patchright** (Playwright Anti-detect)，底层抹除 Webdriver 特征，伪装 WebGL 与 Canvas 渲染指纹
- **资产与生命周期管理**：多维度可视化资产面板，支持 Session 状态秒级探测、自动每日签到并收集积分、数据全量导出
- **多语言 GUI 界面**：全响应式现代管理后端，支持原生中/英文热切

## 📋 环境要求

| 依赖 | 版本 | 说明 |
|------|------|------|
| **Windows** | 10/11 | 目前仅支持 Windows |
| **Python** | 3.11+ | [下载地址](https://www.python.org/downloads/)，安装时勾选 `Add to PATH` |
| **Node.js** | 18+ | [下载地址](https://nodejs.org/)，安装时会自动配置 npm |
| **Clash Verge** | 最新版 | [下载地址](https://github.com/clash-verge-rev/clash-verge-rev/releases)，用于代理服务 |

## 🚀 安装与启动

### 方式一：一键启动（推荐）

```bash
# 1. 克隆项目
git clone <仓库地址>
cd jimeng-auto-register

# 2. 运行启动脚本
start.bat
```

首次运行时，脚本会自动完成以下操作：
1. ✅ 检查 Python 和 Node.js 环境
2. ✅ 创建 Python 虚拟环境并安装依赖
3. ✅ 安装 Playwright Chromium 浏览器（~150MB）
4. ✅ 安装 Jimeng API 服务和前端依赖
5. ✅ 从 `.env.example` 创建配置文件

> **⚠️ 首次运行会提示你编辑 `backend/.env` 配置文件，填写完成后重新运行 `start.bat` 即可。**

### 🔄 如何更新

如果您已经克隆或下载了项目，请按以下步骤更新：

1. **下载新包**：下载并解压最新的全量压缩包。
2. **覆盖式更新**：将新文件覆盖到原文件夹，但**务必保留**以下内容不要覆盖：
   - `backend/.env` (您的配置文件)
   - `backend/data/` (您的账号数据、缩略图等)
3. **启动项目**：运行 `start.bat`。

### 方式二：手动安装

如果一键脚本不适用，按以下步骤操作：

#### Step 1：安装后端

```bash
cd backend

# 创建虚拟环境
python -m venv .venv
.venv\Scripts\activate

# 安装 Python 依赖
pip install -r requirements.txt

# 安装 Playwright 浏览器
playwright install chromium
```

#### Step 2：安装 Jimeng API 服务

```bash
cd backend/jimeng_service
npm install
```

#### Step 3：安装前端

```bash
cd frontend
npm install
npm run build
```

#### Step 4：配置环境变量

```bash
cd backend
copy .env.example .env
# 编辑 .env 填写实际配置
```

#### Step 5：启动服务

分别在 3 个终端中运行：

```bash
# 终端 1 - Jimeng API
cd backend/jimeng_service
npm run dev

# 终端 2 - 后端
cd backend
.venv\Scripts\activate
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# 终端 3 - 前端 (开发模式，可选)
cd frontend
npm run dev
```

## ⚙️ 配置说明

编辑 `backend/.env` 文件，以下为必填配置：

### Clash Verge 配置

| 配置项 | 说明 | 在哪里找 |
|--------|------|---------|
| `CLASH_CONTROLLER_URL` | Clash 控制器地址 | Clash Verge → 设置 → External Controller |
| `CLASH_SECRET` | 控制器密钥 | 同上 |
| `CLASH_PROXY_PORT` | 代理端口 | Clash Verge → 设置 → 代理端口 |
| `CLASH_PROXY_GROUP` | 代理组名称 | Clash Verge → 代理 → 选择要使用的组名 |

### Cloudflare 配置

用于接收注册验证码邮件，需要先部署 Cloudflare Worker（见 `cloudflare-worker/` 目录）。

| 配置项 | 说明 | 在哪里找 |
|--------|------|---------|
| `CF_ACCOUNT_ID` | Account ID | Cloudflare Dashboard → 概览 → 右侧 |
| `CF_KV_NAMESPACE_ID` | KV 命名空间 ID | Workers & Pages → KV → 你的命名空间 |
| `CF_API_TOKEN` | API 令牌 | My Profile → API Tokens → 创建令牌 |

### 可选配置

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `BROWSER_HEADLESS` | `false` | 是否隐藏浏览器窗口 |
| `MAX_CONCURRENCY` | `1` | 最大并发注册数 |
| `REGISTER_TIMEOUT` | `300` | 单次注册超时（秒）|
| `EXT_PROXY_FILE_PATH` | `./proxies.txt` | 外部代理文件路径 |
| `PROXY_POOL_KEYWORDS` | `HK,SG,JP,US` | 代理节点筛选关键字 |

## 📁 项目结构

```
jimeng-auto-register/
├── backend/
│   ├── app/                    # 核心后端代码
│   │   ├── api/routers/        # API 路由
│   │   ├── core/               # 配置、数据库
│   │   ├── models/             # 数据模型
│   │   ├── services/           # 业务逻辑
│   │   └── main.py             # 应用入口
│   ├── jimeng_service/         # Jimeng API 中间件 (Node.js)
│   ├── .env.example            # 配置模板（首次运行复制为 .env）
│   ├── proxies.example.txt     # 外部代理列表示例
│   ├── requirements.txt        # Python 依赖
│   ├── run.py                  # 命令行启动入口
│   └── run_gui.py              # GUI 模式启动入口
├── frontend/                   # React 前端
│   ├── src/                    # 前端源码
│   ├── dist/                   # 构建产物
│   └── package.json            # 前端依赖
├── cloudflare-worker/          # Cloudflare Worker（验证码邮件接收）
├── start.bat                   # Windows 一键启动脚本
└── README.md
```

## 🖥️ 使用说明

1. **启动后访问** `http://localhost:5173` 进入管理面板
2. **配置邮箱域名**：在「域名管理」页面添加你在 Cloudflare 配置的邮箱域名
3. **创建注册任务**：在「任务管理」页面设置注册数量、代理策略等参数
4. **查看结果**：在「账号管理」页面查看注册结果、导出数据

## ❓ 常见问题

### Playwright 安装失败

```bash
# 手动安装浏览器
cd backend
.venv\Scripts\activate
playwright install chromium
```

如果网络问题导致下载失败，可设置环境变量使用镜像：
```bash
set PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright
playwright install chromium
```

### 端口冲突

默认端口：`5173`（前端管理面板）、`8000`（后端 API）、`5100`（Jimeng API）

如端口被占用，可修改对应配置：
- 后端端口：修改 `start.bat` 中的 `--port 8000`
- Jimeng API 端口：修改 `backend/.env` 中的 `JIMENG_API_URL`

### 验证码接收失败

1. 确认 Cloudflare Worker 已正确部署
2. 确认 `.env` 中的 `CF_ACCOUNT_ID`、`CF_KV_NAMESPACE_ID`、`CF_API_TOKEN` 正确
3. 确认 Cloudflare Email Routing 已启用并指向你的 Worker

### Clash 代理连接失败

1. 确认 Clash Verge 已启动且代理正常工作
2. 确认 `.env` 中的 `CLASH_CONTROLLER_URL` 和 `CLASH_SECRET` 与 Clash Verge 设置一致
3. 确认 `CLASH_PROXY_PORT` 与 Clash Verge 的代理端口一致

## 📄 免责声明

本项目仅供学习和研究使用，请勿用于任何违反服务条款的行为。使用者需自行承担使用风险。
