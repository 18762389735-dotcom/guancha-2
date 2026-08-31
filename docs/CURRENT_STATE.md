# 观茶当前产品状态

更新日期：2026-08-31。

## 状态

Interview-ready release candidate after P9-5A。当前文档描述正式集成分支上的实际代码能力；最终发布仍需要按发布清单完成目标环境验证。

## 当前能力

### 1. Account

- 邮箱验证码注册。
- 邮箱密码登录。
- 同源 FastAPI Auth BFF。
- HttpOnly refresh cookie。
- 页面刷新后的会话恢复。
- 显式登出。
- 账号边界清理，避免浏览器本地状态串号。

### 2. Selection

- 用户归属的 Selection session。
- 候选与候选截图元数据。
- 提取与证据。
- 多候选决策。
- 追问与商家回复。
- 复判 / delta。
- Selection discovery 与 snapshot restore。

### 3. Long-term user state

- 用户偏好。
- 偏好证据。
- 茶仓。
- 泡茶日记。
- 对认证账号，服务端数据是权威来源，可跨设备恢复。

### 4. Ownership model

- CloudBase verified subject。
- `app_users` 内部 UUID。
- 服务端解析的 `user_id`。
- 前端不能选择 `user_id`。
- 出现 Bearer 时不会降级到匿名 owner。

### 5. Cross-device behavior

- 认证账号的持久业务状态从服务端恢复。
- 浏览器 stores 只承担缓存、UI 和恢复辅助作用。
- 账号切换与登出会清理账号相关的浏览器状态。
- 旧匿名数据不会自动认领。

### 6. Deferred items

- P9-4C：跨重启、跨设备的原始截图持久化与展示。
- 忘记密码 / 账号恢复。
- 只有真实 provider 要求时才实现的交互式 CAPTCHA 流程。
- 泡茶日记云端删除流程尚未纳入当前范围。

## 数据边界

- Selection 以 `selection_sessions` 为授权根；认证会话使用 `user_id`，历史匿名会话使用 `anonymous_client_id`。候选、图片、提取、决策、追问、商家回复和复判通过根会话授权。
- 偏好与偏好证据分别持久化到用户资源；茶仓与泡茶日记分别持久化到用户资源，不放进通用 JSON 文档。
- 认证账号的茶仓和泡茶日记由 PostgreSQL 提供跨设备持久化；`guancha.local-post-purchase.v1` 仅保留为缓存 / 恢复边界。
- IndexedDB 只保存待上传图片 Blob。短生命周期临时图片由后端私有临时存储处理，不是 P9-4C 的持久图片方案。
- `recent_preference_evidence` 作为当前选茶请求的输入快照提交；它与用户偏好证据资源相关，但不替代账号级偏好档案。

## 运行与验证

本地 Fake 演示需要：

```powershell
$env:GUANCHA_DATABASE_URL = '<local-postgresql-url>'
$env:GUANCHA_PROVIDER = 'fake'
backend\.venv\Scripts\python.exe backend\scripts\run_local.py
```

验证命令：

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests -q
node --check app.js
node --check frontend/auth-client.js
node --check frontend/api-client.js
node --test frontend/tests/*.test.js
```

数据库测试必须将 `TEST_DATABASE_URL` 指向独立测试数据库；不得使用生产数据库。真实 provider 的密钥不得写入文件、Git、前端或日志。

## 保护边界

- 认证、账号隔离、Selection ownership、Evidence 边界、Candidate identity、Merchant reply identity、Rejudge / Delta、Provider contract 和现有测试体系保持不变。
- 保留 `X-Client-Id` 匿名路径；认证 ownership 必须来自服务端验证后的用户身份。
- 不自动认领历史 anonymous data；如未来需要迁移，必须另行设计显式、可审计的流程。

## 当前已知限制

- 临时图片存储不是跨设备的持久对象存储，服务重启会影响尚未完成或仍可重试的 Job。
- Selection session 仍有既定过期策略，长期保留策略未在本阶段扩展。
- 原始截图的持久化展示属于 P9-4C，当前不宣称已完成。
- 忘记密码 / 账号恢复、交互式 CAPTCHA（除非真实 provider 触发）和泡茶日记云端删除不在当前阶段。
- 旧匿名浏览器数据不会自动认领或导入认证账号。

历史 Phase 9 身份与 ownership 审计见 [`docs/AUTH_MIGRATION_AUDIT.md`](AUTH_MIGRATION_AUDIT.md)；该文件是历史证据，不是当前状态的唯一说明。
