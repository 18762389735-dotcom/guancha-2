# 观茶 Beta 产品当前状态

更新日期：2026-08-30。本文记录 Phase 9-0 完成后的真实能力边界、运行方式和当前产品化状态；历史阶段文档仅作对照，不覆盖本文。

## 当前定位

观茶已进入 Beta 产品化阶段。目标是形成真实可注册、可登录、数据按用户隔离、可跨设备恢复的 AI 茶叶购买决策与冲泡记录产品。认证服务确定为 Tencent CloudBase Authentication。当前计划：frontend 使用 `@cloudbase/js-sdk` v3（Phase 9-3），backend 使用 CloudBase HTTP Token Introspection（Phase 9-1）；当前仍尚未实现任何认证能力。

## 已完成

- anonymous client ownership（当前以 `X-Client-Id` 实现的匿名会话 ownership）。
- Selection Session。
- Candidate。
- 图片上传、私有临时图片处理和图片读取/删除边界。
- Extraction。
- Evidence。
- Decision。
- Followup。
- Merchant Reply。
- Rejudge / Delta。
- Brew Feedback bridge：后端 `/brew-feedback/analyze`、匿名 replay/idempotency，以及前端低置信偏好证据的本地桥接；这不是云端 Journal 持久化。
- PostgreSQL 数据持久化与当前 Repository / Application / Domain 分层。
- Fake / OpenAI / MiMo Provider。
- 当前 Vanilla JS SPA 中的 Tea Warehouse / Journal UI；目前是浏览器本地用户数据与演示初始状态，不是用户级云端持久化。

## 尚未完成

- authenticated user。
- register/login/logout。
- account recovery。
- user-scoped warehouse persistence。
- user-scoped journal persistence。
- user preference cloud persistence。
- authenticated selection-session ownership。
- cross-device restore。

当前尚无用户账户表、CloudBase token verifier、`/api/v1/me` 或用户级茶仓/Journal 数据表；不应将上述能力描述为已实现。

## 当前数据边界

- 服务端当前的选茶链路由 anonymous client ownership 保护：`selection_session` 是根，候选、图片、提取、决策、追问、商家回复和复判通过直接字段或外键关系延续 ownership。
- O1 / O2 偏好、茶仓、Journal 和部分 UI session 仍保存在浏览器 localStorage；IndexedDB 只缓存待上传图片 Blob，用于本地上传恢复。
- `recent_preference_evidence` 会作为当前选茶会话快照提交到服务端，但还不是用户偏好档案。
- Brew Feedback replay 当前按匿名 client 与 feedback id 做幂等保存，不等同于用户级 Journal 或 Brew Record。
- 不存在 authenticated user，因此不存在可靠的账号级数据隔离和跨设备恢复。

## 运行方式

本地 Fake 演示：

```powershell
$env:GUANCHA_DATABASE_URL = '<local-postgresql-url>'
$env:GUANCHA_PROVIDER = 'fake'
backend\.venv\Scripts\python.exe backend\scripts\run_local.py
```

浏览器访问 `http://127.0.0.1:8000/`。真实 MiMo 或 OpenAI Provider 只能在服务端通过环境变量显式启用；变量值不得写入文件、Git、前端或日志。

## 验证命令

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests -q
node --check app.js
node --test frontend/tests/*.test.js
```

后端测试如使用数据库，必须通过 `TEST_DATABASE_URL` 指向独立测试库；本轮不连接真实 CloudBase，也不使用真实 API Key。

## 当前 Beta 保护边界

- 认证接入必须保护已验收视觉基调、核心选茶链路、Evidence 边界、Candidate identity、Merchant reply identity、Rejudge / Delta、Provider contract 和当前测试体系。
- 允许为确有产品需求新增 auth 页面、account 状态、必要的身份相关导航，以及 loading / unauthenticated 状态；不得借认证改造重设计整个产品，也不得迁移 React、Vue 或 Next.js。
- 当前仍保留 `X-Client-Id` 匿名路径；后续 authenticated ownership 必须由服务端验证后的用户身份决定，不得由前端传入 `user_id`。
- 第一版不得自动认领任意历史 anonymous data；如需迁移，必须另行设计显式、可审计的 migration/import 流程。

## 已知限制

- 临时图片存储为进程内实现；服务重启会影响尚未完成或仍可重试的 Job。
- 当前 Selection Session 有过期策略；authenticated restore 的保留期尚未确定。
- Tea Warehouse / Journal 当前含浏览器本地状态和演示 seed，尚未形成账号级云端数据边界。
- 尚无注册登录、账号恢复、CloudBase verifier、用户级数据库表或真实 auth 测试；这些属于后续阶段，不是本轮完成项。
- Phase 9-0 的详细实现前审计见 `docs/AUTH_MIGRATION_AUDIT.md`。
