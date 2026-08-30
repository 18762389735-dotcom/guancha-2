# Phase 9-0 / 9-3 — Auth and Selection Ownership Audit

审计日期：2026-08-31  
审计范围：当前 Vanilla JS SPA、FastAPI、Application / Domain / Repository、PostgreSQL migrations、浏览器存储和现有测试边界。  
本文件先记录 Phase 9-0 的实现前事实，再记录 Phase 9-1/9-2 的认证与 Selection ownership 边界以及 Phase 9-3 的前端认证边界。本轮没有接入真实 CloudBase、没有使用真实 token 或生产数据库；Phase 9-3 使用 Fake SDK 测试，真实 CloudBase console/runtime smoke 仍待单独审查。

## 结论摘要

当前前端已通过 `frontend/auth-client.js` 封装 CloudBase Web SDK v3.8.2，使用 SDK 维护 session，临时读取 access token 并只为 owner-scoped Selection 请求注入 Bearer。后端以 `OwnerContext` 表示 authenticated / anonymous owner：Authorization 存在时必须通过 CloudBase-compatible `TokenVerifier`，再由 `app_users.id` 作为内部 owner；只有 Authorization 完全缺失时才解析 `X-Client-Id`。匿名 header 仍是客户端可控制的凭证，不能当作 authenticated identity。

当前选择链路的 ownership 以 `selection_sessions` 为根：认证 session 使用 `user_id`，legacy anonymous session 使用 `anonymous_client_id`；候选、图片、提取、任务、Evidence、决策、追问、商家回复和复判通过 FK 解析回该 root。Tea Warehouse、Journal、O1 / O2 等用户长期状态仍主要在浏览器 localStorage，不能当作账号级云端持久化。

前端保持“认证用户 owner 优先、匿名 owner 兼容”的双轨过渡：required-auth 模式 token 缺失、token supplier 失败或 `/me` 不可用时不会退回匿名 Selection。既有 `anonymous_clients` 保留，且不自动把任意匿名数据认领到新账号。

## 1. Anonymous identity：创建、保存、注入和校验

### 1.1 实际代码位置

| 环节 | 文件 | 关键位置 / 函数 | 实际行为 |
| --- | --- | --- | --- |
| 生成与读取 | `frontend/api-client.js` | `getOrCreateClientId()` | 读取 `localStorage`；已有合法 UUID v4 则复用，否则通过 `createIdempotencyKey()` 生成并写回。 |
| localStorage key | `frontend/api-client.js` | `getOrCreateClientId()` | key 为 `guancha.anonymous-client-id.v1`。 |
| API client 创建 | `app.js` | `bootAuth()` / `rebuildApiClient()` | 先读取 public config、恢复 SDK session，再用 async token supplier 创建 API client。匿名兼容时仍取得 client id。 |
| 请求注入 | `frontend/api-client.js` | `createApiClient()` 内的 `ownerHeaders()` | owner-scoped Selection 请求有 token 时只注入 `Authorization: Bearer ...`，不以 `X-Client-Id` 授权；required-auth 模式无 token 或 token supplier 失败时本地拒绝。Brew Feedback、events 和 public config 不自动附带 Bearer。 |
| 前端接口入口 | `frontend/api-client.js`、`frontend/adapters.js`、`app.js` | API adapter 与页面动作 | Selection、图片、分析、追问、回复、复判、Brew Feedback 等调用复用该 client。 |
| FastAPI dependency | `backend/src/guancha_api/auth/dependencies.py`、`backend/src/guancha_api/api/v1/routes.py` | `get_owner_context()`、`Owner`；`ClientId` 仅保留给 Brew Feedback | Authorization 存在时必须走 `_resolve_authenticated_user()`；完全缺失时才解析 `X-Client-Id` 为 anonymous owner。 |
| authenticated mapping | `backend/src/guancha_api/auth/dependencies.py`、`backend/src/guancha_api/repositories/postgres.py` | `_resolve_authenticated_user()`、`resolve_or_create_app_user()` | 只使用已验证 `VerifiedIdentity.external_subject` 幂等解析 `app_users.id`；前端不能提供 internal `user_id`。 |
| repository ownership | `backend/src/guancha_api/repositories/postgres.py` | `_require_owned_resource()`、`_require_owned_session()`、`_require_owned_candidate()`、`require_*_for_owner()` | 将 `OwnerContext` 与 Selection root session 的 `user_id` 或 `anonymous_client_id` 比较；派生资源先 join 回 root，不以重复 anonymous 列作为认证授权依据。 |
| owner row 写入 | `backend/src/guancha_api/repositories/postgres.py` | `create_client()` | 在创建 Selection Session 或保存 Brew Feedback replay 等写操作时，以 `anonymous_clients.id` 幂等插入；只读请求不一定创建该行。 |

