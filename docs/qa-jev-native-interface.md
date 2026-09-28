# PR #28 独立 QA：Q-26 修复复验通过

## 最新独立复验（2026-09-28）

固定提交 `c43e3fdca7aef376aa85119c5b5c9a14b3fa21ac`，依赖 PR #27 / `f341a74`；main 仍 `9859fce090e636664216dad9e2fcbf94605149c8`。读取飞书 PRD 264 / 技术文档 261。用该提交干净 Git archive、新虚拟环境、锁文件安装 dev/jev extras。

- 全套核心及 QA **258/258**、Ruff check/format 通过。
- 原独立用例 **6/6**，逐字节（归一化换行）确认与 `b1c7c76` 相同，SHA256 `e67ebb978c605910caeabd595a60dd0e22a7133575d5a13eddbae25baf9c8b0f`。已请求的两种 Noul 答案为 null、空对象或布尔值时均拒绝，每例仅一次请求，没有静默补分。
- 适配器、独立用例和密钥测试合计 **54/54**（包含在 258 项内，不重复相加）。涵盖新增 18 项边界：合法 0/1、缺失、null 值、布尔值、越界、字符串及类型不匹配；未请求信号仍可省略，已有 0.5 合法值、Choice、模型失配及单次请求等回归通过。
- PR #27 的相同宿主补丁及 S1 源码在本轮实际原生环境 **72/72**，并完成修正停用探针的独立演练，见 PR #27 QA 提交 `7f7dba0`。这是共享代码验证，不冒充 Jev 与宿主已接通。
- 开工 CI `36375934991` 两项通过。只用 MockTransport、临时虚构密钥；没有真实供应商调用、菜单接入或新增浏览器结论。

**Q-26 指定修复子项独立通过。** 非作者 APPROVED、责任人结果签署仍缺；FR-06/TD-07 完整验收、真实准入/预算消费和生产继续 NO-GO。依赖顺序 #27 → #28，合并前需核对目标与当前版本检查。本轮仅更新报告；原始日志/JUnit 在 `var/qa-final-20260928/pr28.log`、`pr28.xml`、`jev-focused.log/xml`，历史失败不改写。

## 上一轮失败记录（原样保留）

2026-09-28。业务基线 `4b5743964134aee35c7da1c045806e7e0ffd1f05`，依赖 PR #27 / `a4a0614`；不是 main。映射 FR-06/TD-07 传输与响应校验子项。读取飞书 PRD 255 / 技术文档 248，沿用默认关闭、实际准入/预算由未来调用方执行的有限契约。

## 本轮执行

- 在干净 Git archive 和独立锁定虚拟环境安装 dev/jev extras，核心及既有 QA **234/234** 通过，包含适配器 25 项和密钥 5 项。只用 MockTransport 和临时虚构密钥，没有读取用户 private 文件、调用真实服务或产生费用。
- 审查固定 HTTPS endpoint、模型、Bearer、单次请求、无重定向、总超时、输入/响应大小、默认关闭以及门控前不读取上下文。无菜单/事件/Outbox 调用方，不能声称预算、准入、推断或完整 FR 已接入。
- 对照 [官方 API](https://docs.typesafe.ai/api) 与[模型页](https://docs.typesafe.ai/models)：请求为 state/model/questions，响应每个已请求问题须有对应类型答案；Noul 是带 type/noul 的对象。此为文档对照，不是真实模型运行验收。
- 新增独立验收 **4 通过、2 失败**。`tests/test_jev_independent.py` 保留严格失败断言，无 skip/xfail。Ruff 通过。

## Q-26 / P2：已请求 Noul 返回 null 被静默接受

位置：`src/whynote/jev_provider.py` 响应校验调用，以及 `domain.validate_jev_response` 对 signal=None 的处理。

步骤：调用默认关闭接口的显式启用测试路径，提供纯虚构审查/预算引用及临时密钥，以 MockTransport 返回合法固定模型/Choice/usage；分别在已请求的 `factual_error_signal`、`instruction_failure_signal` 字段返回 JSON null。

预期：两种情况均以通用契约错误拒绝，不能把缺失的必答信号包装成成功结果。

实际：两者均正常返回，`binary_signals={}`。适配器只比较 answers 的键集合；复用校验器把 None 视为未请求的可选信号并跳过。因此“键存在”没有证明“请求的问题得到了合法答案”。返回 `{}` 或 `true` 的四个负向对照均被拒绝。

此缺陷是本地响应校验问题。当前没有生产调用方，未造成用户数据写入；但会让未来调用方误将不完整 provider 结果视为已校验成功。不能通过允许 null、删断言或补造 0 分修复。

下一次：开发修复必须使已请求的每个答案通过类型和值校验；保留“未请求的可选信号可省略”，并回归合法 Noul 0/1、缺失/null/错误类型、Choice、模型漂移与零自动重试。最终独立复验、非作者 APPROVED、相应签署及真实出站准入仍待完成，生产 NO-GO。

原始本轮日志在 QA 工作树 `var/qa-s1-retest-20260928/pr28-core.log`、`jev-independent.log/xml`。QA 只增加本报告与验收测试，业务代码未改。继续使用原 PR #28，不另开同切片 PR；依赖合并顺序仍为 #27 → #28，后者须在适当时机改目标 main。
