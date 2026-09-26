# Open WebUI S0 独立知因入口

状态：**本分支已修复 Q-11/Q-15，局部自动化及正常浏览器选择/更正通过，待非作者和责任人复验签署**。2026-09-26，基线 main `86f21b7`，实时核对 PRD 修订 68、技术文档修订 12。修复及新证据见 [S0 评审修复](s0-review-fixes.md)；[旧 QA 报告](qa-pr10-11.md)保留修复前复现。对应 D-11～D-13、FR-01/11/13、Q-05/Q-09/Q-10。下文旧浏览器观测不构成完整 D-13 验收。原生数据生命周期见 [S0 数据审计](openwebui-s0-data-audit.md)，生产仍为 NO-GO。

## 运行边界

`integrations/openwebui/s0_pipe.py` 是只对[固定中文虚构问题](../fixtures/openwebui-s0-v1.json)返回固定答案的无模型 Pipe；宿主仍会保存用户输入，因此必须限制实例使用者只能输入虚构数据。`integrations/openwebui/s0_action.py` 是独立的“知因 S0 点踩” Action，**仅绑定这个 Pipe 模型**，不得设为全局 Action。不要使用 Open WebUI 原生点踩作为知因事件来源；在隔离实例关闭 `USER_PERMISSIONS_CHAT_RATE_RESPONSE`。Action 运行在 Open WebUI 进程内，通过其认证后的 `__user__` 和 `Chats.get_chat_by_id_and_user_id` 复核当前聊天归属；仅接受完整匹配的固定虚构问答和当前浏览器上报的消息内容。Open WebUI 管理员仍有该实例的数据和函数代码权限。

启动前为隔离实例单独准备以下环境变量；令牌、随机值和数据库只放忽略的本地测试目录，不放进仓库：

| 变量 | S0 值或规则 |
| --- | --- |
| `DATA_DIR`、`WEBUI_SECRET_KEY` | 单独实例目录与稳定随机密钥；只监听 `127.0.0.1`。 |
| `PYTHONPATH` | 本仓库 `src` 的绝对路径，使宿主 Function 能导入 `whynote`。 |
| `WHYNOTE_S0_FIXTURE`、`WHYNOTE_S0_DB` | fixture 的绝对路径；**单独的知因 SQLite** 路径。 |
| `WHYNOTE_S0_TENANT`、`WHYNOTE_S0_VERSION_KEY` | 该测试实例独有的随机租户标识和至少 32 字节随机版本密钥；重启时保持不变。 |
| `OFFLINE_MODE`、`HF_HUB_OFFLINE`、`ENABLE_OLLAMA_API`、`ENABLE_OPENAI_API` | 分别设为 `true`、`1`、`false`、`false`；这不能替代网络层出站限制。 |
| `ENABLE_ADMIN_EXPORT`、`ENABLE_ADMIN_CHAT_ACCESS`、`USER_PERMISSIONS_CHAT_RATE_RESPONSE` | 均设为 `false`。本轮为确保重启后生效还设 `ENABLE_PERSISTENT_CONFIG=false`；仅适用于这个隔离实例。评分导出残余入口仍按 PR #7 记录。 |
| `WEBSOCKET_EVENT_CALLER_TIMEOUT`、`CORS_ALLOW_ORIGIN` | `60`；`http://127.0.0.1:8088`。Action 自身另有 60 秒超时。 |

用已固定的 `open-webui==0.11.4` 启动后，由管理员在 Functions API 创建并启用两个源码文件；保持 Action 的 `is_global=false`。为 `whynote_s0_pipe` 建立同名 Workspace Model，`meta.actionIds=["whynote_s0_action"]`，只给专用测试用户 `read` 授权。`GET /api/version` 须为 `0.11.4`；普通测试用户的 `GET /api/models?refresh=true` 应只出现固定 Pipe，且它的 actions 只含 `whynote_s0_action`。联调时只输入 fixture 中的问题，不接真实模型或外部数据集。

## 事件与回执契约

