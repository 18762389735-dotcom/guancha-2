> **FOR FUTURE CODEX TASKS:**  
> Before proposing or implementing any change, read this document first.  
> If another historical document conflicts with this document, do not silently choose the historical version.  
> Verify against current code and latest release reports.

# GUANCHA CURRENT SYSTEM SPEC

观茶当前系统事实总规范

版本：Phase 17B / 2026-08-13  
当前产品代码边界：`5bdd600`  
当前发布门报告边界：`5bdd600`  
文档状态：当前系统 SSOT（Single Source of Truth）

## 0. Document Authority

- Current product code commit：`5bdd600`
- Current documentation baseline：`5bdd600`
- Current validation branch：`codex/final-browser-provider-validation`
- Generated date：2026-08-13
- Authority：当前系统事实唯一入口；旧 PRD 用于理解历史设计，不再单独作为“当前已实现状态”的依据。

### 来源优先级与真值标签

本文只回答“观茶现在是什么、代码里有什么、验证到了哪里”。它不是愿望清单、完整 PRD、发布公告或用户研究结论。

事实来源优先级为：

1. 当前代码与测试；
2. Phase 15 Release Gate 报告；
3. Phase 14、Phase 13 的审计与验证工件；
4. 最新项目运行说明；
5. 当前可找到的核心体验文档和较晚 PRD；
6. 完整机制、后端、买后等早期 PRD；
7. 更早历史文档。

发生冲突时，高优先级来源覆盖低优先级来源，但旧文档仍可作为演进档案。本文使用以下状态标签：

| 标签 | 含义 |
|---|---|
| `IMPLEMENTED_VERIFIED` | 代码已实现，且当前有直接、可重复的验证证据 |
| `IMPLEMENTED_PARTIALLY_VERIFIED` | 已实现，但缺少数据库、浏览器、真实 Provider 或其他关键层验证 |
| `DESIGNED_NOT_CURRENTLY_COMMITTED` | 已形成设计意图，但当前代码边界不能证明已提交 |
| `FUTURE_PLANNED` | 后续方向，不属于当前产品 |
| `HISTORICAL_ONLY` | 只代表旧阶段，不得作为当前事实 |
| `UNCONFIRMED` | 当前证据不能得出结论 |

任何新文档若与本文冲突，应先更新代码/验证证据，再显式修订本文；不得用历史投入或口头说法悄悄覆盖。

## 1. 一页摘要

观茶当前是一套面向普通饮茶用户的候选茶决策辅助原型。它把商品页和商家表达中的专业茶语结构化为证据，翻译成有边界的感官含义，再结合用户本次明确需求，对 1–5 个候选给出规则驱动、可解释、可追问、可用新证据修正的行动建议。

当前主路径已经在代码中形成闭环：

`Home → 首次偏好引导/跳过 → 候选与本次需求 → 图片提取 → 证据/感官/Personal Fit → Decision V1 → 高价值追问 → 商家回复 → Aggregate Rejudge → Decision V2 + Delta → 用户选择 → 本地茶仓/泡茶记录`

代码门已经关闭已知 P0/P1 隐私和状态完整性问题；前端、真实隔离 PostgreSQL、隐私和数据库 AI Eval 均有自动验证。FakeProvider 浏览器矩阵已执行 21 项（16 PASS、5 个诚实 BLOCKED、0 FAIL），独立浏览器红队无 P0/P1；真实 Provider 未发送 demo fixture，因无已授权真实商品截图而保持 BLOCKED。

因此当前最高准确发布判断是：

> `RUNTIME_GATES_CLOSED_PROVIDER_SMOKE_BLOCKED`

这不是“已准备生产发布”，也不是“真实用户验证通过”。

## 2. Product Definition

### 2.1 One-line Positioning / 产品北极星

> 观茶不是帮助用户识别一款茶，而是把商品和商家说的专业茶语，翻译成普通用户能够理解的感官含义，并帮助其在已经挑出的候选茶之间做出可解释、可追问、可修正的选择。

### 2.2 User-facing Positioning

> 把茶商说的专业话，翻译成你喝得到的感觉。

### 2.3 Target User

- 用户已经在电商或商家处挑出若干候选，但看不懂术语、难以比较，也不知道应该继续问什么。
- 当前核心用户是对茶有兴趣、但不具备稳定专业判断能力的普通消费者；不是鉴定师、商家运营者或专业审评人员。

### 2.4 User Problem 与 Job To Be Done

- 系统帮助用户处理“选哪款、先问什么、是否值得小样试错”，而不是替代真实品饮或替用户下单。
- 当前比赛版主要聚焦铁观音候选；覆盖全部茶类是未来方向，当前不可声称。

### 2.5 Non-goals

见第 3.5 节“当前明确不做”。

## 3. Current Product Scope

