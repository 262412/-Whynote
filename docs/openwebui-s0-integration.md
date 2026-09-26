# Open WebUI S0 独立知因入口

状态：**虚构数据联调切片通过；生产接入与完整 D-13 验收未完成**。2026-09-26 在 Open WebUI `v0.11.4` 本机隔离实例验证。对应 PRD 修订 68 D-11～D-13、技术文档修订 10、Q-05/Q-09/Q-10。宿主的数据留存、原生评分与导出问题另见 [PR #7 的 S0 数据审计](https://github.com/262412/-Whynote/pull/7)。此切片不改变知因的产品定位。

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
3. 宿主向该用户会话打开固定三选一菜单。随机 `display_id` 与挂起的 `__event_call__` 共同构成仅在本次调用内有效的短期展示票据；60 秒超时、客户端断线或非法选择不追加展示事件。客户端回复后**再次**查当前聊天归属和消息版本，才调用 `record_display`；有效选择在 `reason_selected` 或 `reason_edited` 前再复核一次。取消只留下展示事件，不伪造“拒填”或用户确认。该回复最多说明客户端报告了菜单交互，不能证明人实际看见。
4. 服务端和宿主数据库不是同一个事务；复核之后到写入知因 SQLite 之间仍有竞态。这个 S0 会话回调不是跨服务可验证的签名票据。正式宿主接入须定义签名、到期、撤权、换版及跨服务竞态处理，并单独复测。

## 实测证据

- 专用普通用户在真实浏览器中看到固定 Pipe 回答与单独的“知因 S0 点踩”按钮，未看到原生评分按钮。首次点踩和选择“事实错误”生成 `negative_feedback_action_recorded → reason_displayed → reason_selected`；同会话多次更正，每次追加 `reason_displayed → reason_edited`，仍只有一个动作和一个 Gate Outbox。
- 第二个真实普通用户访问该聊天返回 401；直接调用同一 Action 返回 404，知因事件数不变。单测覆盖非归属用户、过期浏览器内容、菜单打开期间撤权/换版、超时票据、选择和更正。
- Open WebUI 的该聊天仍在宿主 `chat` 表，`feedback` 为 0 行；知因 SQLite 只有不透明目标引用、原因码和操作字段，字节扫描没有问题/回答哨兵；本轮服务端日志检索也未发现哨兵。此结论只覆盖固定虚构案例和本机测试配置。
- 联调入口不调用真实推断，不创建快照，不批准生产上下文、自动附加或自由文本 SLM。HelpSteer2/UltraFeedback 仍待审查，COIG-P 未导入。

验收仍缺：非作者与数据/隐私责任人签署、跨服务短期签名票据、实际宿主权限撤销演练、跨服务竞态、完整日志/导出/备份/删除生命周期及环境级外发审计。Q-09/Q-10 的服务端修复在 [PR #6](https://github.com/262412/-Whynote/pull/6)；本切片只增加 S0 宿主联调证据。