`/api/v1/events` 和 `/api/v1/config/public` 不要求 `ClientId`。前者是经过限制的产品分析事件，后者是公开配置，不应被误认为业务资源 ownership。管理接口另有环境变量 bearer token，它与用户认证无关。

### 1.2 当前请求链

```text
app.js 启动
  → GET /api/v1/config/public
  → GuanchaAuth（CloudBase v3.8.2 SDK）恢复 session
  → authenticated：临时 token → GET /api/v1/me → account marker / local clear → Bearer Selection
  → unauthenticated 且 auth.required=false：GuanchaApi.getOrCreateClientId() → X-Client-Id anonymous Selection
  → unauthenticated 且 auth.required=true：停留登录 UI，不发匿名 Selection
  → Selection 路由的 Owner dependency：Bearer 优先，否则 X-Client-Id
  → verified subject → app_users.id，或 UUID anonymous owner
  → Repository 按 selection_sessions root / FK 链授权
  → PostgreSQL app_users / anonymous_clients / selection_sessions
```

这是一种 pseudonymous client ownership，不是 authenticated user identity。拥有或伪造同一个 UUID 的客户端即可尝试以该匿名 owner 发起请求；UUID 的不可预测性降低猜测概率，但不改变“凭证由客户端控制”的事实。

## 2. Selection 数据 ownership 链

### 2.1 关系链

```text
app_users.id                                      [认证根 owner]
  └─ selection_sessions.user_id                   [直接保存认证 ownership]

anonymous_clients.id                              [legacy anonymous root]
  └─ selection_sessions.anonymous_client_id       [直接保存匿名 ownership]
      └─ candidates.selection_session_id          [FK 间接继承]
          ├─ candidate_images.candidate_id        [FK 间接继承]
          ├─ analysis_jobs.candidate_id / candidate_image_id / extraction_version_id
          │                                             [FK 间接继承]
          ├─ extraction_versions.candidate_id / source_image_id
          │                                             [FK 间接继承]
          │   └─ evidence_items.extraction_version_id / source_image_id
          │                                             [FK 间接继承]
          └─ candidate_decisions.candidate_id      [与 decision version 共同继承]

selection_sessions
  └─ decision_versions.selection_session_id
      ├─ decision_versions.anonymous_client_id     [直接重复保存 owner]
      ├─ candidate_decisions.decision_version_id
      ├─ question_generation_runs.decision_version_id
      └─ followup_questions.decision_version_id / selection_session_id / candidate_id

selection_sessions
  └─ merchant_replies.selection_session_id
      ├─ merchant_replies.decision_version_id / candidate_id
      └─ merchant_claims.merchant_reply_id / candidate_id / conflicts_with_evidence_id

selection_sessions
  └─ decision_deltas.selection_session_id
      ├─ old_decision_version_id / new_decision_version_id
      └─ merchant_reply_id / merchant_reply_ids
```

### 2.2 直接字段与间接继承

