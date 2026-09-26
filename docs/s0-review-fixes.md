# S0 评审修复契约与复验

2026-09-26；基线 main `86f21b7`，实时读取 PRD 修订 68、技术文档修订 12。对应 D-11～D-13、FR-01/11/13、TD-04/06、Q-11～Q-15。仅允许自编虚构数据，原生评分复用及生产仍为 NO-GO。

后续状态（PRD 80、技术文档 20、main `63a0c54`）：Q-11～Q-15 仅在原指定范围局部通过。Q-16/H-02 已复现跨主体伪关联清理，下面 Q-14 的原实现不能作为可信关联契约或完整删除验收。新契约及签署事项见 [Q-16 候选](q16-association-contract-proposal.md)。

## 本次行为契约

- Q-11：截止时间为签发墙钟时间加 60 秒，保留小数秒，签名和校验使用同一数值。墙钟到达截止时间或单调时钟经过 60 秒即过期；等待回调另有 60 秒超时。票据作为不透明字符串原样回传，不由客户端拆分。旧票据不会跨调用被接受。
- Q-12/Q-13：`ENABLE_ADMIN_EXPORT=false` 时，评分 GET/POST 均只按当前用户查找或修改；普通用户和管理员本人都可访问，非所有者均返回 404 且不修改记录。导出、列表和 ID 列表仍返回 401。开关开启时保留上游管理员访问语义。DELETE 的管理员管理权限未在本切片重定义。
- Q-14 原实现记录（存在 Q-16 缺陷）：单聊天、全部聊天、文件夹删除依据客户端 `meta.chat_id`，未验证评分作者与目标归属。无 chat_id、其他聊天的评分保留；账号删除另清理该账户评分。这描述原实现，不授予跨主体清理权限。H-02 要求可信关联及获准范围；伪关联/未知旧记录的规则须先签署再修复。
- Q-15：同一 action 最新签发的菜单替代旧菜单，即使新菜单随后取消、断线或超时，旧菜单也不会恢复。签发本身不代表展示，不追加展示事件。以 SQLite `display_tickets(event_id, display_id)` 保存每个动作最新签发 ID；无问答、签名或密钥。签发、展示写入及原因写入各自用写事务串行化；展示/原因写入在同一事务内检查最新 ID，覆盖多 Action 实例。历史展示的同内容重试仍返回历史回执、`actionable=false`；既有原因幂等重试仍不追加事件。

## 兼容与回滚

新增表通过 `CREATE TABLE IF NOT EXISTS` 前向初始化，不改已有事件、投影版本或 Outbox。未签发票据的原有 API 动作继续使用原展示契约。历史重放不依赖票据表；恢复服务时必须保留该表，不能用事件重放伪造待处理菜单。回滚旧 Action/store 会重新暴露 Q-11/Q-15，应先停用 S0 Action 并终止在途请求；不能将旧版本与新版并行运行。

外部补丁仍固定 Open WebUI `8bd8b4fac5e059578ac0c74b3c18d11139f88b7d`，重新从干净检出应用。新增账号清理不会恢复已被旧补丁误删的评分。回滚原生补丁前停用评分测试入口，保留审计副本；不自动处理旧快照、备份或导出。

## 验证记录

本地执行日期：2026-09-26。原工作目录的未提交内容和旧审计副本未修改；使用新隔离工作树、新虚构数据库与新测试身份。

