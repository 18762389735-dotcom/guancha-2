# 观茶发布清单

## PRE-DEPLOY

- [ ] 确认发布分支与目标 SHA 正确。
- [ ] GitHub Actions 的 baseline、Backend contract、PostgreSQL integration 全部通过。
- [ ] 本阶段没有新增 migration；确认目标环境已包含此前已批准并完成的数据库迁移。
- [ ] 通过部署平台配置所需环境变量名称，值只存在于安全配置中。
- [ ] 检查 Git 中没有密钥、token、密码或数据库凭据。
- [ ] 明确配置了实际使用的 AI provider，并确认 provider 的服务端凭据有效。

## POST-DEPLOY SMOKE

1. 检查 `/health` 和 `/api/v1/config/public`。
2. 使用已有账号登录。
3. 按 F5，确认会话恢复。
4. 使用新的测试邮箱完成注册。
5. 用账号 A / B 验证数据隔离。
6. 验证偏好恢复。
7. 完成一次 Selection 流程。
8. 验证茶仓读取与写入。
9. 验证泡茶日记读取与写入。
10. 登出后刷新页面，确认仍保持登出且不能继续访问受保护业务。

## KNOWN NON-BLOCKERS

- 持久图片展示 / P9-4C 尚未完成。
- 忘记密码 / 账号恢复尚未完成。
- CAPTCHA 仅在真实流程触发时处理。

## SECURITY BEFORE PUBLIC SHARING

如果开发凭据曾在预期 secret store 之外暴露，在公开演示或分享项目之前由负责人轮换这些凭据。不要把凭据值写入文档、Git、截图或日志。