| 数据 | ownership 形式 | 当前审计结论 |
| --- | --- | --- |
| `selection_sessions` | `user_id` FK `app_users` 或 `anonymous_client_id` FK `anonymous_clients` | 当前唯一 ownership root；check 约束要求二者至少一个非空，但不要求同时存在。旧 anonymous 行不回填 `user_id`。 |
| `candidates` | `selection_session_id` FK | 通过 session 间接继承；repository 会检查 session owner。 |
| `candidate_images` | `candidate_id` FK | 通过 candidate → session 继承；图片读取、删除会回查 session owner。 |
| `analysis_jobs` | candidate / image / extraction FK | 通过候选链继承；后台 worker 本身按原子 claim job，不以浏览器身份运行。 |
| `extraction_versions` | candidate / source image FK | 通过候选链继承；repository 有 extraction owner check。 |
| `evidence_items` | extraction / source image FK | 通过 extraction 链继承。 |
| `decision_versions` | `selection_session_id` + nullable `anonymous_client_id` | session FK 是授权根；`anonymous_client_id` 仅保留匿名 provenance，认证新行写 NULL。 |
| `candidate_decisions` | decision version / candidate / extraction FK | 通过决策版本和候选关系继承。 |
| `question_generation_runs` | decision version FK | 通过决策版本继承。 |
| `followup_questions` | decision / session / candidate 多个 FK | 通过多个关系继承，写入时由 application / repository 校验当前决策。当前 schema 没有一个 composite FK 证明这三个 FK 总是指向同一 session/candidate，是后续完整性加固点。 |
| `merchant_replies` | session / decision / question / candidate FK + nullable `anonymous_client_id` | session FK 是授权根；匿名新行保留 client 值，认证新行写 NULL；认证幂等使用 session-rooted partial unique index。 |
| `merchant_claims` | merchant reply / candidate / evidence FK | 通过商家回复及其证据关系继承。 |
| `decision_deltas` | session / old-new decision / merchant reply FK | 通过 session 根 owner 继承，读取时检查 session owner。 |
| `brew_feedback_replays` | `anonymous_client_id` 直接 FK | 是匿名 feedback replay / 幂等缓存，不是完整 Brew Record 或 Journal。 |
| `ai_call_logs` | `analysis_job_id` FK | 是管理审计数据，通过任务和候选链间接关联；不提供用户认证。 |

当前实现的主要保护点是 repository 查询中的 root ownership join，而不是仅依赖资源 ID。所有 authenticated 读写都从服务端解析出的 `OwnerContext.user_id` 回查 session 根，不能把 `user_id` 加进请求参数，也不能让历史 `anonymous_client_id` 绕过该检查。

## 3. 浏览器本地用户数据盘点

