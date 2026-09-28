# S1-2R 当前主干复验准备

2026-09-28；FR-01/02/11/13/14，TD-04/10，D-13/15/17。

## 结论与基线

**虚构宿主闭环的开发复测通过，真实 DeepSeek API 尚未调用，S1-2R 尚未完成。**
本轮只增加复验记录和[供应商审查/待签运行单](s1-2r-provider-review.md)，没有修改业务代码、事件或研究准入语义。

- 代码：`47212fb058b3769bfb9a922fe33dbc7fad3e9e79`；分支 `codex/s1-2r-readiness`。
- 文档输入：飞书 PRD 292 / 技术 294，以及主目录最新开发计划。
- 宿主：Open WebUI 0.11.4，`8bd8b4fac5e059578ac0c74b3c18d11139f88b7d`。新建隔离源码副本，顺序应用 S0、timing、S1 三份主干补丁并重建前端。
- 实例：仅回环 8126 虚构 provider、8127 宿主；全新测试数据库与两个虚构用户。未使用真实 Key、旧聊天或公开数据集。

## 本轮实际证据

| 范围 | 结果 | 边界 |
| --- | --- | --- |
| 前端 | Vite 构建完成 | 构建不代表浏览器操作通过 |
| 原生回归 | 75/75 通过 | 针对真实固定宿主源码及完整补丁栈；开发侧重跑 |
| 登录 HTTP/WebSocket | 47/47 通过 | 运行 `qa.s1_http_probe --browser-request`，包含浏览器请求结构、越权、重试、撤销后新点踩、旧版本拒绝及正文最小化 |
| 实际浏览器 | 登录、生成、选“事实有误”、更正为“未按指令”、撤回完成 | 回答来自虚构 provider；本轮未留独立浏览器网络 trace，HTTP 证据单列 |
| 浏览器对象数据库对账 | 1 个合格生成、1 个动作、2 个 Outbox（动作门控和撤回各 1）、6 个事件 | 独立绑定该浏览器创建的对象；虚构 provider 无 usage，预留保留，不计为真实费用 |

浏览器事件序列：`negative_feedback_action_recorded → reason_displayed → reason_selected → reason_displayed → reason_edited → action_retracted`。
去标识证据见 `qa/evidence/2026-09-28-s1-2r/`；原日志、数据库、截图保留在工作树 `var/s1-2r-20260928/`。

## 复现方法与失败尝试

使用锁定的原生环境执行 `WHYNOTE_OPENWEBUI_SOURCE=<新隔离源码>` 下的 `pytest integrations/openwebui/tests -q`；原生 conftest 对完整补丁栈进行校验。按 `qa.s1_browser_host` 的 serve/provision 创建新实例，再执行 HTTP probe 和浏览器操作。

首次使用了不存在的单个测试文件，因此没有执行测试；首次前端构建因 node_modules 路径错误失败。修正后完成上述复测，失败日志保留。对旧源码逐份反向检查补丁存在重叠误差，未据此宣称旧部署损坏；最终使用新副本和完整栈检查。

## 剩余事项

真实 API Key 是否有效尚未知。供应商公开材料未补齐账户 API 留存、训练用途、删除机制；按已签 S1-1 仍阻断出站，限定虚构例外需责任人确认。云宿主配置、真实回包及费用、适用副本/删除、独立 QA、非作者批准和限定结果签署尚未完成。现有虚构研究记录不因此升级为 cloud 采集，完整 FR 和生产继续 NO-GO。
