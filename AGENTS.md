# 观茶项目工作约定

## 当前项目定位

观茶已从比赛演示原型进入 Beta 产品化阶段。

目标是形成一个真实可注册、可登录、数据按用户隔离、可跨设备恢复的 AI 茶叶购买决策与冲泡记录产品。现有核心能力继续保留：用户偏好、本次需求、候选茶、商品截图、AI 提取、Evidence、多候选判断、关键未知、商家追问、商家回复、复判、茶仓、泡茶记录 / Journal 和 Brew Feedback。

认证服务确定为 Tencent CloudBase Authentication。后续 Web 客户端使用当前 `@cloudbase/js-sdk` v3；不主动采用已经标记为旧版的 v2 SDK。不得自行替换为 Supabase Auth、Auth0、Clerk、Firebase 或自建密码认证。CloudBase SDK / API 若未来更新，应在实现阶段重新核验官方文档，不永久锁死一个已过时的小版本 API。

后端继续使用 FastAPI、PostgreSQL 以及当前 Repository / Application / Domain 分层；前端继续使用当前 Vanilla JS SPA，不为了认证迁移 React、Vue 或 Next.js。

## 常用命令

```powershell
$env:GUANCHA_DATABASE_URL = '<local-postgresql-url>'
$env:GUANCHA_PROVIDER = 'fake'
backend\.venv\Scripts\python.exe backend\scripts\run_local.py
backend\.venv\Scripts\python.exe -m pytest backend\tests -q
node --check app.js
node --test frontend/tests/*.test.js
```

真实 Provider 只能通过服务端环境变量显式启用；自动测试始终使用 `fake` 或测试替身，不得读取或提交任何 API Key。认证测试不得调用真实 CloudBase。

## 活跃文档与历史文档

- `docs/CURRENT_STATE.md`：当前能力、未完成项和运行方式的唯一简明状态说明。
- `docs/AUTH_MIGRATION_AUDIT.md`：Phase 9-0 基于实际代码的身份、ownership、浏览器存储和认证迁移审计。
- 其他旧阶段文档、比赛 PRD、历史交接和旧审计仅作对照，不再自动构成“永不实现登录、云端茶仓或云端 Journal”等产品禁令。
- 研究结论、产品定位、长期架构和数据规则若发生冲突，以当前已确认任务、研究宪法、决策记录和本文件/活跃状态文档为准；不得因为旧文件存在就自动恢复旧结论。

## 必须保护的已有资产

不得对以下内容做无关重构或重新设计：

- 已验收的视觉基调、页面结构、样式、文案、资产和主要导航；
- 已完成的核心选茶链路及其加载、失败和恢复行为；
- Evidence 边界和来源标注；
- Candidate identity、Merchant reply identity、Rejudge / Delta；
- Provider contract、当前 Repository / Application / Domain 分层和测试体系。

认证产品化允许在确有需求时新增 auth 页面、account 状态、必要的身份相关导航，以及必要的 loading / unauthenticated / authenticated 状态，但不得借认证改造重新设计整个产品。

## 目录与实现边界

- `app.js`、`styles.css`、`index.html`：已验收的前端视觉与交互基线。
- `frontend/`：API Client、适配器、浏览器状态/存储以及后续最小 auth client 的边界。
- `backend/src/guancha_api/`：FastAPI、Application、Domain、Repository、Provider、任务和图片存储。
- `supabase/migrations/`：当前仅保存 PostgreSQL 迁移文件；认证迁移需要新建但本阶段不创建 migration。
- `backend/tests/`、`frontend/tests/`：当前回归基线。新增认证测试必须使用 fake verifier / synthetic claims，不连接真实认证服务。

## 身份与数据原则

- 当前匿名流程继续保留 `X-Client-Id`，但它只是可伪造的匿名 ownership 凭证，不得当作 authenticated user identity。本 Phase 9-0 不修改它的生成、注入或 API contract。
- 未来任何长期用户数据都必须绑定服务端解析出的 authenticated user；不得由前端传入任意 `user_id` 决定资源归属。
- Repository 必须依据服务端 `CurrentUser` 或明确的 anonymous owner context 做 ownership check；不能信任 URL、body、localStorage 或自定义 header 中的 user id。
- Authenticated-owned session invariant：未来如果 `selection_sessions.user_id IS NOT NULL`，该 Session 必须视为 authenticated-owned resource。此时即使请求没有 Authorization、携带匹配的 `X-Client-Id`，或 `selection_sessions.anonymous_client_id` 仍作为 transition / provenance 字段存在，也不得通过 anonymous ownership path 读取、修改或访问该 Session 及其派生资源。Legacy anonymous authorization 只允许 `selection_sessions.user_id IS NULL` 的资源；认证资源必须要求服务端解析出的 `CurrentUser`，且 `CurrentUser.id` 必须等于 `selection_sessions.user_id`，`X-Client-Id` 不得授予授权。
- 不把密码存入当前 PostgreSQL。CloudBase Access Token 不得写入 Git、日志、分析事件、错误详情或业务数据库；Provider Key 不得放到前端。
- authenticated 请求和 anonymous 请求必须有明确的 owner precedence。不能因为同一浏览器仍带有 `X-Client-Id` 就把匿名资源自动认领给账号。
- 默认不自动迁移任意 anonymous data 到新账号。历史数据如需迁移，必须作为后续显式、可审计、可撤销的 migration/import 流程。
- logout 和账号切换必须清理内存状态、账号作用域的缓存以及待上传图片状态，防止上一账号数据泄漏给下一账号。

## 开发与验证纪律

- 先完成最小可运行、可审查、可回滚的改动，再逐步扩展。
- 不得顺手重构无关文件、升级无关依赖、删除 `anonymous_clients` 或改变 Provider / Selection Decision Logic。
- 修改后运行最相关的测试、lint、类型检查、构建或启动验证；无法验证时明确说明原因和剩余不确定性。
- 新增依赖、联网安装、删除文件、修改数据库、修改权限或调整部署配置前，先获得确认。
- 生产代码、测试、日志和文档中都不得出现真实 API Key、Access Token、数据库 URL、用户截图内容或本机绝对路径。