| 存储 | 实际结构 / 内容 | 是否长期用户数据 | 云端迁移建议 | 当前边界 |
| --- | --- | --- | --- | --- |
| `localStorage['guancha.ui-session.v1']` | schema 2；当前 screen、openDrink、activeSelectionFlow、ownershipChoice、activeCandidateId，以及 O1 数组、O2 flavors / sweetness。 | O1 / O2 是长期偏好候选；screen 和 flow 是 UI 恢复状态。 | O1 / O2 后续进入 user-scoped preferences；UI session 默认仍可本地，不能作为权限依据。 | `frontend/stores.js` 的 UI store。 |
| `localStorage['guancha.local-post-purchase.v1']` | schema 2；warehouse 最多 100 条、journalRecords 最多 365 条、history 最多 100 条、selectedTeaId。茶条目含名称、类型、产地、烘焙、香气、来源、状态及部分 extraction / candidate / decision refs；Journal 含 date、teaId、infusions、plan、feedback、suggestion、createdAt。 | 是长期用户内容。 | 应迁移为 user-scoped warehouse items、brew records / Journal；不能靠当前匿名 client 或前端 `user_id` 作为最终归属。 | 当前无 backend CRUD；仍是浏览器本地状态。 |
| `localStorage['guancha.selection-bridge.v1']` | schema 3；仅保存 session UUID、候选锚点、A-E label/status/error、job / extraction UUID、最多 2 个图片锚点、merchant reply / decision / question / rejudge UUID 等。会剥离 need、extraction、reasons、risk、evidence、问题答案、raw merchant text、preview URI、Blob 和 secrets。 | 是 UI / 远程会话恢复桥，不是完整用户资料。 | authenticated restore 应由服务端 user-scoped session 提供；本地 bridge 需账号命名空间或登录/登出时清理和重建。 | 还有 legacy `guancha-prototype-v2` allowlist 迁移，迁移后删除旧 key。 |
| `localStorage['guancha.preference-evidence.v1']` | schema 2；最多 12 个、最近 90 天的低置信偏好证据 anchor，含 target、value、polarity、来源 brew session、timestamp。 | 是偏好推断的长期/半长期证据。 | 后续设计为 user-scoped preference evidence；现阶段只能作为本地 evidence，并可作为当前 session 的 `recent_preference_evidence` 快照。 | 不是 user profile；服务端只保存当前 selection snapshot。 |
| `localStorage['guancha.anonymous-client-id.v1']` | 一个 UUID v4。 | 是匿名身份恢复凭证，不是用户资料。 | 登录后不能把它当 authenticated identity；匿名模式继续兼容，切换策略需显式定义。 | `frontend/api-client.js` 生成和读取。 |
| `localStorage['guancha_onboarding_status']` | `not_started`、`completed`、`skipped`。 | 产品状态，账号归属取决于未来体验。 | 可按账号保存或作为设备级 onboarding；不应影响资源 authorization。 | `frontend/onboarding-routing.js` 单独管理。 |
| `sessionStorage['guancha.analytics-session.v1']` | 当前浏览器 tab / analytics session id。 | 非长期用户数据。 | 不迁移到用户数据；继续与 ownership 分离。 | 通过 `X-Analytics-Session-Id` 发送。 |
| IndexedDB `guancha.pending-images.v1` / object store `images` | 待上传图片文件 / Blob 缓存，帮助本地上传恢复。 | 临时本地数据，可能含用户截图。 | 不自动导入账号；登录、登出和账号切换必须有清理或明确归属策略。 | 不是长期档案；服务端图片仍是匿名 session 资源。 |
| 内存 `runtimeImages` | 当前运行时图片对象。 | 否，临时 UI 状态。 | 不迁移。 | 页面刷新即丢失。 |
| `state.brew` | 当前泡茶进行中的状态未进入 `saveState()` 的持久化结构。 | 进行中临时状态。 | 默认本地临时即可；提交后的 brew record 才需要云端规划。 | 不能误称为已持久化 Journal。 |
| `state.need` | 新建 session 前为临时需求；提交后由服务端保存进 `selection_sessions.need`。local bridge sanitizer 不保存自由文本 need。 | 当前 session 数据是服务端长期/阶段性数据；local need 不是完整备份。 | authenticated session 由 user owner 保护。 | 需区分 session snapshot 与 user profile。 |
| `feedbackAnalysis` / Brew Feedback | 分析响应在内存中挂到 Journal record；`journalAnchor` 不保存完整分析；后端 replay 保存 response JSON，key 为 anonymous client + feedback id。 | 分析结果本身可能是用户内容，但当前 replay 不是 Journal。 | 后续作为明确的 user-scoped Brew Feedback / Journal 数据设计。 | 当前是 bridge，不是云端 Journal。 |

另一个实际风险是 `app.js` 在 warehouse / journal 为空或不是数组时会用 `defaultState` 中的 `spring`、`peony`、`puer` 和 `demo-0804` 等演示 seed 填充界面。认证接入前必须区分 demo seed、设备本地内容和账号云端内容，否则可能把演示数据当成新用户数据上传。

扫描未发现独立的 `settings` 持久化 store；目前最接近 settings 的是 `guancha_onboarding_status` 和 `guancha.ui-session.v1` 中的 UI / onboarding 状态。它们都不能作为资源授权依据，是否按账号同步应在后续产品决策中单独确定。

## 4. CloudBase Authentication 最小接入点（Phase 9-3 已实现前端边界）

认证服务确定为 Tencent CloudBase Authentication。前端通过官方 CDN 固定使用 `@cloudbase/js-sdk` v3.8.2：`https://static.cloudbase.net/cloudbase-js-sdk/3.8.2/cloudbase.full.js`（实现时验证 HTTP 200）。不使用已经标记为旧版的 v2 SDK，也没有安装 bundler / npm auth 依赖。CloudBase SDK / API 若未来更新，应在对应实现阶段重新核验官方文档，不永久锁死一个已过时的小版本 API。

### 4.1 Frontend：最小侵入边界

Phase 9-3 已新增 `frontend/auth-client.js`，职责只包括：

