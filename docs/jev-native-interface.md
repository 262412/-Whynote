# TypeSafe Jev 原生接口预留

2026-09-28，按用户“给 TypeSafe 原生 Jev 预留 API 接口”要求准备；这是 FR-06/TD-07 的传输与响应校验子项。未接入菜单、Outbox worker、原因推断或 auto-attach，不代表模型效果或真实出站获准。

## 固定接口与调用边界

依据当日读取的[官方 API](https://docs.typesafe.ai/api)与[模型说明](https://docs.typesafe.ai/models)：

- 使用 `POST https://api.typesafe.ai/v1/systemone`，Bearer 凭据；请求为 `state / model / questions`，不是聊天 completion 接口。
- 首版固定 `jev-1.13.0`，不使用漂移别名。调用方提供 `primary_reason` Choice 指令及现有 taxonomy 全部选项；可附带现有两个 Noul 信号。接口不替调用方编造评测指令或类别说明。
- 响应复用现有 `validate_jev_response`：固定模型、Choice 概率/类别/置信度、Noul 及 usage 检查。返回结构化模型结果，绝不写成用户确认原因；不写事件、队列、日志或数据库。
- 默认关闭。只有调用方显式启用且提供出站审查和预算预留引用后，才读取本机密钥并执行延迟的 `state_factory`。这些引用是调用契约，不是本适配器执行了预算或权限检查的证明；未来调用方必须先执行真实门控、快照准入、预占及失败对账。
- 单次 HTTP，无自动重试、无重定向；总请求超时60秒，连接10秒。429/529、错误响应、超时、模型漂移直接失败；错误消息不携带请求、响应或Key。输入最多8192 UTF-8字节，响应最多1 MiB，这是预留接口本地限制，未声称覆盖供应商全部能力。

## 本机凭据与接入点

`D:/PythonProject/jev项目/var/private/provider-keys.json` 的 `typesafe` 字段供此接口使用；DeepSeek使用独立的 `deepseek` 字段。文件由用户填写，已被Git忽略；示例文件无真实Key。

接入点：`whynote.jev_provider.evaluate_reason`。安装可选依赖：`uv sync --locked --extra jev`。调用方提供已获准上下文的延迟读取函数、问题定义、keys_file绝对路径、审查与预算预留引用；函数默认 `enabled=False`。不提供一键真实调用脚本，以免把预留接口误当成已验收试用入口。

## 兼容、回滚与验收

不修改现有事件、投影或schema。移除适配器或保持关闭即可回滚；现有手动原因链路不依赖此模块。此次仅用MockTransport测试协议、拒绝零上下文读取、错误与版本失配；没有发送真实模型请求或消耗用户预算。实际模型版本/账户可用性、供应商数据条款、预算消费者和独立质量评测仍待后续获准切片。

本切片明确依赖 PR #27 的私密凭据加载器，合并顺序 #27 → 本片；不得把依赖分支已测试当作 main 已包含改动。