### 3.1 `IMPLEMENTED_VERIFIED`

- Evidence 来源边界、Decision 规则与排序、Question 纯逻辑、MerchantReply 解析边界。
- 客户端持久化 schema v3、候选稳定身份、skip 语义、推荐/用户选择拆分。
- 26 个 analytics 事件的合同、隐私投影、fail-open sink、CSV 导出。
- 前端与无数据库后端自动测试覆盖的行为。

### 3.2 `IMPLEMENTED_PARTIALLY_VERIFIED`

- 1–5 个候选；每个候选 1–2 张图片。
- JPEG/PNG 为后端明确安全 MIME；HEIC/HEIF 仅在浏览器转换条件满足时支持。
- 单图上限 5 MB。
- Selection Need、证据提取、感官翻译、规则决策、问题生成、商家回复解析、统一复判、Delta。
- 本地茶仓与基础泡茶记录。
- 隐私安全的本地状态锚点与产品事件观测。

上述代码路径存在，但涉及真实 PostgreSQL、浏览器、真实 Provider 或完整买后 UI 的部分仍未完成运行级验证。

### 3.3 `DESIGNED_NOT_CURRENTLY_COMMITTED`

- 完整、动态、可恢复的 TeaStock/BrewSession/Infusion 买后数据模型。
- 去演示 seed 的个人茶仓和动态日历。
- 反馈如何影响下一次判断的完整可视解释。

### 3.4 `FUTURE_PLANNED`

- 账号、云同步、跨设备体验。
- 谨慎的长期偏好学习、个人趋势、更多茶类与商家洞察。

### 3.5 当前明确不做

- 不做全中国茶知识百科或普适识茶器。
- 不做真假鉴定、绝对品质评分、健康/体质推荐、全网找茶、商城、交易或社区。
- 不做复杂口味人格或伪精确匹配百分比。
- 不建设 RAG、知识图谱、自研模型训练；当前代码也不包含这些架构。
- 不宣称替用户判断真伪、质量或商家诚信。
- 不做自动购买、支付、订单、登录、权限和多租户。
- 不做生产级高并发、多实例任务平台或完整 DevOps。
- 不做第三方画像、广告追踪、A/B 平台或把 analytics 用作决策输入。
- 不做账号云同步、跨设备连续体验。
- 不承诺长期自动学习、模型在线训练或“越用越懂你”。
- 不把买后泡茶记录写成已经成熟上线的完整产品。

## 4. 当前真实用户流程

### 4.0 Current Flow Overview

`Home → Onboarding → Need（候选页内）→ Candidates → Images → Analysis → Evidence → Sensory Interpretation → Personal Fit → Decision V1 → Question → Merchant Reply → Aggregate Rejudge → Decision V2 → Delta → Selection → Tea Stock`

| Step | 当前状态 |
|---|---|
| Home / Onboarding / Skip | `IMPLEMENTED_VERIFIED`；浏览器待验 |
| Need / Candidates / Images | `IMPLEMENTED_PARTIALLY_VERIFIED` |
| Analysis / Evidence / Sensory | `IMPLEMENTED_PARTIALLY_VERIFIED`；DB/Provider 待验 |
| Personal Fit / Decision V1 | `IMPLEMENTED_VERIFIED`（规则层）；DB/browser 待验 |
| Question / Reply | `IMPLEMENTED_VERIFIED`（纯逻辑/合同）；DB/browser 待验 |
| Aggregate Rejudge / V2 / Delta | `IMPLEMENTED_PARTIALLY_VERIFIED` |
| Selection / Tea Stock | `IMPLEMENTED_PARTIALLY_VERIFIED` |

### 4.1 启动与 Onboarding

应用从 Home 开始。首次用户点击开始选茶后进入 O1、O2 偏好引导，也可以跳过。跳过会清除默认演示式偏好，因此后续不会把用户没选过的茶类或香气写成个人事实。完成或跳过后，后续正常开始可绕过 onboarding。

状态：`IMPLEMENTED_VERIFIED`（自动测试）；真实浏览器交互为 `IMPLEMENTED_PARTIALLY_VERIFIED`。

### 4.2 候选与本次需求

Selection Need 位于候选页的可编辑卡片/弹层，不是独立页面。用户可以表达本次饮用目的、感官方向和预算。若已有活跃 session/decision，Need 更新必须在服务端成功后使旧 Decision、Questions、Replies、Answer 和 Delta 失效，并返回候选页重新分析；不能用新 Need 展示旧结论。

本地持久化不保存 Need 自由文本。活跃 session reload 时由服务端 snapshot 恢复；未提交、无 session 的草稿刷新后可丢失，这是当前隐私优先取舍。

状态：前端“服务端成功后再清理旧派生状态”的 transition 与 PostgreSQL 原子失效链为 `IMPLEMENTED_VERIFIED`；真实浏览器 reload 仍为 `IMPLEMENTED_PARTIALLY_VERIFIED`。

