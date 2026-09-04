# CloudBase HTTP handoff（本地实现说明）

本地实现保留既有 `in-process + memory` 默认行为，并通过显式配置启用两种新路径：

- `GUANCHA_PRIVATE_STORAGE_BACKEND=cloudbase-http`：在已认证请求内使用当前
  `Authorization: Bearer` token 访问 CloudBase Storage。
- `GUANCHA_EXTRACTION_EXECUTION=cloudbase-handoff`：在已认证 Analyze 请求内，
  使用同一 request-scoped token 调用 `guancha-extraction-handoff`；请求体只含
  `job_id`。

相关配置名：

- `CLOUDBASE_ENV_ID`
- `CLOUDBASE_REGION`
- `GUANCHA_PRIVATE_STORAGE_HTTP_TIMEOUT_SECONDS`
- `GUANCHA_EXTRACTION_HANDOFF_FUNCTION_NAME`
- `GUANCHA_EXTRACTION_HANDOFF_TIMEOUT_SECONDS`

当前实现不改变 CloudBase Storage ACL/rules。启用真实截图前，`temporary/` 下的
临时截图必须由后续云配置 gate 设为 private（优先使用官方 custom rules，避免影响
其他既有对象）。实现不会生成 public URL；handoff 复制到 COS 后不删除 CloudBase
源对象，直到既有终态清理生命周期安全完成。
