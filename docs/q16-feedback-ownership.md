# Q-16 / H-02 评分归属与删除修复

日期：2026-09-26。PRD 80、技术文档 20；D-12/D-13、TD-01/04/06、FR-13/14、Q-05/Q-16。基线 main `63a0c54fb4089bcfecd412c00a6bdc2cdcc668f4`，固定 Open WebUI `8bd8b4fac5e059578ac0c74b3c18d11139f88b7d`。交付：[PR #16](https://github.com/262412/-Whynote/pull/16)，不自动合并。

**结论：签署规则已实现，开发侧指定范围通过，等待独立 QA 和非作者评审。** 用户确认“可以采用，所有签署的角色都是我，因为我是暂时唯一的项目人员”。该确认覆盖 [关联契约](q16-association-contract-proposal.md)的后端、数据/隐私和产品决策，不替代尚未发生的测试结果签署。完整 FR、原生评分复用、真实数据和生产继续 NO-GO。本轮未修改飞书文档，修订 80/20 是核对基线。

## 行为与事务

- 新建评分的作者取认证主体。普通用户和管理员均只能关联本人当前聊天及其中的消息，导出开关不改变这个边界。不存在、已删或外人对象返回 404；非法结构（非字符串、空白、超过 200 字符、只有 message_id）返回 422。
- `meta.chat_id/message_id` 绑定后不可更换或清空，独立评分也不可通过更新首次绑定，冲突返回 409。内容更新再次核对已有目标。管理员仅在导出开启时可按原有权限编辑其他作者内容，仍按该作者验证目标，并禁止重绑定。评分 DELETE 权限不变。
- Feedback 新增仅服务端写入的 `association_version`、`verified_chat_id`、`verified_message_id`，不进入输入/输出模型。新记录版本为 1；独立评分引用为空。关联验证只查主体、聊天归属和消息 ID，不读取问答正文。原生消息主键为 `{chat_id}-{message_id}`，API 接收前端的原始 message_id。
- SQLite 中先以无值变化的 UPDATE 获取写锁，再验证并写入，持锁直到提交；创建还锁定存在的用户行。删除与撤权无法穿过这段事务。测试覆盖创建/更新与删除、撤权的交错。**并发结论只适用于本轮 SQLite 宿主，未验收 PostgreSQL。**
- 单条、用户全部、文件夹删除只清理服务端引用与 meta 一致、版本 1 且作者等于实际被删聊天所有者的评分。旧记录和矛盾记录保留，加入 `whynote_feedback_review`。空集合和重复删除不扩大范围。
- 账号删除按作者删除全部评分（包括独立和旧评分）；其他作者的伪关联仍保留。聊天、消息、共享副本、关联评分、审核任务、组成员、用户及 Auth 凭据在该删除路径同事务提交或回滚。其他宿主数据表、插件副本和外部备份仍不在本轮完整生命周期验收范围。
- Whynote 事件/Outbox 契约没有变化；生产上下文、auto-attach、自由文本 SLM 和真实模型仍关闭。

## 迁移、旧数据与回滚

新增 Alembic revision `whynote_s0_v1`，父版本 `d4c1a8e37b62`。启动迁移添加三个 nullable 列及最小审核表。每条已有评分加入 `legacy_unverified` 待审核任务，原评分字段保持不变，验证版本为空。即使作者与现有聊天相符，也不自动升为可信。

旧记录仍按既有评分读取权限可读。内容更新遵守相同的不可重绑定与当前归属校验；成功更新也不会补写验证版本。无法验证或结构矛盾则拒绝修改。历史快照不被此迁移抹除，仍不能用作训练、事实源或生产准入证据。

受控本机审核清单：

```powershell
uv run --no-sync python integrations/openwebui/review_feedback.py --database <隔离实例/webui.db> --output <新的本机报告.json>
```

工具使用 SQLite `mode=ro`，拒绝覆盖已有报告，只输出评分 ID、类别、任务原因及 `pending_review`。类别区分无关联、当前表面匹配但未验证、主体矛盾、消息缺失、目标缺失和未知。不会输出评分正文、问答、作者身份或凭据，也不自动重新验证、升级可信状态或删除旧记录。报告应作为项目人员的本机审核任务处理；任何结案或重新验证流程仍需另行冻结规则，不能把“保留”当作合法长期留存决定。

回滚须先停止隔离实例写入，保留一致性数据库副本和审核报告。在离线副本上执行 Alembic downgrade 到父版本，再逆向应用补丁。downgrade 保留旧评分字段，但丢失验证列和审核表；重新升级后所有当时记录重新标为未验证。测试已执行升级→降级→再升级并核对旧字段不变。**旧代码仍含 Q-16 缺陷，不能据此恢复对外评分/删除服务。** 本轮未回滚、清理或迁移任何已有审计库。

## 验证与证据

| 检查 | 开发侧结果 |
| --- | --- |
| Ruff lint / format，核心 pytest | 通过；64/64 |
| 既有 S0 QA pytest | 9/9 |
| 固定上游真实路由、ORM、迁移与事务 pytest | 46/46；含角色/导出开关、消息归属、拒绝零修改、不可重绑定、旧记录保留、全部删除路径、故障回滚、重试、六组交错及 Auth 最后一步故障 |
| 更新后的 QA 原生探针 | 58/58；保留原拒绝/删除断言，补真实合法目标，将历史伪关联改为明确的旧库注入，并新增伪关联请求 404 与零写入两项 |
| 新实例真实 signup/add/signin、HTTP 与 SQLite 回读 | 47/47；覆盖拒绝码、合法消息关联、内容更新、不可重绑、单聊及账号清理、历史伪关联保留和审核任务 |
| 浏览器 | 固定虚构 Pipe 回答→原生点踩→“与事实不符”→保存→回读。原生创建/更新均 200，网络请求均无 snapshot；活动库验证字段为 1 且归属匹配 |
| 活动库、备份、导出和日志边界 | 既有原生测试仍通过；备份/导出保留哨兵，不能宣称物理擦除 |

原生套件的 8 条依赖弃用/迁移警告、核心的 1 条 Starlette/httpx 弃用警告不作业务通过依据。所有输入为 `.invalid` 身份和虚构中文数据，未调用真实模型。

签署前 `4bf2525` 保存原始探针及 [55/56 失败摘要](../qa/evidence/2026-09-26-q16-contract/baseline-summary.json)。本次对探针的修改是已签署契约的测试更新，**开发者运行不构成独立 QA 复验**，不得把 58/58 重命名为原 56 项独立通过。当前摘要见 [验证摘要](../qa/evidence/2026-09-26-q16-contract/implementation-summary.json)。

前端本轮没有变更；浏览器使用之前已构建的同一 S0 前端，入口 SHA256 `c0de254db70d58c2d05693c86282603a0769a1dd9641a4057c69e99d4ac117b0`，后端从新 `var/q16-upstream/backend` 加载。合成库、完整日志、截图与凭据只保存在忽略的 `var/`，不上传。旧失败库和历史审计副本保留。

复现（先按既有宿主文档在固定上游应用补丁并安装锁定环境）：

```powershell
$env:WHYNOTE_OPENWEBUI_SOURCE = (Resolve-Path var/q16-upstream).Path
& var/native-venv/Scripts/python.exe -m pytest integrations/openwebui/tests -q
$env:STATIC_DIR = Join-Path (Get-Location).Path 'var/q16-probe-static'
$env:USE_SLIM_DOCKER = 'true'
& var/native-venv/Scripts/python.exe qa/native_regressions.py --source var/q16-upstream --output <新的空输出目录> --admin-other-status 404
# 在单独终端启动（数据目录必须不存在），再运行 HTTP 探针：
& var/native-venv/Scripts/python.exe qa/serve_openwebui.py --source var/q16-upstream --data-dir <新的实例数据目录> --port 8094
& .venv/Scripts/python.exe qa/http_regressions.py --data-dir <全新运行实例的数据目录> --base-url http://127.0.0.1:8094 --admin-other-status 404
```

CI 已纳入核心、S0 QA、46 项原生用例和 58 项探针。独立复验、非作者批准及测试结果责任签署完成前，不关闭 Q-16/H-02，也不推进真实数据或模型接入。