### 4.3 候选、图片与分析

用户建立候选、上传图片，再启动分析。提取任务产生结构化字段和证据，Decision 任务基于当前 session 的 Need、候选 extraction 和 bounded preference 输入运行。queued/processing/failed/completed 有对应状态；服务端 snapshot 用当前 Need 与 extraction lineage 识别权威 decision job。

当前图片采用临时存储；浏览器 IndexedDB 还会临时保存待上传 Blob，以支持恢复，但缺少 TTL/eviction。

状态：代码路径 `IMPLEMENTED_VERIFIED`；真实 Postgres/浏览器全链 `IMPLEMENTED_PARTIALLY_VERIFIED`。

### 4.4 结果、问题、回复与复判

结果页按证据来源、感官含义、Personal Fit 和 Decision 展示。用户可查看/复制高价值问题，逐题绑定商家回复。所有当前问题的回复在会话范围统一复判；若问题生成成功但确实没有可行动问题，系统允许用户按现有判断选择，不伪造回复或空跑复判。

复判完成后显示 V2 和 Delta。用户仍可选择非系统首选；历史会分别保存系统推荐项和用户选择项。

状态：代码路径 `IMPLEMENTED_VERIFIED`；真实数据库与浏览器链 `IMPLEMENTED_PARTIALLY_VERIFIED`。

## 5. AI 与决策架构

### 5.1 两条相交管线

当前系统不是“上传图片后让一个大模型直接推荐”。它由两条相交管线组成：

1. **证据管线**：图片 → extraction job → 结构化 evidence → 感官翻译。
2. **决策管线**：Need + evidence + bounded preference → 规则档位与排序 → Question counterfactual → MerchantReply claims → rejudge → Delta。

生成式 Provider 主要服务结构化提取；Question、Decision 和默认 MerchantReply 解析包含大量确定性规则。把整个系统描述为“MiMo 大模型推荐”是不准确的。

### 5.2 Provider 状态

代码支持 unavailable、fake、OpenAI 和 MiMo 兼容 provider 配置。当前 Beta 文档以 MiMo 为目标，但实际部署 runtime provider/model 仍未确认。Phase 17B 检测到兼容凭据可用，但没有已授权真实商品截图，live multimodal 调用数为 0；历史 smoke 只能标记为 `HISTORICAL_ONLY`。

## 6. Evidence Contract

### 6.1 三个独立维度

| 维度 | 当前闭集 |
|---|---|
| InformationStatus | `explicit`、`inferred`、`unknown`、`conflict` |
| EvidenceSourceType | `product-claim`、`merchant-claim`、`user-input`、`system-inference`、`brew-feedback` |
| VerificationStatus | `unverified`、`user-confirmed`、`system-consistent`、`conflicting` |

Evidence strength 为 `low`、`medium`、`high`。

### 6.2 不可跨越的来源边界

- 商品页 explicit 事实可以进入“商品页写明/目前能确认”的区域，但仍只是页面声明。
- 商品页 inferred 不得被包装为已确认事实。
- Merchant claim 只进入商家补充区域，不能改写为“商品页明确标注”。
- 营销词不得升级为品质或真实喝感结论。
- `system-consistent` 只表示系统内部没有发现矛盾，不等于现实世界验证。
- 冲突必须保留，不能用后来的声明静默覆盖旧来源。

状态：answer contract、provider 与 repository 边界及真实 PostgreSQL 持久化路径为 `IMPLEMENTED_VERIFIED`；真实模型抽取质量 `UNCONFIRMED`。

## 7. 感官翻译

当前受控感官映射主要围绕 `aroma_style`、`roast_level` 与兼容字段 `roast_or_style`。表达采用“通常、可能、如果你偏好……”等条件语言，把术语转换为用户可理解的清爽度、熟香、焙火感等方向。

它不是实喝预测，也不把“兰花香、高山、大师、核心产区、传统工艺”等营销词当作被验证的感官事实。inferred evidence 不应单独把候选跨入硬性不推荐档位；当前相关策略仍需继续由评测守护。

状态：模板与规则 `IMPLEMENTED_VERIFIED`；全真实图片/模型覆盖 `IMPLEMENTED_PARTIALLY_VERIFIED`。

## 8. Personal Fit 与优先级

当前优先关系是：

1. 用户本次 Selection Need；
2. 有明确证据支持的本次感官方向；
3. O1/O2 形成的 bounded preference reference；
4. 最近冲泡反馈形成的低置信 preference evidence。

O1/O2 不得压过本次 Need；跳过 onboarding 时不生成偏好陈述。近期 preference evidence 有数量和时间边界（最多 12 条、90 天）并以低置信方式参与同档排序。当前没有“系统长期学会你的口味”的真实证据。

