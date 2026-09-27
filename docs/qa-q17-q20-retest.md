# Q-17～Q-20 独立复验及 Q-21（2026-09-27）

**原 Q-17～Q-20 的指定技术子项通过；补查发现 Q-21／P2：已完成点击的重试重新打开菜单并追加更正。当前切片仍不满足合并/完整验收门槛。**

## 实际基线与执行范围

- main `f560c4abe03a6c8579eae95e433766825c50f230`，已合入 PR #21 `a40c401f7adc3ab603c06349cecb75b41febcbfc`，两者文件树相同。PR #18/#19 也已合并。本 QA 未执行这些合并。
- 本轮读取 PRD **84**、技术文档 **24**，沿用签署的 B1 v1；映射 FR-01/11/13/14/15、TD-04/09/11、D-15、H-06、Q-17～Q-21。技术文档新增 Git 交付/分支规约，不是产品准入放行。
- 按更新后的原目录开发规约复用 `codex/qa-manual-v1` 和 PR #20，将最新 main 合入；只解决 CI 步骤名称冲突，保留相同验收命令。原工作目录未提交改动、开发审计库和上一轮失败证据保留。
- 重装当前锁定包：`uv sync --extra dev --locked --no-editable --reinstall-package whynote`。没有复用旧安装包作本轮通过依据。
- 原两份 QA 脚本与 PR #20 `ab2259e` 到修复 `a40c401` 的 Git diff 为空；先原样重跑，再新增边界。此前 **1 通过/9 失败**、HTTP **33 通过/2 失败** 不改写。

## 本轮实测