1. 封装当前 `@cloudbase/js-sdk` v3 的注册、登录、登出、当前认证状态和 access token 获取；
2. 对外提供稳定的 `getCurrentUser()` / `getAccessToken()` / auth-state subscription，不让 `app.js` 直接依赖 SDK 细节；
3. 不把 refresh token 或 access token 写入 Git、日志、analytics payload 或业务 localStorage；SDK 的安全持久化策略需按官方能力和部署环境验证；
4. 登录、登出和账号切换时通知应用清理内存、account-scoped local state、pending image 状态和正在运行的 poller，然后重新 hydrate。

根目录 `index.html` 通过固定 CDN 先加载 SDK，再加载 auth client；`app.js` 仅使用其最小 API 和 API client 注入的 token getter，不直接保存或传播 session。没有迁移 SPA 框架，也没有创建 `frontend/index.html`。

`frontend/api-client.js` 已增加 owner-scoped token getter：有经过 SDK 获取的 token 时注入 `Authorization: Bearer <token>`；可选认证且无 token 时才继续发送 `X-Client-Id`。`/me` 总是 Bearer；`/events` 不自动接收 Bearer；Brew Feedback 保持 `X-Client-Id`。两种 owner 有明确优先级，不能因为 header 仍存在就自动把匿名资源认领给账号。

### 4.2 Backend：Phase 9-1 Production TokenVerifier 与 Phase 9-2 OwnerContext

Phase 9-1 已在当前 `backend/src/guancha_api/` 分层中建立认证 kernel，不把 CloudBase 细节散落到 routes 或 repository。`TokenVerifier` interface、CloudBase adapter、`CurrentUser`、`app_users`、`/api/v1/me` 和 auth error contract 属于 Phase 9-1；本轮 Phase 9-2 在其上增加 `OwnerContext` 和 Selection Session ownership。

Production `TokenVerifier` 的当前技术决策：

```text
region = ap-shanghai / ap-guangzhou:
GET https://{CLOUDBASE_ENV_ID}.api.tcloudbasegateway.com/auth/v1/token/introspect

region = ap-singapore:
GET https://{CLOUDBASE_ENV_ID}.api.intl.tcloudbasegateway.com/auth/v1/token/introspect

Authorization: Bearer <incoming access token>
```

`CLOUDBASE_REGION` 默认使用 `ap-shanghai`，当前支持 `ap-shanghai`、`ap-guangzhou` 和 `ap-singapore`。未知 region 必须作为配置错误处理，不得猜测 endpoint。

成功验证时：

- 响应中必须存在非空 `sub`；
- `sub` 作为 CloudBase external subject；
- 如果响应提供 `scope`，它必须是非空字符串，并按空白分隔为 token；其中包含精确 token `anonymous` 时必须视为 invalid credentials；
- 缺少 `scope` 时保留当前兼容行为；其他 scope token（例如 `user sso`）不写入 `VerifiedIdentity`，只允许可信 `sub` 继续进入后续映射；
- CloudBase identity with explicit anonymous scope must not become `app_users` in the current registered-account product；
- 服务端通过 `sub` 幂等 resolve / create `app_users`；
- 前端不得提供 internal `user_id`。

无效 token 时，introspection 返回空对象即视为 invalid credentials。网络错误或 CloudBase 服务不可用时：

- 不得降级为匿名 user；
- 不得相信未经验证的 token；
- 返回明确的 authentication service unavailable error。

安全规则：

- 不在日志记录 `Authorization`；
- 不存储 access token；
- 不存储 refresh token；
- 不自行验证客户端提供的 `user_id`；
- 不仅做 JWT decode 就认为 token 有效；
- 除非未来明确启用了可信 CloudBase Gateway authentication，否则 raw decoded JWT claims 不得作为认证依据。

配置只允许保存：

- `CLOUDBASE_ENV_ID`；
- `CLOUDBASE_REGION`。
- `CLOUDBASE_PUBLISHABLE_KEY`（仅作为 `/config/public` 的浏览器公开 credential）；
- `GUANCHA_AUTH_REQUIRED`。

不得提交真实 token、API Key、SecretId 或 SecretKey。Phase 9-1 使用 `FakeTokenVerifier`，测试不得访问真实 CloudBase。

Phase 9-2 的 Application / Repository Selection 边界接收服务端派生的 owner context，而不是 body 或 URL 中的 `user_id`。authenticated 请求使用 `CurrentUser.id` 形成 `OwnerContext`；anonymous 请求才使用当前 `X-Client-Id`。`X-Client-Id` 可继续作为兼容性 provenance，但不可覆盖 authenticated owner；同一请求只在 Authorization 完全缺失时进入 anonymous 分支。

