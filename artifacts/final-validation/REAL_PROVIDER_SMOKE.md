# Real Provider Smoke

日期：2026-08-13。

## 结论

`BLOCKED — PROVIDER_INPUT_UNAVAILABLE`。

运行环境检测到 MiMo 凭据存在；项目文档的目标模型为 `mimo-v2.5`。但当前仓库可用的图片均为项目自有的测试/演示 fixture，其中明确标有 demo fixture 标识；本轮没有用户提供或仓库内可合法使用的真实茶商品截图。

因此没有向真实 Provider 发送图片：

- real multimodal calls：0 / 4
- 成本：0
- Provider 输出：无
- 不使用 fixture 冒充真实截图，不产生“真实模型准确率”声明。

待获得一张已获授权的真实商品截图后，可按项目既定 MiMo 配置执行至多 4 次最小烟测，并只记录 provider/model、耗时、结构合法性、错误类别与匿名运行标识；不在报告、事件或仓库中写入密钥或图片原文。
