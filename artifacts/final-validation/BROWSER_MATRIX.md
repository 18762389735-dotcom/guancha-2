# Phase 17B Browser Matrix

环境：2026-08-13；本地 FakeProvider；`http://127.0.0.1:8100`；隔离 PostgreSQL `guancha_test`。浏览器控制台 error/warn 为 0。除注明的受控性边界外，状态来自实际 UI 与服务端 lineage；未读取或修改浏览器本地存储。

| Case | Viewport | 预置与步骤 | 观察结果 / 状态 | 结论 |
|---|---:|---|---|---|
| 01 正常 onboarding | 390×844 | Home → O1 → O2 → 完成设置 | 进入候选页；偏好完成状态被保存 | PASS |
| 02 跳过 onboarding | 390×844 | 独立新 origin Home → O1 → 跳过 → 分析 | 结果页不出现绿茶、花香等伪偏好；本次 Need 仍优先 | PASS |
| 03 returning user | 390×844 | Home 返回后再点开始选茶 | 直接回到候选流程，不重复进入 O1/O2 | PASS |
| 04 双候选身份/排序 | 390×844 | 上传 A/B 后分析，打开首卡追问 | 服务端 order=1 的 B 作为首卡；回复 question/candidate 关联一致 | PASS |
| 05 清晰商家回复 | 390×844 | 对价格问题提交 `280` | 回复保存并进入后续问题/统一复判路径 | PASS |
| 06 模糊商家回复 | 390×844 | 提交 `不太清楚` | 未被提升为确认事实；仍显示待确认项 | PASS |
| 07 无关商家回复 | 390×844 | 对价格问题提交 `包装很好看，今天发货。`，双击提交 | DB reply 仅 +1；未生成价格/焙火/香型 merchant claim | PASS |
| 08 冲突回复 | 390×844 | 需要可控的 product evidence 与相反 merchant input | 当前 FakeProvider 固定输出，浏览器层无注入入口；已有 DB 集成覆盖 | BLOCKED（fixture 不可控） |
| 09 排名改变 | 390×844 | 需要可控的可改变 top 的回复分支 | 当前 FakeProvider 固定输出，浏览器层无注入入口；已有 DB 集成覆盖 | BLOCKED（fixture 不可控） |
| 10 排名不变 | 390×844 | 提交不改变比较的回复后统一复判 | Delta 明确“没有改变当前首选”，V2 可继续选择 | PASS |
| 11 无高价值问题终态 | 390×844 | 需要生成 completed + 空问题列表 | 无状态清除/fixture 注入接口，不能真实构造；已有后端回归覆盖 | BLOCKED（fixture 不可控） |
| 12 刷新结果页 | 390×844 | result 页面 F5 | 同一 Need 与首卡恢复；DB 未新增 Decision | PASS |
| 13 回复中刷新 | 390×844 | 保存一条回复、关闭 sheet、F5 | server-authoritative 回复/问题状态恢复；不重新生成 Decision | PASS（sheet draft 不持久化为既知 P2） |
| 14 复判后刷新 | 390×844 | rejudge 完成后 F5 | Delta、Decision V2 和可选择状态恢复 | PASS |
| 15 cold reopen | 390×844 | 新 tab 打开应用 | 起点是 Home，不自动展示陈旧结果；开始后恢复 active server flow | PASS |
| 16 back / forward | — | 需要独立、可控的浏览历史路径 | 单页 UI 没有可审计路由 history，未把推断当作浏览器证据 | BLOCKED（无可控路径） |
| 17 telemetry fail-open | — | 需要拦截 `/api/v1/events` 或故障注入 | 该浏览器未暴露网络路由控制；不更改服务器来制造失败 | BLOCKED（故障注入不可用） |
| 18 重复 UI 操作 | 390×844 | 双击复制、双击回复提交、并发开始分析 | 复制未写 Reply/Decision；无关回复只保存 1 条；无重复可见结果 | PASS |
| 19 移动端 | 390×844 | result + 追问层 | `scrollWidth=clientWidth=390`；主 CTA 在视口内并可点击 | PASS |
| 20 大屏手机 | 430×932 | Home / candidate / result 基础 smoke | 无横向溢出或控制台错误 | PASS |
| 21 桌面 | 1280×900 | Home / candidate / result 基础 smoke | 426px 原型画布稳定居中；无横向溢出或控制台错误 | PASS |

## 本轮 P1 修复

浏览器复核发现 `.need-card .leaf-float` 覆盖了“可编辑”按钮的命中区域。已在 `styles.css` 设为 `pointer-events:none`，并新增前端回归测试，防止装饰层再次拦截需求编辑。

## 浏览器结论

`PASS_WITH_P2`：已执行的主链无 P0/P1 失败。5 个 BLOCKED 项是测试输入或浏览器故障注入能力不足，并非通过推断补齐；它们仍是下一轮浏览器验收债务。