### 4.3 测试边界

后续 auth 测试必须使用 fake verifier 和 synthetic claims，覆盖缺失、过期、伪造 token、用户 A/B 隔离、anonymous 回归、登出清理和跨设备恢复。测试不能调用真实 CloudBase，也不能需要真实 access token、refresh token 或 API key。

### 4.4 FastAPI Template reference boundary

Phase 9-1 实现时将只读参考：

`F:\观茶最新\full-stack-fastapi-template-master\`

具体允许参考范围将在 Phase 9-1 任务单中指定。本轮不读取该参考项目。

## 5. PostgreSQL backward-compatible migration 策略

Phase 9-1/9-2 已采用 additive migration；没有删除 `anonymous_clients`，没有 backfill 旧 anonymous session，也没有新建每个派生表的 `user_id`。

### 5.1 Authentication Kernel 第一批新增结构（Phase 9-1）

Phase 9-1 已新增：

```text
app_users
  id                uuid primary key                 -- 内部稳定用户 id
  cloudbase_user_id text not null unique              -- CloudBase 外部 subject
  created_at        timestamptz not null
  updated_at        timestamptz not null
```

`app_users` 只保存 CloudBase 外部身份映射和产品需要的非敏感元数据，不保存密码。已验证的 external subject 由服务端幂等 resolve/create 该映射；现有用户解析使用 INSERT ... ON CONFLICT DO NOTHING RETURNING，冲突后 SELECT，不对每次认证做 no-op UPDATE。

### 5.2 与现有 anonymous_clients 共存

- Phase 9-2 migration `20260830110000_phase9_2_selection_ownership.sql` 新增 nullable `selection_sessions.user_id uuid references app_users(id)`，并增加 `user_id IS NOT NULL OR anonymous_client_id IS NOT NULL` check、authenticated idempotency partial unique index 和 authenticated restore index。
- 第一阶段不删除 `anonymous_clients`，不删除 `selection_sessions.anonymous_client_id`，不对既有匿名 session 做任意 backfill；旧行继续保持 `user_id IS NULL`。
- `selection_sessions` 的新 authenticated 行写 `user_id`、把 `anonymous_client_id` 留空；匿名新行继续写 `anonymous_client_id`、把 `user_id` 留空。二者不要求同时存在。
- `decision_versions.anonymous_client_id` 和 `merchant_replies.anonymous_client_id` 已改为 nullable，仅作为匿名 provenance；认证新行写 NULL。它们不参与 authenticated authorization。
- authenticated 读写必须以 `selection_sessions.user_id` 为根回查；派生表不新增镜像 `user_id`，而由 repository ownership helper 沿 FK join 回 root。
- 未来账号级表（例如 `user_preferences`、`tea_warehouse_items`、`brew_records` / Journal、必要时 `preference_evidence`）应直接使用 `user_id NOT NULL`，由服务端生成，不通过匿名 client 复用。

### 5.3 迁移顺序建议

```text
add app_users + indexes (Phase 9-1) [completed]
  → fake verifier / CurrentUser + /api/v1/me
  → add nullable selection_sessions.user_id (Phase 9-2) [completed]
  → authenticated session create/read 的 user owner [completed]
  → authenticated derived-resource ownership checks [completed for current Selection routes]
  → frontend auth state 与本地状态边界 (Phase 9-3)
  → preferences / warehouse / Journal 的 user-scoped CRUD (Phase 9-4)
  → 最后评估 anonymous_client_id nullable 化及历史迁移工具
