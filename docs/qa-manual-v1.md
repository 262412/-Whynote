# B2a/B2b 独立 QA：manual-v1（2026-09-27）

**结论：指定正常路径通过，但独立验收不通过。新增 Q-17～Q-20 均为 P2；三项用户操作/输入问题和一项窗后撤销汇总缺口待修复。完整 FR、真实数据、原生评分复用和生产继续 NO-GO。**

## 实际基线

- main：`944c3acdba75e76c9cd69306d660431f865f30fc`，已含上轮测试 PR #17，尚无 B2a/B2b。
- 本轮实际测试：PR #18 当前 HEAD **`4d2ba0fdf2730a6a3048e76a391249cd65148c3b`**。PR #19 已合入 #18 的 `codex/s0-menu-readiness` 分支；#18 仍 OPEN、未进入 main。本 QA 未合并它们。
- B2a 原提交 `678fcae182ec169f958cea44d5966ab19ee29832`，B2b 提交 `edcfd185ccf857361808341b5ee10199838e194c`。本轮运行的是二者合并结果，不冒称分别独立运行两个历史快照。
- 飞书开工修订：PRD **82**、技术文档 **22**。PM-20260927 仍将 B1 列为待冻结准备；[仓库 B1 v1](manual-reason-contract-proposal.md)及用户交付消息明确已签署，按此执行，不重复请求同一规则批准。本次飞书追加记录这一新事实与测试结果。
- 映射 D-08/14/15、TD-02/03/04/09/11、FR-01/11/13/14/15、Q-04/07、H-06；Q-16 历史保留及原生数据路径继续受既有契约约束。
- 复用干净 QA 工作树，从 #18 最新提交新建 `codex/qa-manual-v1`。原目录既有改动（含开发计划）及开发审计库均保留。

## 执行与通过范围

