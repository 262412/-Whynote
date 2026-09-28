# M5-2a 展示与确认记录独立 QA

2026-09-28；FR-10/11/13/14/15，TD-04/06/09/10/11，D-13/18，Q-09/10/21/22、S1-3R。

## 结论与基线

所测 mock 台账工程子项通过，未发现新缺陷。原回归592项、新增独立20项、完整612项全部通过；
Ruff lint/format通过（81文件），保留1条既有Starlette/httpx弃用警告。
合成记录的独立对账通过，不代表用户确实看见建议、模型质量、真实采集或完整FR验收。
非作者有效APPROVED、产品/客户端/数据契约及结果签署仍缺，保持待合并/阻塞。

- 固定 PR #38 业务提交 `f20518b9ea2a0ea73f34439be0dc8bc80c253c5c`，分支 `codex/m5-display-contract`。
- main `c2ee7a3463f2b058d4f7560ec8e1a440b3c0564e`；PR #37 已合入。
- 读取飞书 PRD **343** / 技术文档 **342**；核对上述需求编号、原M5-2a任务和渲染/确认条款。
- 业务候选 [CI 36436137037](https://github.com/262412/-Whynote/actions/runs/36436137037) 两项通过。
  已检查三条自动评审与修复源码，确认来源ID、停用后的宿主清理和报告终态原测试均复跑通过。
- Windows / CPython3.11.14；复用干净且无运行任务占用的QA工作树，按锁文件强制重装非editable wheel。
  新用例复用已有虚构Fixture，真实执行TrialStore、SQLite事务、事件投影与只读报告。

## 操作、预期与实际

| 操作 / 预期 | 实际结果与证据 |
| --- | --- |
| 重新运行原 `pytest -q tests qa` | 592/592；包含时间边界、实际锁等待、在途撤销、append-only和三项评审修复用例，未调整原断言 |
| 新增 `tests/test_suggestions_independent.py` 后完整回归 | 新20/20、全套612/612；以下为新增验证范围 |
| render/respond/invalidate各自在真实INSERT后注入提交前异常 | 三条路径整个数据库逻辑快照完全不变；事件、Outbox及其他表均回滚 |
| 两个不同首次确认／两个不同更正，同时使用相同前序CAS | 每组仅一条提交，另一条Conflict；历史前缀不变，响应组/确认组均只计1 |
| 首次yes之后correct，再重发首次yes的原键 | 返回最初record_id，数据库零变化；当前原因及response_id/last_response_id仍指向更正 |
| 已成功render/yes后，分别到期、关开关、换模型、删除、撤权、撤销动作，再重试原键 | 两种重试在六种状态下均拒绝，数据库逻辑快照不变；重试不绕过当前准入 |
| 已确认后生成unknown/no_match，重发旧display及原yes | 旧请求拒绝，原确认保留；新生成计入生成数但不计可展示/响应 |
| 外租户／外操作者调用render/respond/invalidate | 六条路径NotFound，零数据库变化 |
| 按时收到响应，进入事务前暂停；完成停用／删除／新预约后放行 | 三条路径重新核对非时间准入并拒绝，零响应事件；新预约还检查received_at不得早于生成 |
| 确认→更正→动作撤销，按前两个as_of重新查询 | 两个历史报告完全复现；当前确认清空、历史确认/响应分母保留；新建议事件未改变旧manual投影 |

## 独立合成脚本与数据库对账

实际运行 `uv run --no-sync python -X utf8 qa/m52_suggestion_fixture.py --output var/qa-m5-2a-independent/fixture`。
全新数据库和输出目录；脚本未启动服务、浏览器或模型。独立SQL查询和再次执行只读报告的结果：

| 来源组 | 生成 | 可展示 | 渲染报告 | 有效响应组 | 历史确认组 |
| --- | --- | --- | --- | --- | --- |
| self_natural | 1 | 1 | 1 | 1 | 1 |
| scripted | 5 | 5 | 5 | 4 | 0 |
| public_replay | 3 | 1 | 0 | 0 | 0 |
| 合计 | **9** | **7** | **6** | **5** | **1** |

数据库有23条M5-2a事件：generated9、render6、response7、invalidated1。
7条响应中含一次更正和一次close，所以不能作为7个有效响应组。
另有10条原始点踩和1条动作撤销，Outbox总计11；本切片不为建议生成推断Outbox。
public_replay的4条回答对应的操作者角色均为evaluator，原人工填写分子仍为0；没有合成gold。
最终状态包含responded5、unresponded1、abstained2、render_unknown1；历史确认所在动作已撤销。

再次生成报告与首次JSON完全相同，SQLite文件前后字节及SHA相同。
数据库与报告未包含fixture问题、回答正文或虚构version_key。对外只提交元数据事件和报告，
没有提交数据库、配置文件或密钥。该检查只针对本轮虚构样本，不替代真实数据隐私验收。

## 证据、边界与退出条件

- [独立回执](../qa/evidence/2026-09-28-m5-suggestions-independent/summary.json)、
  [23条事件](../qa/evidence/2026-09-28-m5-suggestions-independent/m52-events.json)、
  [只读报告](../qa/evidence/2026-09-28-m5-suggestions-independent/report.json)。
- 本地完整日志/JUnit/数据库/飞书前后快照：QA工作树 `var/qa-m5-2a-independent/`。
  旧开发报告、7项历史失败与上轮证据保留；本轮没有业务修复。
- 本轮仅有受控本地Python入口的mock验证，没有新HTTP/UI路径；未独立重跑原生宿主、浏览器或真实模型。
  原生绿色属于远端CI证据。未开启真实采集或M5-2b。
- 下一次复验：若修改事件语义或准入，重跑CAS、原子回滚、时限、历史as_of及来源对账；
  M5-2b须另测真实渲染/模板引用/显式操作端到端，并补齐采集用途/保留/删除契约和M5-1质量门槛。
- 按开发规约§3.2，仍需非作者对最新提交有效批准，以及产品/客户端/数据责任人契约与结果签署。
  不自行代签或追认完整FR。原目录未提交改动、运行服务与审计数据保留；PR未合并前保留分支/工作树。

飞书回填：按每次最新revision-id局部更新15处原需求/契约/任务条目并逐次读回，PRD **350**、技术文档 **350**。
历史归档正文及原链接集合保持不变；前后快照与逐条写入回执留在本轮本地证据目录。
