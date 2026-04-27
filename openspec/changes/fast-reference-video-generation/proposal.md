# Fast Reference Video Generation

## Summary

将 ShukeAI 的"快速参考视频生成"功能迁移到 dreamina-auto-register 项目，通过 Patchright 浏览器自动化直接操作 Dreamina Web 端完成视频生成，作为现有 API 路径的补充。

## Motivation

即梦 API 逆向接口持续收紧，签名算法频繁变更、风控升级。浏览器自动化方案绕过 API 签名限制，直接操作 Web 端完成视频生成，保证能力持续可用。

## User Decisions (Confirmed)

| 决策项 | 确认值 | 来源 |
|--------|--------|------|
| 目标 URL | `https://dreamina.capcut.com/ai-tool/generate?type=video&workspace=0` | ShukeAI 实际代码 |
| 默认模型 | `Dreamina Seedance 2.0 Fast` | ShukeAI 代码常量 |
| 实施优先级 | Phase 1-7 顺序全部实施 | 用户确认 |
| 账号消费策略 | `reusable`（默认） | 用户确认 |

## Hard Constraints

1. **浏览器引擎**: 使用 Patchright（非原版 Playwright），导入路径 `patchright.async_api`
2. **ORM**: SQLAlchemy 2.0 异步模式（非 ShukeAI 的 Peewee 同步模式）
3. **调度**: asyncio Worker 池 + asyncio.Semaphore（非线程池）
4. **模型复用**: 复用 `Account` + `ContentGenerationJob`，不新建独立账号/任务表
5. **签名算法**: `md5("9e2c|{pathname}|web|8.4.0|{device_time}||1e67")`，双签名降级 11ac → 1e67
6. **ShukeAI 源码不可直接复制**: Python 3.13 .pyc 反编译不完整，需根据 MIGRATION_GUIDE 伪代码重新实现

## Soft Constraints

1. CSS 选择器可能随 Dreamina 页面更新而失效，需运行时验证
2. 状态码映射需实际测试确认（`ret == "0"` vs `status_code == 0`）
3. 浏览器并发默认 3 实例，可通过 `FAST_MAX_BROWSERS` 环境变量调整

## Dependencies

- Phase 1 (数据层) → Phase 2 (素材库) + Phase 3 (执行器) 可并行
- Phase 4 (轮询器) 独立
- Phase 5 (服务集成) 依赖 Phase 1-4
- Phase 6 (前端) 依赖 Phase 5
- Phase 7 (测试) 依赖 Phase 1-6

## Risks

| 风险 | 等级 | 缓解 |
|------|------|------|
| CSS 选择器过时 | 中 | 实现时实际检查页面结构 |
| 签名算法更新 | 中 | 双签名降级策略 |
| 浏览器被风控 | 低 | BrowserStealth + HumanBehavior + ProxyPool |

## Success Criteria

1. 能通过浏览器自动化在 Dreamina Web 端提交视频生成任务
2. 能通过 HTTP 轮询获取视频 URL 并下载到本地
3. 前端页面能创建/查看/管理 fast_reference 任务
4. 素材库支持上传、@mention 引用、别名解析
5. 并发控制正常工作（Semaphore 限制浏览器实例数）
6. 账号租约原子性，无并发竞争

## Scope

### In Scope
- 后端: 5 个新文件 + 3 个修改文件
- 前端: 1 个新页面 + 路由注册 + API 客户端扩展
- 配置: 新增 8 个环境变量

### Out of Scope
- 自动化测试（项目当前无测试框架）
- jimeng_service 签名算法修改
- Cloudflare Worker 修改

## Implementation Phases

- Phase 1: 数据层（模型 + 迁移 + 配置）
- Phase 2: 素材库服务
- Phase 3: 浏览器执行器
- Phase 4: 视频轮询器
- Phase 5: 服务集成
- Phase 6: 前端页面
- Phase 7: 端到端测试与调优
