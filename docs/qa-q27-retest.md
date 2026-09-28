# Q-27 独立复验：指定技术范围通过

2026-09-29，Asia/Shanghai。[PR #39](https://github.com/262412/-Whynote/pull/39) 仍待合并／阻塞：非作者有效批准与适用责任人签署未完成。

## 固定基线

- 修复候选：`fbd24ca3162f7af4b1252655bd3fd3c69e67d0b5`；远端 main：`3f4c9e898f82ece7bf8f428c99ade050b334b8b7`。
- 输入飞书 PRD 377／技术文档 375；映射 FR-06/11/13/15、TD-04/10/11，D-13/18。
- 使用无其他任务／进程占用的干净 QA 工作树，固定 detached checkout；锁文件同步后重新安装不可编辑包。实际安装的 suggestions、template_suggestions、research、research_report、s1_host 与源码字节一致。
- 修复仅改变服务端渲染到达时间的传递与校验；未修改前端、事件结构或 60 秒期限。本轮 QA 未修改业务代码或测试断言。
- `tests/test_templates_independent.py` 及 `qa/evidence/2026-09-29-m5-templates-independent/` 相对 QA 提交 `5227e61` 无差异。历史 675 通过／1 失败证据原样保留。

## 实际执行

| 检查 | 独立结果 | 说明 |
| --- | --- | --- |
| 原独立 M5-2b 验收 | 23/23 | 含原 Q-27 失败用例，内容未改 |
| 修复新增到达时间用例 | 23/23 | QA 审阅后独立运行；与上一行合计 46/46 |
| 完整核心及保留 QA | 699/699 | `tests`、S0、manual-v1；1 条既有 Starlette/httpx 弃用警告 |
| Ruff lint / format | 通过 | 87 文件格式合格 |
| 实际原生路由／ORM | 80/80 | 本轮重新运行，9 条既有依赖弃用警告 |
| 实际 Q-16 契约探针 | 58/58 | 新输出库，管理员他人访问预期 404 |

原生复用上轮隔离的 Open WebUI v0.11.4／`8bd8b4fac5e059578ac0c74b3c18d11139f88b7d` 源码，未覆盖旧数据库。fixture 在临时 Git index 校验完整四补丁栈及 JS 字节一致性，并为本轮创建新的虚构 SQLite。登录身份等原生测试替身边界沿用既有测试契约，不宣称完整登录 UI 实测。

## Q-27 操作、预期和实际

1. 实际 Action 创建点踩和建议；到期时间 `1788220860.0`。
2. 有效渲染回执在 `1788220859.5` 到达；宿主复查模拟耗时 1 秒，结束为 `1788220860.5`。
3. 原断言要求按时回执写入；新响应仍受原期限限制。

**实际符合预期：** `m52_render_reported` 已写入，后续响应超时，结果为 `fallback`；常规菜单关闭后动作仍 active，Outbox=1，没有研究确认。事件依序为：

```text
negative_feedback_action_recorded
m52_suggestion_generated
m52_render_reported
reason_displayed
reason_menu_closed
```

相较旧版本的 `superseded`＋零渲染事件，此路径已修复。`receive()` 的可信服务器到达时间传至 `render()`；省略内部时间参数时在事务前捕获。客户端只能回传 binding，不能提供到达时间。

## 期限、竞态与副作用

- **真实 SQLite 锁等待 2 项：** 显式传入或入口捕获到达时间，在 59.5 秒进入、等待跨过截止点后均保存渲染；报告为 1 渲染／0 有效响应、`unresponded`。后续迟到确认抛冲突，事件不变，没有延长有效期。
- **等待期间失效 8 项：** 停用、撤权、删除、动作撤销、新建议替代、建议撤销、协议或模型版本变化均拒绝写渲染，事件等于失效操作完成后的快照。
- **宿主复查跨期限 4 项：** 停用、撤销、换版、撤权仍返回 `superseded`，没有渲染追加；按时到达不能绕过当前权限和版本。
- **原期限外 3 项：** 生成前、60 秒整、60.5 秒到达均拒绝。非法服务器时间 5 项和客户端附加伪造时间 1 项均不能写入。
- 上轮独立的首次确认／更正失效、原键重试、常规回退、Unicode 引用等 23 项原样通过。

本轮未发现新的业务缺陷。Q-27 指定技术缺陷可记为独立复验通过；这不代替完整 FR、产品／客户端／数据签署或发布批准。

## 证据、复现和剩余门槛

[结果与源码摘要](../qa/evidence/2026-09-29-q27-independent/summary.json)、[46项逐例结果](../qa/evidence/2026-09-29-q27-independent/focused-results.json)。原日志及 JUnit 位于忽略目录 `var/qa-q27-retest/`；不提交数据库、配置或凭据。

```powershell
uv sync --locked --extra dev --extra jev --no-editable --reinstall-package whynote
uv run --no-sync pytest tests/test_render_receipt_timing.py tests/test_templates_independent.py -q -s
uv run --no-sync pytest tests qa/test_s0_regressions.py qa/test_manual_v1_acceptance.py -q
uv run --no-sync ruff check src tests integrations qa
uv run --no-sync ruff format --check src tests integrations qa
```

本轮未重新运行浏览器或前端生产构建。上一轮 `5474496` 的浏览器正常链路仍是历史证据；本轮针对改动执行受控时钟、实际锁等待、Action 和原生 ORM 检查。没有真实本人试用、真实模型调用、费用或质量评测。

候选 `fbd24ca` 的 CI `36455390679` 两项通过；QA 文档提交后的最新检查另在 PR 核验。非作者有效 APPROVED、产品／客户端／数据契约与结果签署仍缺。原 PR 保持未合并，分支、工作树及审计数据保留供评审；没有启动临时服务或改动原目录。

2026-09-29本轮独立复验已按准确revision-id更新19个飞书原条目，读回PRD387／技术文档384；历史归档与既有链接保留。
写入回执见[记录](../qa/evidence/2026-09-29-q27-independent/feishu-writes.json)。
