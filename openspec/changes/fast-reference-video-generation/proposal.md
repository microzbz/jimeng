# Fast Reference Video Generation — Proposal (v2)

## Summary

将 ShukeAI 的"快速参考视频生成"功能迁移到 dreamina-auto-register 项目，通过 Patchright 浏览器自动化直接操作 Dreamina Web 端完成视频生成，作为现有 API 路径的补充。

## Motivation

即梦 API 逆向接口持续收紧，签名算法频繁变更、风控升级。浏览器自动化方案绕过 API 签名限制，直接操作 Web 端完成视频生成，保证能力持续可用。

## User Decisions (Confirmed)

| 决策项 | 确认值 | 来源 |
|--------|--------|------|
| 目标 URL | `https://dreamina.capcut.com/ai-tool/generate?type=video&workspace=0` | ShukeAI 字节码常量 |
| 默认模型 | `Dreamina Seedance 2.0 Fast` | ShukeAI 字节码常量 |
| 实施范围 | Phase 1-8 全部实施 | 用户确认 |
| Worker 池架构 | 独立 fast worker pool，与 API worker 完全隔离 | 用户确认 |
| 账号池策略 | 允许 gen_enabled/fast_enabled 重叠，共用 gen_locked_until 互斥 | 用户确认 |
| 账号消费策略 | one_time（默认），成功后设 fast_enabled=False | 用户确认 |
| 之前实现 | 已回滚，设计方案有问题需重新规划 | 用户确认 |

## Hard Constraints

1. **浏览器引擎**: Patchright（非原版 Playwright），导入路径 `patchright.async_api`
2. **ORM**: SQLAlchemy 2.0 异步模式（非 ShukeAI 的 Peewee 同步模式）
3. **调度**: asyncio Worker 池 + asyncio.Semaphore（非线程池）
4. **模型复用**: 复用 `Account` + `ContentGenerationJob`，不新建独立账号/任务表
5. **签名算法**: `md5("9e2c|{pathname}|web|8.4.0|{device_time}||1e67")`，双签名降级 11ac → 1e67
6. **ShukeAI 源码不可直接复制**: Python 3.13 .pyc 反编译不完整，需根据 MIGRATION_GUIDE + 字节码常量重新实现
7. **Worker 隔离**: fast_reference 必须使用独立 fast_queue + fast_workers，不能与 API worker 共用队列（避免队头阻塞）
8. **账号锁共享**: fast_reference 和 API 生成共用 gen_locked_until 作为全局互斥锁，防止同一 session 并发操作
9. **浏览器不池化**: 每个 fast job 新建 browser/context/page，finally 关闭（符合 BrowserStealth 隔离设计）
10. **前端无 TanStack Query**: 使用 useState/useEffect + setInterval 轮询模式（与 ContentGeneration.tsx 一致）
11. **前端无 Sheet 组件**: 素材库需用 Dialog 或自定义侧滑面板实现

## Soft Constraints

1. CSS 选择器可能随 Dreamina 页面更新而失效，需运行时验证
2. 状态码映射需实际测试确认（`ret == "0"` vs `status_code == 0`）
3. 浏览器并发默认 3 实例，可通过 `FAST_MAX_BROWSERS` 环境变量调整
4. 10 分钟账号锁可能短于视频生成时长，polling 时需延长锁

## Dependencies

- Phase 1 (数据层) → Phase 2 (素材库) + Phase 3 (执行器) + Phase 4 (轮询器) 可并行
- Phase 5 (服务集成) 依赖 Phase 1-4
- Phase 6 (API 路由) 依赖 Phase 5
- Phase 7 (前端) 依赖 Phase 6
- Phase 8 (测试) 依赖 Phase 1-7

## Risks

| 风险 | 等级 | 缓解 |
|------|------|------|
| CSS 选择器过时 | 高 | 实施时实际检查 Dreamina 页面结构，选择器需多候选 fallback |
| 浏览器提交后未捕获 history_id | 高 | 标记 ambiguous_submission，不自动重试避免重复扣费 |
| 现有 _acquire_account() 非原子 | 中 | 改为 conditional UPDATE + rowcount 校验 |
| 签名算法更新 | 中 | 双签名降级策略 (11ac → 1e67) |
| 浏览器被风控 | 低 | BrowserStealth + HumanBehavior + ProxyPool |
| 服务重启后 fast_reference 任务卡死 | 中 | 启动时恢复：queued 重入队，submitting 无 history_id 改 failed |
| 前端无 Sheet 组件 | 低 | 用 Dialog 或自定义 Tailwind 侧滑面板 |

## Success Criteria

1. 能通过浏览器自动化在 Dreamina Web 端提交视频生成任务
2. 能通过 HTTP 轮询（双签名）获取视频 URL 并下载到本地
3. 前端页面能创建/查看/管理 fast_reference 任务
4. 素材库支持上传、@mention 引用、别名解析
5. 独立 fast worker pool 不阻塞 API 生成任务
6. 账号租约原子性（conditional UPDATE），无并发竞争
7. one_time 策略正确设置 fast_enabled=False（不影响 gen_enabled）
8. 服务重启后 fast_reference 任务能正确恢复

## Scope

### In Scope
- 后端: 5 个新文件 + 5 个修改文件
- jimeng_service: 1 个新路由文件（history polling proxy）
- 前端: 2 个新文件 + 4 个修改文件
- 配置: 新增 8 个环境变量
- 数据库: 2 个新表 + 2 个表新增字段

### Out of Scope
- 自动化测试框架搭建（项目当前无测试框架）
- jimeng_service 核心签名算法修改
- Cloudflare Worker 修改

## Architecture Overview

```
ContentGenerationService (扩展)
  ├── api_queue  ──► api_worker_1..N  ──► JimengClient (现有路径)
  ├── fast_queue ──► fast_worker_1..M ──► FastReferenceBrowserExecutor
  │                   └── browser_semaphore(FAST_MAX_BROWSERS)
  ├── polling_task (现有 API 任务轮询)
  └── fast_polling_task (新增 fast_reference 轮询)
        └── FastReferencePoller (双签名: 11ac → 1e67)

Account Pool:
  gen_enabled  ──► API 生成池
  fast_enabled ──► 浏览器生成池
  gen_locked_until ──► 全局互斥锁（两个池共享）
```

## Implementation Phases

- Phase 1: 数据层（模型 + 迁移 + 配置）
- Phase 2: 素材库服务
- Phase 3: 浏览器执行器
- Phase 4: 视频轮询器
- Phase 5: 服务集成（独立 fast worker pool + fast polling loop）
- Phase 6: API 路由
- Phase 7: 前端页面
- Phase 8: 集成测试与调优

## Key Design Changes from v1

| 维度 | v1 (已回滚) | v2 (本次) |
|------|------------|-----------|
| Worker 池 | 共用 worker pool | 独立 fast_queue + fast_workers |
| 轮询 | 共用 polling_loop | 独立 fast_polling_task |
| 账号消费 | reusable | one_time (默认) |
| 账号获取 | 非原子 select+update | conditional UPDATE + rowcount |
| 启动恢复 | 无 | queued 重入队 / submitting 改 failed |
| 锁续期 | 无 | polling 时延长 gen_locked_until |