| 检查 | 本轮独立结果 |
| --- | --- |
| 锁定环境 | 首次复用环境加载旧版已安装包，出现 4 个 collection errors。定位为本地包缓存，用 `uv sync --extra dev --locked --no-editable --reinstall-package whynote` 重装当前源码后消除；未作为业务缺陷 |
| Ruff | lint / format 通过 |
| 既有核心及票据 | **90/90**（81 核心、9 票据）；真实 EventStore、投影重放及受控菜单回调 |
| 新增独立验收 | **1 通过 / 9 失败**，共 10 个用例；失败对应下文四项缺陷，无 skip/xfail |
| 新实例回环 HTTP | **33 通过 / 2 失败**，35 项检查；失败均为 Q-19 的状态码及未持久化断言。实际 uvicorn/demo HTTP、临时 demo 身份、SQLite 对账；不是浏览器，也不是宿主真实登录 |
| 实际 Open WebUI 原生回归 | **53/53**；上游固定 `8bd8b4fac5e059578ac0c74b3c18d11139f88b7d`，实际 ORM/评分路由、全新虚构库。原生补丁与上轮一致 |
| 补丁检查 | 新检出依次应用 native 与 manual timing 补丁，正向检查及 timing 逆向检查通过 |
| 上游 CI | main [36287965797](https://github.com/262412/-Whynote/actions/runs/36287965797)、#18 当前 HEAD [36292597662](https://github.com/262412/-Whynote/actions/runs/36292597662)、#19 [36291933646](https://github.com/262412/-Whynote/actions/runs/36291933646) 均通过；未覆盖本轮新增失败 |
| 浏览器/前端构建 | 浏览器枚举报 `nodeRepl.fetch request failed`，未取得可操作实例；本轮未独立运行 UI 或宿主前端构建。开发浏览器和构建仅作为[开发留存证据](manual-slices-delivery.md)引用 |

套件覆盖重叠，不合计为验收样本量。原生补丁 SHA256 `f2eedf75e3be42786265c742be7f9701b5717311b9ed34efaee29f5d6881d8e8`；计时补丁 SHA256 `e6c201bddfbf4ddfd57a5e4b6dacba17409a5880a818cb016dcdde4e80f17da8`。

√ 局部通过：选择与更正来源正确；跳过/拒填/关闭保留已有原因，都不是清空；重新展示、旧回调零追加、跨会话有效动作复用、并发动作/门控 Outbox 去重、旧事件兼容通过。24 小时半开窗口、到期响应标迟到、历史 as_of 稳定、报告读取不改变 DB 哈希、分母含撤销、其他或无法归类计入填写、正常主动时长及缺失原因通过。该 √ 不涵盖下列失败或真实浏览器主动计时。

## 开放缺陷

### Q-17／P2：宿主同会话撤销后不能再次主动点踩

- **操作**：实际 Action 选择原因；通过既有 EventStore 撤销命令撤销；保持 user/session/target/version，再次调用该入口。仅宿主对象查询及菜单回调为替身。
- **预期**：v1 要求撤销后主动点踩创建新 event_id；旧请求重试仍返回旧结果，应区分两者。
- **实际**：返回相同 event_id、`result=retracted`，新菜单回调未运行。数据库只有 1 个动作及 1 条 `feedback.gate.requested`。撤销自身的 Outbox 另计，不能把全部 Outbox 数误作点踩数。
- **原因**：`s0_action.py` 请求键固定取 user/session/target/version；`create_action()` 先命中旧幂等记录，无机会创建新意图。服务端使用新请求键的正例已通过，缺口位于宿主入口。
- **下一次**：用明确的新点击标识区分新意图与网络重试；同会话撤销→再点踩得到第二个动作及第二条门控 Outbox，旧请求重放零新增。不要通过每次盲目随机请求键破坏重试语义。

### Q-18／P2：无原因操作收到已记录原因的错误提示

- **操作**：在初次菜单分别选都不是、暂时跳过、不愿说明、关闭，及返回 modal 取消 false。
- **预期**：确认用户响应或关闭，不宣称记录了原因；数据库继续保留空原因。
- **实际**：五种情况 `reason_code=null`，都发出成功通知“知因 S0 反馈与原因已记录”。Action 确实产生该通知；本轮未在真实浏览器观看提示。
- **原因**：成功通知没有按响应类型区分。
- **下一次**：五种无原因路径提示准确；选择/更正仍能确认已记录原因。不得修改数据库语义来迎合提示。

### Q-19／P2：非法可选计时阻断反馈，HTTP 500

- **操作**：合法主体、对象、展示及选择请求，仅将 timing.active_ms 或 elapsed_ms 改为 `10**400`（JSON 整数）。
- **预期**：反馈 200、追加一次用户响应；主动耗时为空，`timing_status=invalid_duration`；同键重试不再追加。
- **实际**：两种输入均 HTTP 500，事件保持动作+展示两条，无用户响应。实际回环 HTTP 再次复现，日志 `OverflowError: int too large to convert to float`。把计时改回合法值后重试成功；失败没有部分写入。
- **原因**：`timing_payload()` 对任意 Python int 调用 `math.isfinite()`，转换超大整数时溢出，异常穿过事务及 HTTP 路由。
- **下一次**：超大正/负整数及其他非法计时均只影响测量状态，不阻断反馈；验证准确码、事务、同键重试、正常耗时及缺失状态。

### Q-20／P2：窗后撤销没有单独汇总

- **操作**：动作和展示在 9 月 1 日 00:00 UTC；24 小时到期无响应；9 月 2 日 01:00 撤销，再以该时刻生成报告。
- **预期**：保留原 24h 窗口结果和固定分母，同时按 v1“撤销另报”给出截至 as_of 的撤销计数，不能只让消费者把已撤销动作看成当前未响应。
- **实际**：`denominator=1`，`window_status_counts={unresponded:1}`，行内 `current_state.action_status=retracted`；没有当前撤销汇总。历史 as_of 重读一致、DB 哈希不变。历史窗口本身没有算错，缺的是独立的当前撤销计数。
- **下一次**：区分窗内/截至 as_of 撤销，窗后撤销单列，不改写历史窗口、不缩分母。用例以 `retracted_action_count` 表达所需计数；该名称是 QA 建议的输出字段，若最终接口采用其他明确名称，同步测试字段绑定但仍断言数量 1。无需将已正确冻结的 window_status 改成撤销。

以上责任为 B2a/B2b 实现者修复，后端/客户端负责；数据/产品核对测量输出单位和字段，QA 独立复验。规则签署已有，未虚构新的具名签署或期限。

## 未直接登记为计数算法缺陷的评审意见

同一动作选择一次、更正两次：行内 `response_counts_in_window.reason_edited=2`，汇总 `response_action_counts_in_window.reason_edited=1`。后者当前代码和字段名均表示“发生过该操作的唯一动作数”，并非操作发生次数；分母/填写数均为 1。本轮保留此 1/2 对账正例，没有机械采用自动意见把唯一动作数改成 2。

交付记录的“显式操作数”未说明汇总单位，建议后续显式提供 occurrence 次数和 unique-action 数并标注单位。不能用当前唯一动作数字段作为操作频次。此为报告口径澄清项，与 Q-20 已签署的“撤销另报”要求分开处理。

## 证据、复现和准入

- [逐项摘要](../qa/evidence/2026-09-27-manual-independent/summary.json)、[独立失败用例](../qa/test_manual_v1_acceptance.py)、[真实回环 HTTP 探针](../qa/manual_http_probe.py)。原始日志、合成数据库及 Feishu 快照位于忽略目录 `var/qa-manual-v1/`。摘要不包含会话令牌、密钥或原始问答。
- 执行 `uv run --no-sync pytest tests qa/test_s0_regressions.py -q` 得到既有 90 通过；执行 `uv run --no-sync pytest qa/test_manual_v1_acceptance.py -q -s` 得到 9 失败/1 通过。新增用例接入 CI，失败不标记 xfail 或跳过。
- HTTP：新目录运行 `uv run --no-sync python -m whynote.demo --db <新库> --port 8106`，另一终端运行 `uv run --no-sync python qa/manual_http_probe.py --db <新库> --output <新结果文件>`。探针拒绝有事件的旧库，失败返回非零。其主动计时输入为脚本构造，不证明前端 performance.now/失焦行为。
- 浏览器连接恢复后，仍需 A1 Q-16 真实宿主保存/更正/删除历史保留，以及 B2a/B2b 菜单、重开、跨会话、撤销再点踩与真实切窗主动计时的独立验证。开发侧这些结果不改记为本轮 QA 实测。
- 本轮仅改测试、CI 和报告，不修业务代码，不自行合并或签署结果。非作者 APPROVED、结果签署、副本保留期限及环境级出站控制仍缺；业务缺陷先修复，不能据既有绿色 CI 放行。

## 飞书回写与收尾

- [PRD 修订 83](https://my.feishu.cn/wiki/XG6SwutL0i3fyWkwmhScEEB86Gg#doxc6tkku7tqgxyv11ZpsU5eXZb)，按准确 revision-id 82 追加。
- [技术文档修订 23](https://my.feishu.cn/wiki/GuphweJs3iWwBRkBDjlcljA16bh#doxc6ouoII5tEXhRbAgM7lgk5Jf)，按准确 revision-id 22 追加。
- 两次写入 success、无 warnings，完整读回确认旧文仍为新文前缀，本轮段落存在。保留上轮 Q-11～Q-16 通过及更早失败历史。
- localhost:8106 服务已停止，端口不再监听；原目录 `git status --short` 与开工一致。虚构证据保留，不清理历史副本。
- 测试 PR 依赖 #18 分支；新增 CI 明确执行失败用例，预期保持失败直到业务修复。本 QA 不降低断言或改业务实现来取得绿色结果。