1. Action 入口先用宿主认证用户和当前聊天归属核对固定虚构消息，再计算 `object_type=openwebui_assistant_message`、`object_id=<chat UUID>/<message UUID>`、`object_version=s0v1-<HMAC>`。HMAC 输入是当前消息内容、模型、父消息 ID 与时间戳；原文和版本密钥均不进入知因事件。浏览器传来的 ID、内容或会话字符串不是身份凭据。
2. 点击动作使用用户、会话、对象及版本作用域的幂等键调用 `EventStore.create_action`；动作事件与 Gate Outbox 同事务写入，不等待模型。相同会话重试复用该动作。已有用户原因再次打开菜单走 `edit_menu`；动作已撤销时不重新归因。
3. 宿主向该用户会话打开固定三选一菜单。每个选项展示中文标签，返回值为 `s0t1` 签名票据；HMAC 绑定租户、认证用户、浏览器会话、动作、对象版本、展示 ID、菜单模式、标题、说明、选项和 60 秒到期时间。原始问答、密钥和完整票据不写入知因事件。Open WebUI `v0.11.4` 的选择组件支持标签与独立返回值。篡改、旧菜单重放、超时或断线不追加展示事件。客户端回复后**再次**查当前聊天归属和消息版本，才调用 `record_display`；有效选择在 `reason_selected` 或 `reason_edited` 前再复核一次。取消经同一挂起回调返回 `false`，只留下展示事件，不伪造“拒填”或用户确认。该回复最多说明客户端报告了菜单交互，不能证明人实际看见。
4. 截止时间保留小数秒，到达 60 秒即过期；客户端把票据作为不透明字符串原样回传。新菜单签发立即替代同动作旧菜单，最新签发 ID 存于 SQLite，展示和原因事务均复核它。取消、断线或超时不会恢复旧菜单。`reason_displayed` 字段及 `ui_version=openwebui-s0-select-v1` 不变，旧事件重放保留；历史回执的 `actionable` 同时考虑是否被新签发替代。迁移和回滚见 [修复契约](s0-review-fixes.md)。宿主权限复核与知因数据库写入仍不在同一事务；同进程 HMAC 也不是跨服务宿主签名，该竞态仍待正式接入方案。

## 实测证据

- 专用普通用户在真实浏览器中看到固定 Pipe 回答与单独的“知因 S0 点踩”按钮，未看到原生评分按钮。首次点踩和选择“事实错误”生成 `negative_feedback_action_recorded → reason_displayed → reason_selected`；同会话多次更正，每次追加 `reason_displayed → reason_edited`，仍只有一个动作和一个 Gate Outbox。
- 2026-09-26 对本次签名选项再次实测：有效测试用户打开其虚构聊天，点击按钮后浏览器发送 `POST /api/chat/actions/whynote_s0_action` 并收到 200；三个可选原因的 DOM 值均符合 `s0t1.<display_id>.<expires_at>.<reason_code>.<HMAC>`。先选“事实错误”，再打开菜单改为“内容不相关”，事件依次为 `negative_feedback_action_recorded → reason_displayed → reason_selected → reason_displayed → reason_edited`，动作与 Outbox 各 1 条；宿主 `feedback` 为 0 行，知因 SQLite 无问答哨兵。此前对已删除用户的旧聊天打开页面会被宿主重定向，不能用那次未发请求判断按钮链路。
- 第二个真实普通用户访问该聊天返回 401；直接调用同一 Action 返回 404，知因事件数不变。单测覆盖非归属用户、过期浏览器内容、菜单打开期间撤权/换版、超时票据、选择和更正。
- Open WebUI 的该聊天仍在宿主 `chat` 表，`feedback` 为 0 行；知因 SQLite 只有不透明目标引用、原因码和操作字段，字节扫描没有问题/回答哨兵；本轮服务端日志检索也未发现哨兵。此结论只覆盖固定虚构案例和本机测试配置。
- 联调入口不调用真实推断，不创建快照，不批准生产上下文、自动附加或自由文本 SLM。HelpSteer2/UltraFeedback 仍待审查，COIG-P 未导入。

验收仍缺：非作者与数据/隐私责任人签署、跨服务短期签名票据、实际宿主权限撤销演练、跨服务竞态、完整日志/导出/备份/删除生命周期及环境级外发审计。Q-09/Q-10 的服务端修复在 [PR #6](https://github.com/262412/-Whynote/pull/6)；本切片只增加 S0 宿主联调证据。
