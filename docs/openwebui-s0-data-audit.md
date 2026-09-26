# Open WebUI S0 虚构数据审计

状态：**原生评分复用 NO-GO；独立知因入口联调待验收**。2026-09-26 实测。对应 PRD 修订 68 的 D-11～D-13、技术文档修订 10，以及质量基线 Q-05、Q-09、Q-10。本记录是隔离测试实例的开发证据，不是生产数据流、隐私或完整 FR 的签署。

## 固定范围

- Open WebUI `v0.11.4`，发布标签提交 `8bd8b4fac5e059578ac0c74b3c18d11139f88b7d`，Windows 本机 `uvx --python 3.11 --from open-webui==0.11.4` 运行；`GET /api/version` 返回 `0.11.4`。仅监听 `127.0.0.1:8088`，独立 `DATA_DIR` 为未跟踪的 `var/openwebui-s0-v0114`。没有容器或真实模型。
- 输入只用[自编中文虚构哨兵](../fixtures/openwebui-s0-v1.json)。通过 API 建立固定问答聊天，用浏览器点击原生点踩并提交内置原因；另用 API 重放同等虚构评分以检查外发代理。没有导入公开数据、生产对话或个人资料。
- `OFFLINE_MODE=true`、`HF_HUB_OFFLINE=1`、`ENABLE_VERSION_UPDATE_CHECK=false`、`ENABLE_OLLAMA_API=false`、`ENABLE_OPENAI_API=false`。首次启动的 `CORS_ALLOW_ORIGIN` 为默认 `*`；后续设为 `http://127.0.0.1:8088`。第二阶段再设置 `ENABLE_ADMIN_EXPORT=false` 与 `ENABLE_ADMIN_CHAT_ACCESS=false` 并重启。独立管理员和两个普通测试用户均为 `.invalid` 邮箱；密钥、令牌、数据库、日志和导出文件仅保存在忽略的本地测试目录。
- [官方评分说明](https://docs.openwebui.com/features/administration/evaluation/)明确评分会捕获聊天快照。[环境变量说明](https://docs.openwebui.com/reference/env-configuration/)描述管理员导出和他人聊天入口、单实例管理员权限以及离线模式边界。在线配置文档读取时标为适配 `v0.11.1`；以下结论以实际 `v0.11.4` 行为为准。

## 数据位置与请求行为

| 步骤 | `v0.11.4` 实测 |
| --- | --- |
| 创建聊天，尚未评分 | SQLite 的 `chat.chat` 与 `chat_message.content` 已包含问答哨兵；`feedback` 为 0 行。Open WebUI 自身会保存聊天内容，独立知因入口无法消除宿主的这份存储。 |
| 浏览器点击原生点踩 | 在原因弹窗按“保存”之前已向本机 `/api/v1/evaluations/feedback` 发送请求；`feedback` 新增 `rating=-1`，`snapshot.chat` 包含完整问题与回答，`meta` 包含 `chat_id`、`message_id`。随后保存内置原因会更新该评分，快照仍保留完整文本。内置原因不能视作知因的用户确认或独立 gold。 |
| 默认管理员入口 | 管理员可经 `/api/v1/chats/{id}`、`/api/v1/chats/list/user/{user_id}`、`/api/v1/chats/all/db`、`/api/v1/evaluations/feedbacks/all/export`、`/api/v1/utils/db/download` 取得相应聊天或评分；导出内容中有哨兵。普通用户只能读取自己的聊天；另一普通用户读取该聊天返回 401。 |
| 关闭两项管理员开关并重启 | 管理员读取其他用户聊天、用户聊天列表、聊天导出及 SQLite 下载均返回 401；但 `/api/v1/evaluations/feedbacks/all/export` **仍返回 200 和完整评分快照**，`/api/v1/evaluations/feedbacks/all` 也返回 200。两项开关不能覆盖评分导出面。 |

上述路径与返回码是该固定版本的观测，不承诺其他版本一致。`/api/v1/chats/all/db` 实际返回 JSON；文件名中的 `db` 不表示 SQLite。宿主管理员本身具有数据库级权限，不能把单实例的管理员开关当作强租户隔离。

## 备份、删除与残留

1. 在 SQLite WAL 尚有未检查点写入时，管理员 `/api/v1/utils/db/download` 下载的文件有效，但其中 `chat=0`、`chat_message=0`、`feedback=0`；同一时刻使用 SQLite `Connection.backup()` 得到 `1/2/1` 行。执行 `PRAGMA wal_checkpoint(PASSIVE)` 返回 `(0,85,85)` 后再次下载，才得到 `1/2/1` 行。**不得把未经验证的运行中单文件下载当完整备份**。
2. 普通用户 `DELETE /api/v1/chats/{id}` 返回 200 后，`chat` 和 `chat_message` 逻辑行归零，`feedback` 仍有 1 行，`snapshot.chat` 仍含完整问答。管理员删除该用户后，评分和快照仍在；即使两项管理员开关关闭，评分导出仍能返回该快照。
3. 管理员再 `DELETE /api/v1/evaluations/feedback/{id}` 后，三张表的逻辑行均为 0。已生成的备份和导出仍含完整哨兵。实例的 `journal_mode=wal`、`secure_delete=0`、`auto_vacuum=0`；停止服务后直接扫描 `webui.db` 与 `webui.db-wal` 的字节，仍能找到哨兵标记。逻辑删除不能作为底层擦除或历史副本清除的证据。
4. 第二轮外发检查又创建并删除一组相同类型的虚构聊天与评分；它不改变上述保留和删除结论。测试目录保留作本机复核，**不得提交、分享或作为生产备份使用**。

## 日志与外部请求的证据边界

- 第二阶段服务端日志检索未找到两段哨兵；这仅覆盖本次配置和请求。浏览器网络检查在聊天展示、原生评分和原因保存路径中只看到本机请求，包括 `/api/v1/tasks/tags/completions`，未观察到外部主机请求。
- 第三轮以本机 HTTP(S) 代理拦截器启动同一实例，成功创建虚构聊天与评分；代理没有记录外发尝试。当时 WebUI 进程的 TCP 快照只显示本机连接。**这不是对直连 socket、未执行功能或未来配置的全面断网证明。**官方说明 `OFFLINE_MODE` 不会禁用外部 LLM API；进入真实数据前须用环境级出站限制和完整流量审计复测。
- 评分请求会携带完整聊天快照到 Open WebUI 自身。独立知因入口的验收应检查**知因进程、事件、Outbox、日志与外发**没有原始问答，而不是把宿主已有聊天存储误判为知因新增复制。

## 下一步验收边界

- **存储验收**：保持 Open WebUI 仅作隔离测试宿主；默认关闭原生评分入口，使用独立知因反馈入口。实际安装版本须重新核验管理员评分导出、WAL 一致备份、聊天/评分/用户删除和副本生命周期；先制定测试数据清理办法。原生点踩复用维持 NO-GO。
- **联调验收**：用宿主已验证身份逐次读取当前聊天和消息版本，复核当前对象权限；跨用户、撤权、删除或换版时拒绝。知因只保存不透明对象引用和操作元数据。展示票据绑定身份、对象版本、展示内容与短有效期；浏览器回执仅表示客户端报告了渲染尝试。Q-09 历史回执重试状态和当前可操作性、Q-10 跨用户与跨会话隔离由独立测试验证；不得把测试身份等同于生产身份。
- **数据分阶段**：下一阶段仍只用自编虚构中文案例。HelpSteer2、UltraFeedback 仅作许可、敏感内容和个人信息审查后的候选；其评分不转为用户点踩原因或知因 gold。COIG-P 许可未确认，不导入。

本次没有 Whynote 与 Open WebUI 的真实身份/对象权限接口集成；没有证明 Whynote 事件链路、生产出站控制或删除策略已验收。完整验收仍需责任人复现、测试和签署。