## 9. Decision Contract

### 9.1 行动档位

当前规则生成五个行动档位：

- `currently-selectable`
- `sample-first`
- `ask-before-buying`
- `insufficient-information`
- `not-recommended-now`

行动档位先由规则决定；它表达当前证据下最稳妥的下一步，而不是茶叶绝对优劣。

### 9.2 同档排序

同档排序依次考虑：

1. `explicit_sensory_need_match`
2. `need_match`
3. `budget_fit`
4. `trial_friendliness`
5. `personal_low_confidence`
6. `evidence_sufficiency`
7. 稳定 UUID 次序

前端解释必须承认实际参与排序的感官信号。预算常见区间按上限解析。该组件序列不应宣传为可比较用户或茶款品质的“AI 总分”。

### 9.3 版本语义

CandidateDecision 内容随版本保存，V1/V2 可追溯。DecisionVersion 生命周期字段可能更新，因此准确表述是“决策内容和输入 lineage 可审计”，不是“所有数据库行完全不可变”。Need 或 extraction 改变会使旧 current decision 失效。

## 10. Question Engine

问题生成先构造闭集答案分支，再用无副作用 counterfactual decision simulation 判断答案是否可能改变首选、风险或行动档位。当前最多保留 3 个问题，最低 value threshold 为 3。

这是一套规则化的 Next Best Question 机制，不是训练中的主动学习系统。默认 fake provider 只返回按规则选出的前三项。已知字段不应重复问，低价值问题可以合法返回空列表；“空问题”与“生成失败”是两个不同状态。

状态：纯逻辑与 PostgreSQL 问题闭环为 `IMPLEMENTED_VERIFIED`。

## 11. Merchant Reply Contract

- 一个输入只保存到当前明确 follow-up question ID。
- 主状态为 `answered`、`partially-answered`、`evasive`、`not-answered`、`conflicting`。
- 小样回答采用否定优先的闭集语义，避免“不提供/没有”被子串误判为肯定。
- 只有明确、非 unknown 的 product explicit 值与明确商家值相反时，才形成 conflict。
- 原始商家回复保存在业务数据库机制中，但不进入 localStorage、product event 或 CSV。
- 当前默认 parser 为确定性规则 provider；真实自然语言覆盖有限。

状态：解析与隐私边界 `IMPLEMENTED_VERIFIED`；真实语言分布 `UNCONFIRMED`。

## 12. Aggregate Rejudge 与 Decision Delta

Rejudge 是 session-scoped aggregate 操作：收集当前 Decision 的问题和对应回复，解析 Merchant Claims，合并到来源明确的 evidence，再以与 V1 相同的 evaluator 和 bounded preference 输入重跑，生成 V2。

Delta 用于回答：首选是否变化、行动档位是否变化、风险新增/解除、事实新增、冲突新增，以及为什么发生变化。`changed`、`unchanged`、`risk changed`、`still unknown/insufficient` 都是合法结果。Delta 不证明模型训练或线上学习。

状态：纯 service/stub 路径与真实 PostgreSQL V2/Delta 为 `IMPLEMENTED_VERIFIED`。

## 13. 状态权威、恢复与客户端持久化

### 13.1 权威层

- 活跃业务事实：PostgreSQL session/snapshot/current decision/answer/delta。
- 本地界面状态：闭集 `uiSession`。
- 活跃流程锚点：selection bridge schema v3。
- 临时待上传图片：IndexedDB。
- 买后本地数据：字段级 allowlist 的 post-purchase store。

### 13.2 selection bridge v3

只保存 session、candidate/image/reply 的安全 anchor、稳定 UUID、闭集状态和必要时间戳。明确不保存：

- Need 自由文本；
- 商品/候选自由名称；
- MerchantReply 原文或摘要；
- extraction/evidence/questions/selection answer/delta 完整树；
- 图片 preview/data URI、File/Blob；
- 任意未知嵌套对象。

旧 selection/legacy store 会在读取时按 v3 投影重写并清理旧 key；损坏 JSON 会删除。隐私优先迁移可能丢弃旧自由文本 history，这是已接受的 P2 数据兼容代价。

### 13.3 恢复行为

- 正常 navigate/reopen：Home。
- active reload：请求服务端 snapshot，根据 authoritative job/decision/delta 恢复 candidates、analysis、result 或 rejudge。
- candidate reorder 前后用稳定 candidate ID 恢复当前卡片。
- ask overlay 和未提交 merchant draft 不持久化；用户可回到结果重新打开。

状态：代码与单元/行为测试 `IMPLEMENTED_VERIFIED`；真实浏览器 `IMPLEMENTED_PARTIALLY_VERIFIED`。

## 14. Product Analytics

### 14.1 事件词表

Client interaction 共 13 个：

