# Phase 17A 数据库验证失败记录

## DB-TEST-01 — startup recovery 的 ManualTaskRunner 生命周期断言

- **Test**：`test_startup_recovery_makes_interrupted_enqueue_failure_retryable`
- **Observed**：真实 PostgreSQL 下恢复请求成功，但退出 `lifespan_context` 后 `pending_count=0`，旧断言期待 `1`。
- **Expected**：在 application 生命周期仍处于启动状态时，恢复任务被 enqueue；application shutdown 后 ManualTaskRunner 应释放 pending job identity。
- **Root Cause**：`c704326` 已修复 ManualTaskRunner shutdown，清空 `tasks/_job_ids`；旧测试在生命周期退出后再断言 pending，和当前正确资源释放合同冲突。
- **Severity**：P1 test bug；不改变产品路径。
- **Classification**：Test Bug。
- **FIX ID**：`FIX-DB-TEST-01`
- **Minimal Change**：把 pending assertion 移入 lifespan context，同时保留退出后 pending=0 的断言。
- **Files**：`backend/tests/test_phase2_image_job_api.py`
- **Risk**：只改变测试时点；不会改变 runner、路由或数据库。
- **Rollback**：回退该测试提交即可恢复旧断言，但会重新引入错误失败。

## DB-TEST-02 — MerchantReply conflict 测试使用旧即时解析流程

- **Test**：`test_persisted_merchant_conflict_requires_an_explicit_known_opposite_product_claim`（5 个参数组合）
- **Observed**：保存 MerchantReply 后 `ManualTaskRunner.drain()` 为 0；部分组合在插入 evidence 后再生成问题，导致不再存在 roast question。
- **Expected**：先根据 V1 生成问题；保存所有当前问题的回复；由 Aggregate Rejudge 任务解析 queued replies 并持久化 merchant claims，随后断言 explicit/unknown/inferred/empty/opposite product evidence 的 conflict 边界。
- **Root Cause**：测试由 `cc3f6d6` 新增，但没有可用 TEST_DATABASE_URL 时未实际执行；它沿用旧“一条回复立即后台解析”的假设，而当前 `MerchantReplyService` 明确在 aggregate rejudge 内解析全部 saved replies。
- **Severity**：P1 test bug；当前产品合同没有回归。
- **Classification**：Test Bug。
- **FIX ID**：`FIX-DB-TEST-02`
- **Minimal Change**：生成问题后再注入 product evidence；逐题保存回复、触发一次 session-level rejudge，再对目标 reply 的 merchant claim 断言。
- **Files**：`backend/tests/test_phase6_merchant_rejudgement.py`
- **Risk**：覆盖范围更接近真实用户闭环；不修改 parser、repository、Decision 或 schema。
- **Rollback**：回退该测试提交会恢复不能在真实数据库执行的旧假设。