| 检查 | 结果与限制 |
| --- | --- |
| 原样全套 | **103/103**，含既有测试、原独立验收 10 项和开发补充 3 项 |
| 扩展独立验收 | **17 通过/2 失败**：原 10 通过，新增 7 个非法计时边界通过，2 个点击重试用例失败 |
| 新实例回环 HTTP | **35/35**，真实 uvicorn/demo、临时 demo 身份及 SQLite；不是浏览器或真实宿主登录 |
| 固定宿主原生 | **53/53**，实际 ORM/FastAPI 评分路由，全新虚构 SQLite；native 补丁未改变 |
| Ruff lint/format | 全部通过，27 个文件格式通过 |
| 固定补丁 | 上游 `8bd8b4fac5e059578ac0c74b3c18d11139f88b7d`；新干净检出依次检查并应用 native、最新 timing 补丁，timing 逆向检查通过 |
| 既有 CI | main [36294109826](https://github.com/262412/-Whynote/actions/runs/36294109826)、PR #21 [36293942506](https://github.com/262412/-Whynote/actions/runs/36293942506) 均成功；未包含本轮新增重试失败 |
| 独立浏览器 | 枚举报 `nodeRepl.fetch request failed`，未取得可操作浏览器。本轮未独立跑前端构建、UI 或真实失焦计时；开发侧浏览器保留为[开发证据](q17-q20-fixes.md) |

套件覆盖重叠，不合计为样本量。native SHA256 `f2eedf75e3be42786265c742be7f9701b5717311b9ed34efaee29f5d6881d8e8`；timing SHA256 `1db5759e8c4e50e4ce36f71e0a8c5682cf30cbe74ee1194c5c429e56f03a3645`。

## 已验证的修复子项

- **√ Q-17 原复现**：撤销后新点击产生不同 event_id，动作与门控 Outbox 各 2；已撤销的旧点击重试不弹菜单、不追加。同一有效意图的新点击跨会话复用。无 click ID 的兼容代次在受控并发中不重复创建。旧协议无法区分跨撤销延迟重发，完整区分依赖新前端，该限制已有说明。下文 Q-21 是未撤销且已完成点击的另一条重试路径。
- **√ Q-18**：都不是、暂时跳过、不愿说明、关闭及取消五条路径均不再宣称空原因已记录；原因清空/保留规则未变。验证是 Action 通知输出，未冒充浏览器观看。
- **√ Q-19**：active_ms/elapsed_ms 超大整数得到 HTTP 200，反馈追加一次，测量标 `invalid_duration`、主动值为空。同键重试（含计时修正）不重写首次测量；改变实际原因仍冲突。额外超大负数、布尔、字符串、None、Infinity/NaN 均降为无效测量，不抛溢出异常。真实回环探针 35 项全部通过。
- **√ Q-20**：在 25h 撤销后，`retracted_action_count=1`，原 `window_status_counts={unresponded:1}` 与固定分母 1 保持；历史 as_of 报告一致，DB 哈希不变。
- 重复响应计数单位仍区分唯一动作数与发生次数；未把一次动作的两次更正错误改计为两个动作。

## Q-21／P2：已完成点击重试重新展示并追加更正

**契约依据**：B1 v1 保留请求重试去重；Q-17 修复说明明确同一次点击使用同一 UUID，重试返回旧结果。当前实现只覆盖已撤销动作的旧点击重试，活跃动作完成后的重试仍继续整个菜单流程。

**操作**：在实际 `Action.action` 中，合法固定虚构对象、Alice 身份、固定 `whynote_click_id`，第一次选择“事实有误”；原请求完成后，用同一 click ID 重发。分别保持原 session、改为重连 session。测试仅替换宿主对象查询和菜单回调，事件/投影/幂等均使用实际 EventStore。

**预期**：该次点击的结果被重放，不再调用菜单回调，不签发新展示、不追加响应，事件和已记录的测量保持不变。新用户点击应使用新 UUID，仍可重开菜单。

**实际，两种会话条件一致**：

```text
menus: 1 → 2
events before: negative_feedback_action_recorded → reason_displayed → reason_selected
events after:  原三条 → reason_displayed → reason_edited
attribution_status: selected → edited
```

原因：`create_action()` 能复用 event_id，但 `Action.action()` 在得到 active 投影后无点击完成状态判断，继续生成新的 display_id 并再次收取响应。新响应键按新 display_id 构造，因此评分操作层的幂等无法阻止这次追加。没有新建点踩不等于完整点击重试无副作用。

**影响**：一次明确点击的网络重发被当作新交互，增加展示和更正次数，改变响应来源及统计。此次只确认顺序重试；并发重试覆盖仍应补齐，不外推已验证所有网络竞争。

**下一次复验**：实现者在同一点击生命周期内区分首次执行、进行中与已完成；绑定身份和目标。覆盖同/跨会话、完成后/进行中重试、撤销前后旧点击、新点击重开，保证旧重试不污染事件/测量或使合法菜单失效；保留所有旧失败和新点击正例。不得通过每次重发随机生成 UUID 规避重复请求。

## 证据和复现

- [本轮摘要](../qa/evidence/2026-09-27-q17-q20-independent/summary.json)，含结果、事件类型序列和补丁哈希；完整虚构数据库及日志在忽略目录 `var/qa-q17-q20-retest/`。
- 原样基线命令：`uv run --no-sync pytest tests qa/test_s0_regressions.py qa/test_manual_v1_acceptance.py -q`，在新增边界前为 103/103。
- 新增后执行 `uv run --no-sync pytest qa/test_manual_v1_acceptance.py -q -s`：17 通过/2 失败。该文件已被 CI 执行，无 skip/xfail。
- HTTP：新 demo 使用 `--db var/qa-q17-q20-retest/http.db --port 8106`；`qa/manual_http_probe.py` 对空库运行并保存新结果文件。首次原反馈/重试对应断言保持不变。

## 交付、评审及清理判断

- 沿用测试 PR #20 并将目标调整为 main，避免继续依赖已合并的 #18 分支。不新建同切片 PR，不自行追认 #21 的既有合并为完整验收。
- PR #21 当前仅 COMMENTED，无非作者 APPROVED；main 未启用保护。本 QA PR 有实际失败且缺有效非作者批准，不能合并，也不能称“研发交付完成”。实现者修 Q-21，非作者审查人批准，责任人签本轮结果后再检查合并条件。
- 当前 QA 分支保留，因为失败用例和报告尚未合并；工作树及忽略的审计库保留供复验，解除条件为修复/验收和数据保存安排完成。未执行旧分支批量删除或工作树清理。
- 临时 8106 服务已停止；原目录状态与开工一致。真实数据、原生评分复用、模型、auto-attach、自由文本 SLM、训练导出、生产仍 NO-GO；副本留存和环境级出站控制未获得新的通过证据。

## 飞书回写

- [PRD 修订 85](https://my.feishu.cn/wiki/XG6SwutL0i3fyWkwmhScEEB86Gg#doxc6M6lEkRK9rFoqDDJ3i6rrAe)：以修订 84 追加。
- [技术文档修订 25](https://my.feishu.cn/wiki/GuphweJs3iWwBRkBDjlcljA16bh#doxc6XWF7H0V8h8g2FVwp9odeRh)：以修订 24 追加。
- 两份均已读回，确认历史全文前缀保留且本轮 QA-20260927-R2 章节存在；只标记已验证子项，Q-21 与完整验收保持开放。