```

这样旧 anonymous path 可以继续运行，新路径可以逐步灰度；本轮不执行前端 auth UI 或云端长期用户数据迁移。

## 6. Legacy anonymous data 策略

第一版默认：**不要自动把任意 anonymous data 认领到新用户账号。**

- 既有 anonymous selection session 继续只由原始 `X-Client-Id` 访问，直到现有过期策略结束；新账号默认从空的 cloud scope 开始。
- 同一浏览器登录时，不因为 localStorage 中存在匿名 client id、selection bridge、warehouse 或 Journal 就静默上传或绑定到账号。
- 如果以后需要历史迁移，应另做一次显式 migration/import：由用户主动确认，限定数据范围，展示预览，记录审计事件，具备幂等和失败回滚策略，并要求足够的会话证明；不能让用户粘贴一个 client UUID 就完成认领。
- 本地 warehouse / Journal 和 IndexedDB 待上传图片也应采用显式导入或清晰的“保留在此设备 / 导入账号”流程；不属于 Phase 9-0 或默认 Phase 9-1 的隐式副作用。

## 7. 风险清单

| 风险 | 当前事实 | 后续必须做的边界 |
| --- | --- | --- |
| IDOR / resource ownership | 当前 repository 对 session、candidate、extraction、image、reply、delta 等有匿名 owner 检查，已有 foreign-client 测试覆盖部分路径；未来若新增 auth 查询直接按资源 id 读取，可能绕过 root join。 | 所有 authenticated 资源读写从 verified `CurrentUser` 回查 session/user owner；补齐每种派生资源的 A/B 隔离测试。 |
| token spoofing | Selection 已使用服务端 verifier；`X-Client-Id` 仍是客户端可改的匿名凭证。 | 继续只信任 verified subject；拒绝客户端自报 uid；Authorization 存在但无效或服务不可用时不得 anonymous fallback。 |
| user_id injection | 当前 Selection API 没有 user_id ownership 参数；OwnerContext 只由服务端生成。 | 保持禁止从 body、URL、localStorage 或自定义 header 接受 owner user_id；内部 user id 只由 verified external subject 映射产生。 |
| anonymous/authenticated ownership collision | 同一请求可同时携带 X-Client-Id 和 bearer。 | 已定义并测试 owner precedence：Authorization 存在就必须认证；认证 owner 胜出；匿名路径只在 Authorization 完全缺失时使用，且不自动 claim。 |
| localStorage 多账号串数据 | 当前 key 是 origin 级全局 key，不带 account namespace；O1/O2、warehouse、Journal、selection bridge 可被下一账号看到。 | 登录、登出、切换时清理并重新 hydrate，或建立严格的 account-scoped keys；先处理 demo seed 和 pending images。 |
| logout 后旧状态泄漏 | 当前 app 会保留内存 state、本地 stores、IndexedDB pending image 和可能运行中的 poller。 | logout 时清空内存与账号缓存，停止 poller，清理/隔离待上传 Blob，再加载空账号状态。 |
| tests 依赖真实 CloudBase | 当前实现和 Phase 9-2 测试均使用 fake verifier；没有真实 CloudBase 调用。 | 保持 fake verifier、synthetic identities 和网络禁用条件；保留匿名回归基线。 |
| 生产 token 日志泄漏 | 当前 auth kernel 不保存 token；Provider Key 已约束为服务端环境变量。 | 未来 middleware、反向代理、错误处理、analytics、DB audit 全链路 redact `Authorization`、access token 和 refresh token。 |
| demo seed 污染 | `app.js` 可向空 warehouse / Journal 填充演示记录。 | 区分 demo-only seed 与用户数据；不得把 seed 静默上传到新账号。 |
| pending image 归属不清 | IndexedDB 缓存的是本地 Blob，服务端图片当前属于匿名 session。 | 定义登录/登出时保留、清除或显式导入规则；禁止跨账号复用 pending upload。 |
| session expiry 与跨设备恢复 | authenticated Selection 已可按 `user_id` 跨设备读取；当前仍沿用 session 过期策略，尚无 session list / restore UI。 | 决定 authenticated session 保留期、撤销和前端恢复界面；不要把本地 Warehouse / Journal 当作已同步。 |
| cross-link integrity | `followup_questions` 等结构有多个独立 FK，未以 composite FK 完全证明所有关系指向同一上下文。 | 未来在不扩大本轮范围的前提下，增加 service 校验或数据库完整性约束并补测试。 |

## 8. 分阶段迁移范围

### Phase 9-1 — Authentication Kernel

只允许：

- 确认 CloudBase Authentication 官方服务端验证方式；
- backend `TokenVerifier` interface；
- CloudBase verifier adapter；
- fake verifier / synthetic claims；
- `CurrentUser` value / dependency；
- `app_users`；
- `/api/v1/me`；
- auth error contract；
- backend auth unit / integration tests。

Phase 9-1 不修改现有 Selection Session ownership，不实现完整 register / login / logout UI，不修改 warehouse / Journal，也不做 localStorage account migration。

### Phase 9-2 — User Ownership

本轮已处理：

- `selection_sessions.user_id`；
- authenticated / anonymous `OwnerContext`；
- authenticated Session create / read；
- derived-resource ownership；
- IDOR 与 user A-B isolation；
- authenticated-owned resources 禁止 anonymous fallback。

已实现的不变量：

```text
selection_sessions.user_id IS NOT NULL
    → require authenticated CurrentUser
    → CurrentUser.id must equal selection_sessions.user_id
    → X-Client-Id must not grant authorization