`app_open`、`start_selection`、`onboarding_started`、`onboarding_completed`、`onboarding_skipped`、`need_started`、`candidate_result_viewed`、`merchant_question_viewed`、`merchant_question_copied`、`merchant_reply_started`、`candidate_selected`、`tea_stock_added`、`flow_abandoned`。

Server authoritative 共 13 个：

`need_submitted`、`candidate_created`、`candidate_deleted`、`candidate_image_added`、`candidate_image_removed`、`analysis_started`、`analysis_completed`、`analysis_failed`、`merchant_reply_submitted`、`merchant_reply_unusable`、`rejudge_started`、`rejudge_completed`、`rejudge_failed`。

### 14.2 权威和隐私

- client endpoint 不能伪造 server outcome。
- 事件采用 strict allowlist，不接受 Need、图片、候选名、商家原文、错误 message/stack、凭据或 PII。
- 事件默认输出 JSONL/stdout，可选显式文件；不写 PostgreSQL 业务表。
- sink fail-open，观测失败不能影响业务响应、Decision 或 job 状态。
- anonymous analytics session 是 per-tab sessionStorage UUID，不是账号或跨设备身份。
- export 首见 event ID 去重，并防 CSV formula injection。
- analytics 不参与评分、排序或复判。

状态：代码/脚本测试 `IMPLEMENTED_VERIFIED`；真实用户数据为 0，retention/rotation `UNCONFIRMED`。

## 14.3 Replay / Idempotency

当前原则是 `restore/read/replay ≠ business transition`：

- session/candidate/image create 将真实 `created` edge 传递到 route，只有新建才发 server-authoritative transition event；
- staged extraction 只返回 runner 新接受的 job，pending/active job ID 在进程内去重；完成、失败和 shutdown 会释放身份；
- GET job/snapshot/current decision/delta 不产生业务 transition；
- candidate reorder 与恢复使用稳定 candidate identity；
- server event ID 使用稳定资源身份构造，可在 export 时去重。

上述 created-edge 与进程内 duplicate enqueue 防护有代码级测试；隔离 PostgreSQL 同 key 并发、事务与资源幂等性已验证。产品事件 JSONL 的进程重启后物理重复仍依赖 export 的 event-id 首见去重语义。

## 15. AI Eval

当前机器可读评测矩阵共 30 个案例：

- 30 PASS；
- 0 FAIL；
- 0 BLOCKED（Phase 17A 在隔离 `guancha_test` 运行）。

评测覆盖 Decision、Need、营销边界、证据来源、回复词汇、状态/replay 以及部分 Question/Rejudge。Extraction 的固定 fixture 合同不等于真实图片/provider 端到端；runner 会把 DB 缺失写为 BLOCKED，不把 skip 当 PASS。当前结果也不代表线上准确率或商业效果。

状态：离线/数据库层 `IMPLEMENTED_VERIFIED`；live provider `UNCONFIRMED`。

## 16. 工程形态

- Frontend：静态 HTML/CSS/JavaScript，模块化 client/store/analytics 辅助代码。
- Backend：FastAPI + Pydantic contract + application services + PostgreSQL repository。
- Jobs：analysis jobs + in-process/manual runner；进程内 job ID 去重。
- Images：临时对象存储抽象，不写数据库；当前实现不提供生产级持久保证。
- Database：PostgreSQL schema/migrations；托管商未知。
- Container：Python 3.13 Dockerfile，启动时迁移后运行 uvicorn；本地默认 8000，容器缺省 8080/尊重 `PORT`。
- Provider：可配置 fake/OpenAI/MiMo/unavailable；当前 runtime 未确认。

状态：结构 `IMPLEMENTED_VERIFIED`；生产部署形态 `UNCONFIRMED`。

### 16.1 Architecture Layers

