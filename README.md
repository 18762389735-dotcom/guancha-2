# 观茶 Guancha

观茶是一个 AI 辅助的选茶决策与冲泡记录产品：把个人口味、商品证据和真实饮用反馈连接起来，帮助用户更有依据地买茶、泡茶和回看结果。

## What it does

观茶形成一条完整的产品闭环：

```text
口味偏好 → 购买需求 → 候选商品截图 → 结构化提取 / 证据
→ 多候选决策 → 未知项 / 商家提问 → 商家回复
→ 复判 / 差异 → 茶仓 → 泡茶日记 → 偏好证据
```

## Product highlights

- 基于证据的 AI 选茶决策，而不是只给出一个无解释的推荐。
- 明确展示不确定性与未知项，允许用户向商家补充求证。
- 商家回复可触发复判，并保留前后判断差异。
- 偏好、偏好证据、选茶记录、茶仓和泡茶日记按账号归属并可跨设备恢复。
- 泡茶反馈可以回流为后续偏好的低置信度证据。

## Architecture

```text
Browser / Vanilla JS
          |
          v
       FastAPI
       |      |
       |      +--> CloudBase HTTP Auth
       |
       +--> PostgreSQL user-owned business data
       |
       +--> configured AI provider
```

- CloudBase：身份提供方。
- FastAPI：认证、授权与应用边界。
- PostgreSQL：持久化的用户业务数据。
- 浏览器 localStorage / IndexedDB：缓存、恢复和 UI 状态，不是账号授权依据。

## Authentication

当前浏览器使用同源 Auth BFF：

```text
Browser → Guancha FastAPI → CloudBase HTTP Auth
```

支持邮箱验证码注册、邮箱密码登录、HttpOnly refresh cookie、页面刷新后的会话恢复和显式登出。access token 只在浏览器内存中使用，不写入浏览器持久化存储。FastAPI 通过 `app_users` 和 `CurrentUser` 解析账号归属；前端不能通过提交 `user_id` 取得授权。

## User data currently persisted

认证账号的服务端数据是权威来源，包括：

- 偏好与偏好证据；
- Selection sessions 及其 snapshot、候选、提取、决策、追问和商家回复链路；
- 茶仓；
- 泡茶日记。

浏览器数据仍可作为同浏览器缓存、恢复和导航状态使用，但认证后的服务端数据会覆盖这些业务缓存。旧匿名数据不会自动认领。

## Tech stack

- Vanilla JS / HTML / CSS
- FastAPI / Python
- PostgreSQL
- Tencent CloudBase Authentication
- 外部 AI provider adapter（Fake / OpenAI / MiMo）
- GitHub Actions

## Local development

创建后端环境并安装声明的开发依赖：

```powershell
py -3.14 -m venv backend/.venv
backend/.venv/Scripts/python -m pip install -e "./backend[dev]"
```

本地运行需要一个可安全使用的 PostgreSQL，并显式选择 Fake provider：

```powershell
$env:GUANCHA_DATABASE_URL="<local-postgresql-url>"
$env:GUANCHA_PROVIDER="fake"
backend/.venv/Scripts/python -m uvicorn guancha_api.main:app --app-dir backend/src --host 127.0.0.1 --port 8000
```

浏览器访问 `http://127.0.0.1:8000/`。测试数据库必须是独立、可重建的非生产数据库：

```powershell
$env:TEST_DATABASE_URL="<isolated-test-postgresql-url>"
backend/.venv/Scripts/python -m pytest backend/tests -q
node --check app.js
node --check frontend/auth-client.js
node --check frontend/api-client.js
node --test frontend/tests/*.test.js
```

代码实际读取的配置变量包括：`GUANCHA_DATABASE_URL`、`TEST_DATABASE_URL`、`GUANCHA_PROVIDER`、`PORT`、`GUANCHA_AUTH_REQUIRED`、`CLOUDBASE_ENV_ID`、`CLOUDBASE_REGION`、`CLOUDBASE_PUBLISHABLE_KEY`、`GUANCHA_AUTH_COOKIE_SECURE`、`GUANCHA_OPENAI_MODEL`、`OPENAI_API_KEY`、`GUANCHA_MIMO_MODEL`、`MIMO_API_KEY`、`MIMO_BASE_URL`、`ADMIN_API_TOKEN` 和 `GUANCHA_PRODUCT_EVENT_LOG_PATH`。密钥类变量只应通过本地环境或部署平台的 secret store 提供，不应写入 Git、前端或日志。

## Known limitations

- 原始候选截图目前是短生命周期的私有临时对象；跨重启、跨设备的持久图片展示尚未完成，属于 P9-4C。
- 忘记密码 / 账号恢复尚未实现。
- CAPTCHA 仅有可识别的错误边界，尚未实现交互式 CAPTCHA UI；只有真实 CloudBase 流程触发时才需要单独处理。
- 旧匿名浏览器数据不会自动认领或导入到新账号。
- 泡茶日记云端删除尚未纳入当前产品流程。
- 自动化测试使用 Fake provider，不会调用真实 AI provider。
