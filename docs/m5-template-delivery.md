# M5-2b 合成模板交互交付

基线：main `3f4c9e898f82ece7bf8f428c99ade050b334b8b7`；M5-2a PR #38 已合并，用户验收。
输入：飞书 PRD350 / 技术350。需求映射和兼容定义见[契约](m5-template-contract.md)。本轮按用户选择只做合成验证，真实试用参数另行确认。

## 已实现

- `local_chain_action.py` 保持单按钮、动作/Outbox 先提交；显式 mock 开关下接模板，真实联调路径保持原行为。
- `template_suggestions.py` 连接冻结理由包、固定问题式模板、直接父问题/目标回答的精确原文范围与摘要。无可靠引用只显示类别，正文不写事件或日志。
- `suggestion_dialog.js` 的 DOM 对话框先生成实际渲染回执，再开放“是这个问题／不是／都不是”，另有跳过、拒填、关闭及常规菜单；确认后可显式更正。零默认确认。
- 独立的渲染、响应回调绑定同一 display/version；失败、拒识和拒绝候选后可回到原 manual-v1 菜单。新类别不塞入旧八类投影。
- 事件和只读报告增加可选引用元数据与响应计时，保留生成/渲染/有效响应分母。修复上游 PR38 后续评审指出的生成重试复活、撤销后遗留当前确认指针。

## 验证及其边界

- 首轮完整本地回归646项通过；随后加入按时回调等待宿主核查及非法计时等7项，针对性133项通过。最终完整回归653项通过（83.23秒），Ruff lint/format通过。业务648f586的CI36444465334两项通过：quality为613+9+31项，native-regression为80项+58项Q16探针。
- 固定 Open WebUI `8bd8b4fac5e059578ac0c74b3c18d11139f88b7d`，真实 ORM 的研究登记、生成保存、单按钮确认、删除失效与历史报告：6项通过。原生完整回归由CI执行。
- Svelte5.53.10 编译新增补丁后的 `Chat.svelte` 成功；有原文件自闭合元素警告。JS语法检查通过。没有执行完整前端生产打包。
- [首轮浏览器报告](../qa/evidence/2026-09-28-m5-templates/browser-initial-report.json)与[事件](../qa/evidence/2026-09-28-m5-templates/browser-initial-events.json)：真实浏览器键盘操作固定虚构代码案例；2生成/2渲染/2有效响应组/1历史确认，yes1/correct1/none_matched1，撤销1；三个响应计时均client_reported。常规回退另有人工原因事件。该轮早于最终按时回调等待修复，不能替代最终代码复验。
- 首次pytest收集缺仓库导入路径、首次Svelte编译命令误用CommonJS具名导入均已修正。FastAPI/宿主的既有弃用警告保留，不降低断言或删失败证据。

最终代码再次运行浏览器确认/更正/完成：1生成/1渲染/1有效响应组/1确认、2个client_reported计时。见[最终报告](../qa/evidence/2026-09-28-m5-templates/browser-final-report.json)、[最终事件](../qa/evidence/2026-09-28-m5-templates/browser-final-events.json)及[源码摘要清单](../qa/evidence/2026-09-28-m5-templates/manifest.json)。浏览器宿主桥使用合成保存记录，真实Open WebUI数据库链路由上述6项另行验证。

## 复现

```powershell
uv sync --extra dev --locked --no-editable --reinstall-package whynote
uv run --no-sync ruff check src tests integrations qa
uv run --no-sync ruff format --check src tests integrations qa
uv run --no-sync pytest tests qa/test_s0_regressions.py qa/test_manual_v1_acceptance.py -q
uv run --no-sync python -X utf8 qa/m5_template_browser.py --data-dir var/m52b-fresh --port 8132
```

浏览器访问 loopback 地址，依次点踩、确认、更正、完成、同按钮撤销；再次点踩并选“都不是”，进入常规原因菜单。`--data-dir` 必须为新目录，报告与原事件保留，不复用真实聊天或现有联调库。

原生补丁顺序：native-v0.11.4-s0 → manual-v0.11.4-timing → s1-v0.11.4-trial → m5-v0.11.4-templates；将 `integrations/openwebui/suggestion_dialog.js` 复制到宿主 `src/lib/whynote/suggestion_dialog.js` 后重新构建。CI核对完整补丁栈和JS文件一致性。本轮未部署到已有真实联调实例。

启用须 `WHYNOTE_TEMPLATE_SYNTHETIC=1`、`WHYNOTE_LOCAL_CHAIN=1`，并同时满足mock研究配置与 `suggestion_template_enabled=true`。fixture 格式见合成脚本。只关闭模板 JSON 开关时拒绝新/在途模板；取消环境选择开关后回到既有 Laya 通知路径。保留全部事件及用户数据。

## 尚待完成

独立QA、非作者有效批准和产品/客户端/数据契约及结果签署；真实模型质量门槛、本人试用样本/时长/接受停止阈值，以及新增真实记录的用途/留存/删除协议。补写率、必要补充次数和误导裁决未采集；不将开发者合成操作计为用户价值或完整FR验收。生产上下文、auto-attach、自由文本SLM、训练导出仍关闭。

## 交付状态

[PR #39](https://github.com/262412/-Whynote/pull/39)已推送，业务648f586；飞书按需求编号、字段和任务原条目局部回填并逐项读回，PRD350→361 / 技术350→361，见[回执](../qa/evidence/2026-09-28-m5-templates/feishu-readback.json)。主目录仅更新当前计划并保留旧计划归档，8个相关非计划文件前后摘要相同；真实运行数据未触碰。

截至本次交付无非作者有效批准、新代码独立QA或适用责任人签署，按开发规约保持待合并。分支和工作树保留用于评审修复，旧分支未清理，未部署或合并。文档回填后的最终head及CI以PR最新检查为准。