| Layer | Current responsibility | Must not do | Status |
|---|---|---|---|
| Frontend | 收集 Need/候选、展示证据/判断、发起业务动作 | 不持久化敏感树；不伪造 server outcome | `IMPLEMENTED_VERIFIED` |
| FastAPI application | 合同校验、会话/任务/回复/复判编排 | 不让 analytics 失败改变业务结果 | `IMPLEMENTED_VERIFIED` |
| Provider adapter | 结构化理解与抽取；可配置实现 | 不直接决定最终排序或写数据库 | `IMPLEMENTED_PARTIALLY_VERIFIED` |
| Extraction | 生成结构化字段和来源信息 | 不把营销词自动升级为事实 | `IMPLEMENTED_PARTIALLY_VERIFIED` |
| Evidence | 保留来源、状态、验证与冲突 | 不混淆 product/merchant/inference | `IMPLEMENTED_VERIFIED` |
| Sensory Interpretation | 受控地把术语转成感官方向 | 不声称真实实喝或必然结果 | `IMPLEMENTED_VERIFIED` |
| Personal Fit | Need-first 的有边界适配 | 不让长期偏好压过本次 Need | `IMPLEMENTED_VERIFIED` |
| Decision | 规则档位和同档排序 | 不输出伪 AI 总分 | `IMPLEMENTED_VERIFIED` |
| Question | 选择可能改变决策的未知信息 | 不把字段完整度冒充问题价值 | `IMPLEMENTED_VERIFIED` |
| MerchantReply | 逐题解析商家自然语言并保留来源 | 不验证商家声明为客观事实 | `IMPLEMENTED_VERIFIED` |
| Rejudge | 聚合当前回复后重跑同一 evaluator | 不做一答一推荐 | `IMPLEMENTED_VERIFIED` |
| Version / Delta | 保存 V1/V2 内容和变化解释 | 不把所有生命周期字段称为 immutable | `IMPLEMENTED_VERIFIED` |
| Persistence / PostgreSQL | 权威业务状态、lineage、恢复 | 不存图片；不依赖 localStorage 作为事实源 | `IMPLEMENTED_VERIFIED` |
| Analytics | 匿名、闭集、fail-open 产品事件 | 不影响 Decision，不冒充真实使用数据 | `IMPLEMENTED_VERIFIED` |

### 16.2 AI / Rule Boundary

- AI/provider：理解图片和自然语言、输出受合同约束的结构化候选信息。
- Rules/data：证据边界、硬约束、行动档位、排序权威、provenance、版本和可复现性。
- Provider 不得自行确认真假、创造领域事实、绕过 Decision 输出最终排序或直接写数据库。

## 17. 当前验证矩阵

| 验证层 | 结果 | 事实状态 |
|---|---:|---|
| Frontend tests | 62/62 PASS | `IMPLEMENTED_VERIFIED` |
| Backend tests | 304 PASS / 0 SKIP（isolated PostgreSQL） | `IMPLEMENTED_VERIFIED` |
| AI Eval | 30 PASS / 0 FAIL / 0 BLOCKED（isolated PostgreSQL） | `IMPLEMENTED_VERIFIED` |
| Privacy focused | 26/26，P0/P1=0 | `IMPLEMENTED_VERIFIED` |
| Node syntax / Python AST | PASS | `IMPLEMENTED_VERIFIED` |
| Diff/secret checks | PASS | `IMPLEMENTED_VERIFIED` |
| PostgreSQL full state matrix | 304 backend PASS；DB red team PASS_WITH_BOUNDARIES | `IMPLEMENTED_VERIFIED` |
| Browser full E2E | 21-case FakeProvider matrix：16 PASS / 0 FAIL / 5 BLOCKED；red team PASS_WITH_P2 | `IMPLEMENTED_PARTIALLY_VERIFIED` |
| Live provider current commit | 0 calls；无授权真实截图，smoke BLOCKED | `UNCONFIRMED` |
| Real user validation | 0 participants | `UNCONFIRMED` |

## 18. 发布阻塞项

### 18.1 Phase 15 正式 Release Gate Blockers

1. 已配置隔离 `guancha_test` 并完成数据库测试、AI Eval 和 database red team；保持其 operator safety gate。
2. 完成 browser-accessible localhost 的其余场景：首次/跳过 onboarding、两候选重排、Need 修改失效、active reload、模糊/冲突回复与 ranking-changed 分支；已完成一条商家回复→复判→选择→茶仓闭环和三种 viewport。

数据库 blocker 应覆盖 Session→Candidate→Image→Analysis→Decision V1→Question→Reply→Rejudge→V2→Delta 及 same-key replay/exactly-once。

### 18.2 真实发布前还需确认的运行事实

- 明确实际 deployed commit、platform、runtime port、provider/model 和 database host。
- 若真实发布依赖模型质量，对当前 commit 做受控 live provider smoke；不得用旧 smoke 或 fixture 代替。

## 19. 当前 P2 与可接受债务

- IndexedDB 待上传图片无 TTL/eviction。
- 临时图片存储和 in-process runner 不耐受进程重启或多实例。
- Analytics retention/rotation 未配置。
- Merchant draft/ask overlay 不跨 reload。
- 旧自由文本 history 在隐私迁移时可能丢失。
- 茶仓/日记含演示 seed，容易被误读为真实个人数据。
- 茶仓日历日期与部分体验仍硬编码。

旧浏览器审计曾记录移动端卡片/固定 CTA 轻微重叠、触控尺寸、装饰层、favicon/public config 404 和大 PNG；当前 commit 未复验，因此它们不是“当前已确认 P2”，而是第 27 节中的待复验历史观察。

## 20. 买后链路分层

### 20.1 当前已实现

