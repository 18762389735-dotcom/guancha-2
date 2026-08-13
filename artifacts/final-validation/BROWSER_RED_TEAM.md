# BROWSER RED TEAM

环境：FakeProvider + local `http://127.0.0.1:8100` + isolated `guancha_test`；console error/warn=0；浏览器网络拦截接口当前未暴露，以下以实际 UI 状态 + PostgreSQL lineage 取证。

| CASE | 攻击/复现 | 证据 | Verdict |
|---|---|---|---|
| RT-01 | stale/old Decision：从结果页 F5 reload | 同一 Need「清爽花香/送礼/150–300」、同一 top card「候选 B / 安溪铁观音」；`decision_versions` reload 前后均为 5。 | PASS |
| RT-02 | candidate identity / presentation order | UI 首卡为 B（非输入 A）；latest Decision order1=B、order2=A；latest MerchantReply `candidate_id` 与 FollowupQuestion `candidate_id` 完全一致。 | PASS |
| RT-03 | 并发双击开始分析 | UI 只进入一份 result，未见重复 transition/console error；该轮使用既有 active flow，无法取精确新增 Job 差值。 | PASS_WITH_LIMITATION |
| RT-04 | 对价格问题输入无关文本后双击提交 | UI 只前进一题；DB Reply 8→9、重复组=0；绑定 question/candidate 正确；未提升为价格、焙火或香型事实。 | PASS |
| RT-05 | empty-question dead-end | 当前实例返回两个合法问题；不改变 seed 或客户端状态的条件下无法构造空问题。 | PASS_WITH_LIMITATION |
| RT-06 | 已保存 reply 后关闭追问层并 F5 | server-authoritative question/reply 正确恢复；DB 无新增 Decision。 | PASS |
| RT-07 | telemetry dependency | 浏览器没有网络故障注入接口；未见 console/network error。 | PASS_WITH_LIMITATION |
| RT-08 | 双击复制 | 剪贴板为当前问题文本；DB Reply/Decision 均未增加；仅一次 toast。 | PASS |
| RT-09 | 390×844 移动端 | 无水平溢出；“去问商家”CTA 在视口内且可点；追问层可打开。 | PASS |
| RT-10 | cold reopen / stale onboarding | 新 tab 从 Home 开始，不自动跳 result；Start 后恢复现有 active server flow。无受支持的清空客户端状态入口，不能断言 first-time onboarding。 | PASS_WITH_LIMITATION |

结论：`PASS_WITH_P2`。无 P0/P1 browser blocker。验证债务：空问题终态、analytics fail-open 的浏览器故障注入、fresh-profile onboarding 的可控清空。
