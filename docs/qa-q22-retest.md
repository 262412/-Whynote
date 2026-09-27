# Q-22 独立复验：预约等待已修复，回调调度边界未通过

2026-09-27。**Q-22/P2 部分修复，仍开放；PR #23 不满足验收/合并门槛。** 映射 FR-11/13、TD-04/09/11、D-15，回归 Q-11/Q-21。

## 实际基线

- 测试 PR #23 head `afb9ea13cd04559de4f01f1099633cc79c824895`，尚未合并；main `e15a0fe64826a8b5f4cc34e3a0be7c58e1a82f96` 仍含旧问题。修复 CI 36297274675 两项通过，未含本轮新增边界。
- 飞书读取 PRD 183、技术文档 173。当前点击契约新增 expires_at=0 的未发布期限、提交后计时、回调到达判定与期限写入失败/崩溃规则。本轮按这些具体条目验证，不改产品契约迁就代码。
- 复用干净空闲 QA 工作树及 `codex/qa-q21-retest`，继续 PR #23。锁定安装并重装当前 whynote 包；原目录未提交改动和旧审计数据保留。未修改原失败断言、业务实现或历史失败证据。

## 实际执行结果

| 范围 | 结果与限制 |
| --- | --- |
| 原样全套 | 143/143（核心107、票据9、原独立扩展27） |
| 增补后独立扩展 | 29通过/2失败：新增无调度等待两条通过、有调度等待两条失败；无 skip/xfail |
| 新实例回环 HTTP | 35/35；实际 uvicorn/TCP/SQLite、固定虚构 demo 身份，非宿主登录或浏览器 |
| 原生补丁回归 | 53/53；固定上游 8bd8b4fac5e059578ac0c74b3c18d11139f88b7d，实际 ORM/FastAPI，全新虚构 SQLite；原生补丁此次未改 |
| Ruff lint/format | 通过；30 个文件格式通过 |
| 独立浏览器 | getState 返回 nodeRepl.fetch request failed，无可操作浏览器；本轮无 UI/前端构建/真实宿主重发通过结论 |

套件覆盖有重叠，不加总为样本量。开发侧证据见 [修复记录](q22-fix-verification.md)，与本轮独立结果分开。

## 已验证的具体子项

- √ 显式 click ID 与旧兼容路径：预约写锁等待后，菜单内受控 59.5 秒合法提交成功，60.1 秒拒绝；原两条失败不改断言而通过。
- √ 开发补充的实际读锁引起 commit 等待、期限写入等待、一次性期限/归属校验、0 值跨实例恢复、新点击替代、59.999/60 秒、墙钟和单调时钟边界，9 项在独立环境原样通过。
- √ 原 Q-21 完成/进行中重试、撤销/替代/权限/升级/事务回滚与全部表/有效计时不变的既有回归通过。
- 按时返回后，仅发生 await deadline_task 的等待路径通过；不能由此推导所有“回调到达后等待”均通过。

## Q-22 剩余失败：回调已返回，外层恢复前等待导致误判

**操作**：实际 Action/EventStore，新虚构 SQLite；分别有/无显式 click ID。显式路径先确认期限已持久化，排除期限写入争用。另一连接 BEGIN IMMEDIATE 持锁；菜单回调受控推进 59.5 秒并取得有效签名，在返回前通过 call_soon 排入一次实际 EventStore.get_events 读路径（其事务使用 BEGIN IMMEDIATE），随后立即返回。持锁线程从回调返回信号起延迟 0.75 秒释放。队列中的读事务先占用事件循环并等锁，Action 的 wait_for 外层稍后恢复。

**预期**：回调按时返回，有效签名可提交，动作→展示→选择；调度等待不改变已到达时间。

**实际**：两条路径回调返回时分别尚余约 0.500/0.484 秒；排队事务结束时分别已逾期约 0.359/0.266 秒。返回 ticket_expired，数据库只有 negative_feedback_action_recorded，无展示/选择。busy_seconds=0 的两个正例均 reason_submitted，三条事件。精确数值见摘要；这是受控用户时间和事件循环调度，SQLite 等锁是真实执行，未冒称浏览器/真实并发 HTTP 复现。

**原因**：`expired` 在 `await asyncio.wait_for(...)` 外层恢复后才采样，尚未记录菜单回调协程实际返回时的时间。同步 EventStore 事务会阻塞同一循环；因而外层恢复时间可晚于回调到达时间。代码已经避免后续 await deadline_task 的等待计入期限，但没有覆盖这段调度间隙。

**影响与下一次复验**：临界时间内的合法反馈仍可丢失，显式/兼容路径都受影响。实现者须在回调返回处捕获单调/墙钟时刻，并保持真正超时、期限发布失败、旧回调/撤销/替代拒绝与原子预约；复跑原27、新4及相关开发时序9项。Q-22 保持开放，不另编号、不降低断言或跳过失败。

## 证据与交付

- [本轮摘要](../qa/evidence/2026-09-27-q22-independent/summary.json)；完整虚构数据库、JUnit、日志保留在 var/qa-q22-independent/。
- 原样命令 `uv run --no-sync pytest tests qa/test_s0_regressions.py qa/test_manual_v1_acceptance.py -q` 为143/143；增补后 `uv run --no-sync pytest qa/test_manual_v1_acceptance.py -q -s` 为29/2。
- 继续 PR #23，只有测试、文档与摘要变更。评审仅 COMMENTED，无非作者 APPROVED，责任人结果签署缺失；保持待修复/待评审，不合并或代签。
- 原目录和历史失败保留；分支/工作树及忽略审计数据供实现者复验，未做批量清理。生产、完整 FR、真实数据、原生评分复用、模型/auto-attach/自由文本 SLM/训练导出继续 NO-GO。

## 文档与收尾

- [PRD 194](https://my.feishu.cn/wiki/XG6SwutL0i3fyWkwmhScEEB86Gg#doxc6F3irOj8OLmcaTNO06XWxgc)，输入183；[技术文档182](https://my.feishu.cn/wiki/GuphweJs3iWwBRkBDjlcljA16bh#doxc60MhFv8clu5X0J2VFa4q0vb)，输入173。
- 原状态、FR、click ID期限字段、M3/M4、TD-04及Q-22条目局部更新；每次写前复核目标文本和准确revision-id，全文读回确认历史归档逐字相同、既有链接保留。没有文末追加进度清单。
- 首轮QA CI [36297911316](https://github.com/262412/-Whynote/actions/runs/36297911316)：原生通过，quality两条回调调度失败（29/2），与本地一致。最终提交CI另见PR #23当前head。
- 8114临时服务按进程命令核验后停止；原工作区未变。保留当前分支/工作树及虚构库供修复复验，未执行合并或删除。