selection_sessions.user_id IS NULL
    → legacy anonymous ownership may use X-Client-Id
```

Phase 9-2 测试覆盖：authenticated A 创建 session 后，去掉 Bearer 但保留任意或历史 `X-Client-Id`，请求仍被拒绝；User B 及 anonymous client 也不能访问该 session 或其派生资源。数据库矩阵和完整派生链测试在未配置 `TEST_DATABASE_URL` 时保持 skip。

### Phase 9-3 — Auth UI

已处理：

- `frontend/auth-client.js`；
- CloudBase Web SDK；
- `Authorization: Bearer` injection；
- register；
- login；
- logout；
- auth state；
- account switch；
- localStorage / IndexedDB / poller isolation。

实现边界：CloudBase SDK session 由 SDK 自行持久化；Guancha 不写 access token、refresh token 或密码到浏览器业务存储。public config 仅公开 env ID、region、publishable key、required/configured/provider；`GUANCHA_AUTH_REQUIRED=true` 而浏览器配置不完整时，UI 显示“登录服务暂未配置”，不会进入匿名产品。首次认证登录、账号切换和显式登出均调用 `GuanchaStores.clearAll()`，清除 pending image path 并移除 `guancha.auth-user-id.v1` marker。新认证账号使用无 warehouse、Journal、历史、候选和偏好 seed 的干净状态；旧 anonymous 数据不上传、不认领。

未处理：真实 CloudBase console / secure domain / publishable key 配置和 live smoke，account recovery，以及 user-scoped Warehouse / Journal / Preferences 云端 CRUD。

### Phase 9-4 — User Cloud State

后续才处理：

- preferences；
- warehouse；
- journal；
- cross-device state。

Phase 9-3 不代表真实 CloudBase runtime 已验证，也不代表账号恢复、云端 Warehouse / Journal / Preferences 或 legacy anonymous history claim/import 已完成。Phase 9-4 不在本轮实现。

## 9. 本轮边界与验证说明

- 本审计基于当前实际的 `app.js`、`frontend/`、`backend/src/guancha_api/`、`supabase/migrations/` 和测试目录扫描整理。
- Phase 9-3 只修改 public config、frontend auth/API/UI/account-boundary 以及必要测试和文档；没有改动 Provider、Selection Decision Logic、migration 或 CloudBase console。
- 本轮没有安装认证依赖，没有接入或调用真实 CloudBase，没有使用真实 API Key、真实 access token 或生产数据库。
- 目标工作树未配置本地 `backend\.venv`，因此使用已核验的共享环境 `C:\Users\QQ\Documents\New project\guancha-o1-o2-prototype\backend\.venv\Scripts\python.exe` 运行相同的 `backend\tests` 集合：`265 passed, 82 skipped`。数据库相关测试在未提供 `TEST_DATABASE_URL` 时 skip，不是测试失败。
- `node --check app.js`、`node --check frontend/auth-client.js` 和 `node --check frontend/api-client.js` 通过；`node --test frontend/tests/*.test.js` 结果为 `74 passed, 0 failed`。
- 以上验证没有连接真实 CloudBase、真实 token 或生产数据库；PostgreSQL ownership gate 是否通过，以提交时实际 `TEST_DATABASE_URL` 结果为准。已有 backend auth kernel 和 Selection ownership 不代表前端注册登录或其他云端用户数据能力已经存在。