- 本地茶仓、手动添加、拥有状态和基本详情。
- 选择后入仓。
- 泡茶计划、计时、多泡、基础/进阶反馈、记录详情和删除。
- Brew feedback / recent preference evidence 的后端与前端桥接。

其中存储投影和部分逻辑可标 `IMPLEMENTED_VERIFIED`；完整 UI 为 `IMPLEMENTED_PARTIALLY_VERIFIED`。

### 20.2 已设计但当前未完整提交/验证

- 动态日历和去演示 seed。
- 稳定 TeaStock/BrewSession/Infusion 数据模型。
- 每泡建议值与实际值的完整记录。
- 活跃冲泡可靠恢复、编辑和异常流程。
- 反馈如何影响未来判断的可见解释。

状态：`DESIGNED_NOT_CURRENTLY_COMMITTED`。

### 20.3 未来规划

- 账号与云同步、跨设备。
- 更长周期但仍可撤销的偏好学习。
- 个人趋势、更多茶类、商家侧洞察。

状态：`FUTURE_PLANNED`。

## 21. 商业与用户验证事实

当前商业价值链只是待验证假设：

`术语理解成本下降 → 候选差异更清楚 → 追问更有价值 → 用户更敢做选择/小样试错 → 买后反馈可帮助下一次判断`

当前没有：

- 真实参与者；
- 任务成功率、转化率、留存；
- 决策准确率或真实口味符合率；
- 付费意愿、收入或规模；
- 商业合作或商家端有效性。

用户验证人数严格为 0。任何百分比、显著性或“用户更信任”表述都属于未来研究结果，不是当前事实。

## 22. 部署与 Git 事实

| Git/Deployment item | 当前可证明事实 |
|---|---|
| Competition baseline/freeze | `competition/main` 与本地 competition-freeze 基线 `05b0292` |
| Historical `origin/main` | `f1cc4e8`；不是当前产品代码边界 |
| Observable Beta docs boundary | `1d9d606` |
| Phase 15 product code | `cabc959` |
| Release-gate branch/report | `codex/release-gate-closure` / `84f1435` |
| Phase 16 SSOT branch | `codex/system-spec-consolidation` |
| Phase 17B validation branch/report | `codex/final-browser-provider-validation` / `5bdd600` |
| Actually deployed commit | `UNCONFIRMED` |
| Platform / runtime port / provider-model / DB host | `UNCONFIRMED` |

本文建立 SSOT 不等于 merge、deploy 或 release。仓库能返回 HTTP 200 也只证明服务进程可响应，不等于完整 Browser E2E 已通过。

## 23. 当前文档地图

| 文档 | 当前角色 |
|---|---|
| `docs/GUANCHA_CURRENT_SYSTEM_SPEC.md` | 当前系统事实唯一入口 |
| `artifacts/system-spec/PRODUCT_TRUTH_AUDIT.md` | Phase 16 产品事实核对 |
| `artifacts/system-spec/ENGINEERING_TRUTH_AUDIT.md` | Phase 16 工程事实核对 |
| `artifacts/system-spec/DOCUMENT_CONTRADICTIONS.md` | 历史矛盾与裁决 |
| `artifacts/system-spec/FINAL_FACT_CHECK.md` | SSOT 独立终审 |
| `artifacts/system-spec/CONSOLIDATION_REPORT.md` | 本轮整合与交付记录 |
| `artifacts/release-gate/RELEASE_GATE_REPORT.md` | Phase 15 代码门与验证边界 |
| `artifacts/release-gate/PRIVACY_RED_TEAM.md` | Phase 15 隐私终审 |
| `docs/PRODUCT_ANALYTICS_SPEC.md` | 当前事件合同 |
| `docs/CLIENT_PERSISTENCE_CONTRACT.md` | 当前客户端持久化合同 |
| `docs/AI_EVAL_MATRIX.md` | 当前评测案例说明 |
| `docs/AI_FAILURE_TAXONOMY.md` | 当前失败分类闭集 |
| 旧 PRD / Phase 13–14 工件 | 设计意图与历史证据，只作对照 |

任务曾引用 `GUANCHA_CORE_EXPERIENCE_V3.md`，但本次定向查找未找到，状态为 `UNCONFIRMED`。

## 24. 历史演进摘要

1. 早期以截图/OCR、单候选、单图和简单推荐为中心。
2. 扩展为多候选、多图、结构化 evidence 和可解释 Decision。
3. 引入感官翻译与 Selection Need 优先，降低长期偏好和营销词的越权。
4. 引入高价值 Question、MerchantReply、aggregate rejudge 和 Decision Delta。
5. Phase 13 加强闭环一致性和离线评测。
6. Phase 14 增加隐私安全 analytics、用户验证工具包并暴露发布边界。
7. Phase 15 关闭客户端持久化、replay、candidate identity、skip/history 语义等代码门；数据库与浏览器仍待验。
8. Phase 16 只整合当前事实，不改变产品代码。
9. Phase 17A 在隔离 PostgreSQL 中关闭此前 DB 验证 blocker。
10. Phase 17B 完成 FakeProvider 浏览器矩阵与红队；修复 Need 编辑按钮被装饰层遮挡的 P1；真实 Provider 因缺少授权真实输入而未调用。

