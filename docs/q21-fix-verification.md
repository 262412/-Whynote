# Q-21 开发修复与复测

日期：2026-09-27。映射 FR-01/11/13/14/15、TD-04/09/11、D-15、H-06。

## 范围与基线

继续使用 PR #22、`codex/qa-click-retry`，基线 `b309332`，其业务代码来自 main `f560c4a`。QA 工作树干净、所属 QA 任务空闲、无其目录相关 Python/Node 进程后在该树继续工作。未修改原目录或原失败测试、历史证据。

原样复现 `qa/test_manual_v1_acceptance.py`：17 通过、2 失败；同点击两次开菜单并追加更正。原输出保留于 `var/q21-before.log`。

## 修复

- `EventStore.begin_host_click` 在动作/Outbox/幂等登记同一事务中登记点击与展示票据；并发只有一个请求打开菜单。
- 已完成点击从绑定展示的原响应事件重建首次结果；重连、其他进程实例、之后发生的新更正都不改变原回执，不重复通知、不覆盖首次测量。
- 进行中重试不替换票据；到期、被新点击替代、撤销分别处理。明确的新 UUID 可重开菜单。
- 新增 `host_clicks` 运行时索引。前向升级、旧无 ID 兼容、旧显式 ID 无法恢复绑定、停流回滚规则见 [点击契约](q21-click-contract.md)。不改写事件与历史失败数据。

## 本轮执行证据

| 检查 | 开发方结果与边界 |
| --- | --- |
| Ruff check / format --check | 全部通过；`src tests integrations qa` |
| 原样 QA 扩展验收 | 19/19，无修改断言、跳过或 xfail |
| 新增生命周期回归 | 14/14；跨实例、同/跨会话、全部显式操作、并发、通知失败、旧库升级、撤销/替代中的旧回调、权限与事务回滚 |
| 回环 demo HTTP | 35/35；真实 TCP，计时输入受控；不是浏览器计时结论 |
| 实际 Open WebUI 原生回归 | 53/53；固定上游 `8bd8b4fac5e059578ac0c74b3c18d11139f88b7d`，实际应用两份补丁 |
| 开发浏览器＋宿主 HTTP | 全新 8109 实例、两个虚构用户；浏览器两次明确点击，第二次在页面重载后的新 socket 会话完成更正。首次完成后以 CLI 对实际 Action 路由重发原点击，再在新会话且新菜单尚未完成时重发旧点击：均 HTTP 200、与首次回执完全相同、所有 Whynote 表不变。界面未多开菜单，新菜单仍能完成。不是浏览器自动网络重试或独立 QA 结论 |
| 数据库与旧代码重放 | 1 动作、1 Gate Outbox、2 host_clicks、5 事件（动作→展示→选择→展示→更正）。以 main `f560c4a` 的旧 EventStore 实际读取新数据库，事件和投影相同 |

本轮复用已构建且未改动的固定前端，验证部署 Action 内容逐字一致；没有重新构建前端。浏览器截图位于 `var/q21-browser.png`，原始数据库、运行日志、私有测试配置保留在忽略目录；仅将去除凭据和请求原文的摘要提交到 `qa/evidence/2026-09-27-q21-fix/`。

复现命令（隔离环境中执行）：

```powershell
$env:PYTHONPATH='src'
python -m pytest tests qa/test_s0_regressions.py qa/test_manual_v1_acceptance.py -q
ruff check src tests integrations qa
ruff format --check src tests integrations qa
python qa/host_click_retry_probe.py --base-url http://127.0.0.1:8109 --body <私有虚构请求文件> --expected <原回执文件> --db <虚构数据库> --output <新摘要文件>
```

## 验收与交付门槛

本修复不将 Q-21 标为独立关闭，不把开发浏览器复测替代独立 UI。独立 QA 须复验交付 head；非作者 APPROVED、用户作为已承担责任角色对本轮结果签署均待完成。完整 FR、原生评分复用、真实数据、模型、auto-attach、自由文本 SLM、训练导出与生产继续 NO-GO。

同一 PR 等待评审与合并门槛，不删除当前未交付分支。QA 工作树及实现工作树的旧审计数据保留供独立复验；原目录改动保持原样。合并后的 main 核验及符合条件的引用清理由交付执行人待门槛满足后进行；本次不批量处置积压 PR/分支。