| 层次 | 本轮结果与边界 |
| --- | --- |
| 修复前复现 | 在独立副本加载 main `86f21b7` 的 Action/store：59.5 秒仍返回 `ticket_expired`；八组旧回调用例均未按期拒绝。固定上游应用旧补丁后，新原生套件 18 通过/5 失败，复现 Q-12/Q-13/Q-14。管理员跨用户 GET 的一个失败属于预期状态码由旧 401 改为 404，不误记为额外泄露。 |
| 核心及 Action | `uv sync --extra dev --locked --no-editable`；Ruff lint/format 对 src、tests、integrations 通过；pytest **64/64**，git diff --check 通过。覆盖小数秒、到期时刻、双时钟偏移、实际 asyncio 等待超时、同/异 Action 实例并发、签发后取消/断线/挂起、展示与选因之间替换、历史重试、撤销、所有者隔离、旧库增表及事件重放。 |
| 原生补丁 | 使用 Python 3.11 与锁定 CPU 依赖，在固定源码上执行 [独立套件](../integrations/openwebui/tests/README.md) **24/24**。GET/POST 完整角色/所有者/开关矩阵、创建/更新快照拒收、导出拒绝、单条/全量/文件夹/账号删除、不同作者关联评分、无关联及其他聊天评分、空聊天集、真实 ChatMessage 清理均通过。依赖与上游迁移存在弃用警告，未跳过失败。 |
| 独立 HTTP 探针 | 复用 [PR #13](https://github.com/262412/-Whynote/pull/13) 的 `qa/http_regressions.py`，仅将端口移到空闲 8093，参数 `--admin-other-status 404`。启动脚本另指定新的 STATIC_DIR 和 slim 配置；测试断言未改。新建宿主真实 signup/add/signin 后 **18/18**：未登录 401、所有者 200、非所有者 GET/POST 404、拒绝零修改、空聊天集批量删除保留评分。未操作另一测试任务的 8092 实例。 |
| NameError 疑点 | 固定源码完整函数中的 ChatMessage 删除使用内联 select，账号删除及非空 ChatMessage 清理实际成功；保持为已排除，未添加兼容变量。 |
| 前端与宿主版本 | Node **22.23.3**，锁文件 npm ci 后用 `node --max-old-space-size=8192 node_modules/vite/bin/vite.js build` 成功。宿主 `PYTHONPATH` 指向本轮 backend 和 Whynote src，`FRONTEND_BUILD_DIR` 指向本轮 build；`GET /api/version=0.11.4`。Functions API 读回的 Action/Pipe 源码与本分支逐字一致，均非全局。 |
| 真实浏览器 | 新普通用户登录本机 8089，固定 Pipe 返回虚构 fixture。菜单三个 value 均为签名值，截止时间含小数秒。首次选择事实错误、更正为内容不相关，两个 Action POST 均 200；事件依次为动作→展示→选择→展示→更正。取消第三次菜单只追加展示：最终 **1 action、1 Outbox、6 events**，原因仍为更正值。原生按钮测试前宿主 feedback=0；随后原生点踩/保存原因均 200，请求均无 snapshot/问答哨兵，宿主 feedback=1、snapshot JSON null，未增加知因事件。 |
| 残留边界 | 本轮 Whynote SQLite 和宿主 stdout/stderr 未检出问答哨兵。原生套件验证活动 SQLite secure_delete=1、journal_mode=delete；删除后本轮评分哨兵未检出，删除前备份和导出仍检出。旧快照、旧副本及设备级擦除未处理；caplog/单轮日志扫描不代表全环境日志及出站审计。 |

补丁 SHA-256：`674699c7cb03bc23abcb734b672d0bc32ac334e22f6bb4f397dd4b05f3fb30ff`。本轮前端 build/index.html SHA-256：`c0de254db70d58c2d05693c86282603a0769a1dd9641a4057c69e99d4ac117b0`。

本地详细输出在该工作树忽略目录 `var/`：`baseline-action.log`、`baseline-native.log`、`native-final.log`、`browser-results.json`、`frontend-build.log`、`independent-http/data/http-results.json`。原生回归已加入独立 CI job，不能再以仅运行核心 pytest 代替补丁回归。

浏览器并发/撤权跨服务竞态仍未作完整端到端验收；并发拒绝证据来自实际 Action+SQLite 的可控回调测试。非作者评审、数据/隐私及产品责任人签署均待补齐。下一切片须先复验本 PR 并补准入签署，随后按技术计划推进获批快照/预算/Outbox 消费；本轮没有接入模型、公开数据或生产上下文。