## 25. Appendix A — Resume-safe Current Facts

后续任务可以直接依赖以下事实，无需重读所有历史文档：

- 北极星是专业茶语 → 感官含义 → 候选决策，不是识茶/OCR。
- 当前产品代码与验证报告边界 `5bdd600`。
- 当前发布判断是 `RUNTIME_GATES_CLOSED_PROVIDER_SMOKE_BLOCKED`。
- 当前范围 1–5 候选、每候选 1–2 图。
- Need 优先于偏好；skip 不产生伪偏好。
- Evidence 来源和验证状态必须分开；商家声明未核验。
- Decision 为五档规则行动建议，同档排序不是黑箱 AI 总分。
- Question 最多 3 条，使用反事实决策价值；空问题是合法终态。
- 商家回复逐题绑定，统一复判，V2 + Delta。
- selection bridge v3 不持久化自由文本或 evidence/answer/delta 树。
- Analytics 为 13 client + 13 server，fail-open，不影响决策。
- AI Eval 30=30 PASS/0 FAIL/0 BLOCKED（隔离 PostgreSQL）。
- 前端 62/62；后端 304 PASS/0 SKIP、AI Eval 30/30 PASS（隔离 PostgreSQL）；用户验证 0。
- 实际部署平台、commit、provider/model、数据库 host 均未知。
- 真实 Provider 质量、部署配置确认和用户研究是下一发布门；浏览器仍有 5 个明确 BLOCKED 的可控性验证债务。

这些事实可作为作品集/简历材料的证据底稿，但不代表已经替用户写好简历，也不得删除其中的验证边界。

## 26. Appendix B — Claims to Avoid

- “当前已经 production-ready / deployment-ready。”
- “Phase 15 已完成数据库和浏览器验收。”
- “当前线上使用 MiMo/OpenAI/某个具体模型。”
- “当前部署在 Render/Vercel/Supabase。”
- “系统能准确识别所有茶、验证商家说法或保证适口性。”
- “AI 会从每次使用自动学习。”
- “用户偏好比本次需求更重要。”
- “所有推测字段都等同明确事实。”
- “茶仓和泡茶日记已完整上线、跨设备可用。”
- “已有真实用户、转化率、准确率或商业验证。”
- “Phase 16 改了产品代码。”

还应避免“自研大模型”“训练推荐模型”“98% AI 准确率”“提升转化 XX%”“大规模用户验证”“完整长期自适应学习”“全茶类支持”“RAG 推荐系统”等无证据说法。

## 27. Unresolved Current-State Questions

| What is unclear | Why it cannot be proven | Evidence that would resolve it |
|---|---|---|
| deployed commit、平台、端口、provider/model、DB host | repo 配置只能说明兼容形态，没有平台侧事实 | 部署控制台/只读运行配置与 commit digest |
| PostgreSQL 全链、并发 replay、exactly-once | 已在 `guancha_test` 执行；JSONL 物理重复跨进程仍靠 export 去重 | 进程重启/多进程 telemetry soak |
| 浏览器/移动端主链、reload、console/network、性能 | 21-case matrix 无 FAIL，但 5 个场景受 fixture/history/fault injection 限制而 BLOCKED | 可控 browser fixture、history 和 telemetry failure injection |
| live extraction 质量与营销边界 | 没有已授权真实截图；Provider 调用为 0 | 当前 commit 的受控 live provider smoke/eval |
| 买后完整 UI 与 seed 策略 | 只有代码/局部测试，无完整浏览器链和产品决定 | 买后浏览器验收与明确 seed 决策 |
| IndexedDB TTL/eviction | 当前实现没有机制 | 独立设计批准、实现和时间推进测试 |
| Analytics retention/rotation | 只实现 sink/导出，没有部署运维配置 | 当前环境 retention/rotation 配置与演练 |
| `GUANCHA_CORE_EXPERIENCE_V3.md` | 定向查找未找到文件 | 找到权威文件并与代码逐项对照 |
| 历史 UI P2 是否仍存在 | 当前 commit 未做浏览器复验 | 当前 commit 多 viewport 浏览器验收 |
| 用户价值/可用性/商业假设 | participants=0 | 5–10 人形成性研究及原始分子/分母记录 |

---

维护规则：只有在代码、验证或经明确确认的产品决策发生变化时才更新本文；每次修改必须同时更新状态标签、验证矩阵、未决事实和历史演进，避免把未来规划悄悄写成当前能力。
