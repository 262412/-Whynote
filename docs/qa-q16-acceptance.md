# Q-16 独立 QA 复验（2026-09-26）

**结论：Q-11～Q-16 在以下虚构 SQLite 范围内通过独立复验；没有新复现的业务失败。Q-16/H-02 的技术复验条件已满足，完整关闭仍待受影响浏览器独立复验、非作者批准及本轮测试结果签署。完整 FR、原生评分复用、真实数据和生产继续 NO-GO。**

## 基线与证据来源

- 实际测试 main：`57f09ceef114609770807fb6470ae4462da3e241`，包含 PR #16 修复 `22f6de30f979bb16ed48a84b98e15029e3a5a19e`。二者文件树无差异。PR #16 已于 2026-09-26 13:40:40 UTC 合并，本 QA 未执行合并。PR #14/#15 也已合并。
- 开工读取飞书 PRD **80**、技术文档 **20**；D-12/D-13/D-15、TD-01/04/06、FR-01/11/13/14、Q-05/11～16、H-02 对照本轮范围。最新 H-02 要求先冻结可信关联、历史处置、精确拒绝码及事务规则；仓库[已签署契约](q16-association-contract-proposal.md)记录用户确认。本轮承认该规则签署，不把它扩大为测试结果或完整 FR 签署。
- 固定 Open WebUI v0.11.4，上游 `8bd8b4fac5e059578ac0c74b3c18d11139f88b7d`；在新的 `var/qa-q16-upstream` 检出后应用补丁，正向和逆向检查均通过。补丁 SHA256：`f2eedf75e3be42786265c742be7f9701b5717311b9ed34efaee29f5d6881d8e8`。
- QA 工作树 `whynote-qa-retest/jev项目` 原状态干净、旧 PR 已合并，未发现使用该目录的测试进程后，从最新 main 创建 `codex/qa-q16-acceptance`。原目录未提交改动及历史审计库未改动。
- 本轮由 QA 自行执行，使用每轮全新的虚构 SQLite。原生环境复用锁定依赖解释器，业务模块从 QA 新检出导入，未复用开发数据库或登录凭据。原生路由套件仅替换身份依赖及通知；HTTP 套件使用真实 signup/add/signin、路由和 ORM。
- 环境：Python 3.11、Open WebUI 0.11.4、pytest 9.1.1、FastAPI 0.136.3、SQLAlchemy 2.0.50、httpx 0.28.1、Pydantic 2.13.4。Whynote 执行 `uv sync --extra dev --locked --no-editable`。

## 本轮执行结果

| 检查 | 独立结果及边界 |
| --- | --- |
| Ruff lint / format | `src tests integrations qa` 全部通过，21 个文件格式通过 |
| 核心 pytest | 64/64；1 条既有依赖弃用警告 |
| 独立票据与并发探针 | 9/9；实际 Action、EventStore 和重开重放；宿主归属查询及菜单回调为受控替身 |
| 实际固定补丁原生套件 | 原有 46/46，再加入 7 项 QA 后整套 **53/53**；8 条依赖/迁移警告，无 skip/xfail |
| 独立原生探针 | **58/58**；明确按新签署契约拒绝新伪关联，并保留直接注入的历史伪关联验证 |
| 全新实例真实登录 HTTP | **47/47**；有效配置关闭管理员导出/聊天访问，HTTP 后 SQLite 回读 |
| main CI | [36245964667](https://github.com/262412/-Whynote/actions/runs/36245964667) 两项成功；确实应用固定补丁。PR #16 CI 36242827324 两项也通过 |
| QA 独立浏览器 | 未执行成功：浏览器环境枚举报 `nodeRepl.fetch request failed`，未取得可操作浏览器。没有截图、UI 点击或前端构建的本轮 QA 通过结论 |

上述计数代表不同套件，覆盖有交叉，不合计为独立验收样本量。摘要与逐项结果见 [QA 证据](../qa/evidence/2026-09-26-q16-independent/summary.json)。完整虚构数据库、日志、Feishu 读写快照保留在忽略目录 `var/qa-q16-independent/`，不提交认证信息或原始数据库。

## Q 编号复验记录

| 子项 | 操作与预期 | 实际及副作用证据 |
| --- | --- | --- |
| √ Q-11／P2 | 在墙钟 1000.9 签发，59.5 和 59.999 秒回签应有效，60.001 秒应过期 | 有效时 `reason_submitted`，事件动作→展示→选因；过期 `ticket_expired`，只保留动作事件。重新打开 EventStore，事件及投影一致 |
| √ Q-12／P1 | 导出关闭时普通/管理员本人 GET/POST 200，其他人 404；打开时仅管理员可跨作者操作 | 实际矩阵与精确状态码一致，拒绝响应无评分哨兵、数据库整行不变；真实登录 HTTP 覆盖关闭状态，原生路由覆盖开关两状态 |
| √ Q-13／P2 | 管理员自己的评分保留所有者读取及更新权限 | GET/POST 200，开关关闭时仍通过；跨主体拒绝不扩大为本人拒绝 |
| √ Q-14／P1 | 普通删除仅清理目标聊天可信关联，独立评分、其他聊天及其他作者保留；账号删除按作者清理全部评分 | 单条、全部、空集合、文件夹、账号及重试均按范围对账。HTTP 删除全部聊天后独立评分仍在；账号删除后本人的独立评分、可信评分、聊天及 Auth 均消失 |
| √ Q-15／P2 | A 挂起后签发 B，B 取消或提交后延迟返回 A，同实例与不同实例均应拒绝 A | 四种组合通过，旧 A 不再新增展示/原因，前后事件相等，重开投影一致；B 取消不恢复 A |
| √ Q-16／P1 新写入 | 仅接受认证作者自己的现存 chat/message；外人/不存在/已删 404，非法结构 422，不可换绑/清空/首次补绑 409 | 普通与管理员、导出开关两状态通过；客户端伪造作者及验证字段不能覆盖服务端；新增与更新拒绝前后评分表不变。额外检查 200 字符合法但不存在→404、201 字符→422 |
| √ Q-16 历史记录 | 明确注入历史未知/伪关联，删 Alice 聊天不得删除 Bob 评分；旧记录不自动可信 | 历史评分保留，产生 `legacy_target_deleted` 或冲突审核任务。新伪关联请求 404 与历史保留分开验证；旧记录内容更新不补写 association_version |
| √ Q-16 事务 | 创建/更新与删除/撤权交错，拒绝无副作用；删除中途失败应整体回滚 | SQLite 六组受控交错通过；单条/批量/文件夹/账号删除失败和重试通过；commit 故障及 Auth 最后一步故障回滚。QA 额外验证共享副本也随账号凭据故障恢复，重试后清除 |
| √ Q-16 迁移 | 旧字段不变，旧记录默认未验证，有审核任务；降级再升级不能追认为可信 | 实际 Alembic revision 的升级→降级→再升级通过，旧评分字段不变，验证列为空，审核原因 `legacy_unverified`。整套新库启动也执行完整迁移链；未迁移任何原审计库 |

事件证据见摘要中的 `ticket_events`；数据库/HTTP 对账见 `native_probe`、`http_probe`；原生测试名与结果见 `suites`。历史 55/56 失败保留在[上一轮报告](qa-q11-q15-retest.md)与 `qa/evidence/2026-09-26-q16-contract/baseline-summary.json`，没有把旧失败覆盖成新通过。

## 自动评审意见核实

PR #16 的自动意见称直接删除 SharedChat 绕过了上游 AccessGrant 清理。固定 SHA 下 `SharedChats.create/delete_by_chat_id/delete_all_by_user_id` 不创建或清理 AccessGrant，`Chats.delete_shared_chats_by_user_id` 同样没有所述逻辑。QA 用实际 `insert_shared_chat_by_chat_id` 建立两个用户的共享副本，再分别执行单条、批量、文件夹、账号删除：目标副本清除，对照副本与评分保留，AccessGrant 前后集合不变。四条回归通过，因此该意见在本次固定版本上不成立，不登记为缺陷。未人为注入宿主不会生成的 grant 来构造失败。

这不表示全宿主生命周期通过，也不批准共享聊天评分；跨主体新评分仍应拒绝。上一轮已排除的 ChatMessage NameError 意见不重新登记。

## 评审、签署与剩余条件

- 用户确认覆盖 Q-16 关联与历史处置规则；本轮测试结果尚未由责任人签署。
- 当前 PR #16 GitHub review 仅有 `COMMENTED`，未见非作者 `APPROVED`，main `protected=false`。QA 的独立运行及报告不能冒充另一个人的 GitHub 批准，也不追认既有合并符合规约。
- Q-16/H-02：技术子项可打 √，限定范围完整关闭仍待浏览器与评审/结果签署。下一次以同一修复或后续稳定 SHA，通过可用浏览器在全新虚构实例复验原生评分选择/更正与保存/回读，保留网络和数据库对账；若业务代码变动，重跑受影响矩阵。
- PostgreSQL 并发、多租户完整权限、共享评分授权、全部宿主表/插件/备份副本生命周期、完整 FR、真实数据、原生评分复用、模型、auto-attach、自由文本 SLM、训练导出和生产均未放行。活动库删除不等于备份/导出物理擦除。
- 开发侧浏览器与前端构建证据沿用[交付记录](q16-feedback-ownership.md)，来源标为开发留存；本轮没有独立重跑前端构建。

## 复现入口

在固定上游干净检出应用当前补丁后，指定安装了锁定 `integrations/openwebui/tests/requirements.txt` 的 Python：

```powershell
uv sync --extra dev --locked --no-editable
uv run --no-sync ruff check src tests integrations qa
uv run --no-sync ruff format --check src tests integrations qa
uv run --no-sync pytest -q
uv run --no-sync pytest qa/test_s0_regressions.py -q -s
$env:WHYNOTE_OPENWEBUI_SOURCE = (Resolve-Path var/qa-q16-upstream).Path
& <native-python> -m pytest integrations/openwebui/tests -q
$env:STATIC_DIR = Join-Path $PWD 'var/<新的静态目录>'
$env:USE_SLIM_DOCKER = 'true'
& <native-python> qa/native_regressions.py --source var/qa-q16-upstream --output <新目录> --admin-other-status 404
& <native-python> qa/serve_openwebui.py --source var/qa-q16-upstream --data-dir <新实例目录> --port 8096
# 另一个终端运行；脚本拒绝已有用户的数据库。
uv run --no-sync python qa/http_regressions.py --data-dir <新实例目录> --base-url http://127.0.0.1:8096 --admin-other-status 404
```

## 飞书回写与收尾

- [PRD 修订 81：QA-20260926-R3](https://my.feishu.cn/wiki/XG6SwutL0i3fyWkwmhScEEB86Gg#doxc6swwn0bw1LwsaSoqMQzkKSw)，以准确 revision-id 80 追加。
- [技术文档修订 21：QA-20260926-R3](https://my.feishu.cn/wiki/GuphweJs3iWwBRkBDjlcljA16bh#doxc6TBb5HvCnMqHL7Q1RRA0JtA)，以准确 revision-id 20 追加。
- 两次写入均 success、无 warnings；全文读回验证旧内容完整保留为前缀，新章节及修订准确。旧失败没有删除或改写。
- 本轮 localhost:8096 HTTP 测试服务已停止，确认端口不再监听；虚构库保留。原目录 `git status --short` 与开工相同。
- 本提交只增加 QA 回归、脱敏结果摘要及报告，不改业务补丁，不代签非作者批准或发布验收。
