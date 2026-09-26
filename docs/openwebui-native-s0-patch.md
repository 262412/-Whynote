# Open WebUI 0.11.4 原生评分 S0 隔离补丁

状态：**测试侧复核未通过：Q-12 管理员更新接口可取回他人评分、Q-13 管理员本人明细被拒、Q-14 批量删聊天误删无关联评分，待修复复验**。2026-09-26，复测基线为 main `0844d3e`；详见[PR #10/#11 测试报告](qa-pr10-11.md)。下文保留本机既有路径的证据及目标契约，不作为完整通过结论。对应 PRD 修订 68 的 D-11～D-13、技术文档设计修订 10、Q-05。原生评分复用仍为 NO-GO；原生评分及内置原因不是知因事件来源或独立 gold。

## 固定构建与数据边界

补丁位于 [`integrations/openwebui/patches/native-v0.11.4-s0.patch`](../integrations/openwebui/patches/native-v0.11.4-s0.patch)，只适用于 Open WebUI `v0.11.4` 标签提交 `8bd8b4fac5e059578ac0c74b3c18d11139f88b7d`。在**全新、专用**的测试实例上应用；不得直接覆盖[原版审计实例](openwebui-s0-data-audit.md)的数据库或导出文件。固定版源代码和补丁分别保留，便于逆向应用与复核。升级版本须重新审计和移植。

补丁只处理本轮复现的原生评分数据路径：

1. 浏览器评分请求不再附带 `snapshot.chat`，也不在评分后自动调用标签生成接口；删除评分处理中的原始对象控制台日志。宿主聊天 API 仍保存和传输问答，这属于 Open WebUI 对话存储，不能当作知因数据最小化证据。
2. 服务端对任何带非空 `snapshot` 的评分创建或更新请求返回 422，防止未更新的客户端继续写入快照。评分 `data.comment` 等字段仍可能含自由文本，因此此实例只允许虚构输入，不能据此接真实用户数据。
3. 当 `ENABLE_ADMIN_EXPORT=false` 时，管理员评分导出、列表、ID 列表及跨用户评分明细返回 401。评分所有者的明细仍可访问；宿主管理员和数据库操作者仍控制实例，开关不是物理隔离。
4. 删除聊天、用户全部聊天或文件夹聊天时，同一数据库事务删除以 `meta.chat_id` 关联的评分；删除用户时还删除该用户全部评分。SQLite 连接启用 `secure_delete=ON`；本轮实例另设 `DATABASE_ENABLE_SQLITE_WAL=false`。这改善活动库的可观察残留，不擦除已生成的备份、导出、文件系统或设备层历史副本。

## 复现方法

在一个干净的 Open WebUI 源码检出中固定提交，先验证补丁，再应用：

```powershell
$upstream = Join-Path (Resolve-Path .) 'var/openwebui-v0114-source'
$patchPath = (Resolve-Path 'integrations/openwebui/patches/native-v0.11.4-s0.patch').Path
git clone --branch v0.11.4 https://github.com/open-webui/open-webui.git $upstream
git -C $upstream rev-parse HEAD
git -C $upstream apply --check $patchPath
git -C $upstream apply $patchPath
```

在知因仓库根目录执行上述命令；`var/` 是忽略的本机测试目录。`rev-parse` 必须与上文完整 SHA 一致。构建前使用 Node 22；本轮使用 `npm ci --ignore-scripts --no-audit --no-fund` 安装锁文件依赖，再运行 `node node_modules/vite/bin/vite.js build`。运行时通过 `PYTHONPATH=<补丁源码>/backend` 和 `FRONTEND_BUILD_DIR=<补丁源码>/build` 指向源码与产物，用固定 `open-webui==0.11.4` 的 Python 环境启动 `open-webui serve --host 127.0.0.1 --port 8089`。

隔离实例必须用新的 `DATA_DIR` 与稳定随机 `WEBUI_SECRET_KEY`，只绑定本机，并设置 `OFFLINE_MODE=true`、`HF_HUB_OFFLINE=1`、`ENABLE_OLLAMA_API=false`、`ENABLE_OPENAI_API=false`、`ENABLE_VERSION_UPDATE_CHECK=false`、`ENABLE_ADMIN_EXPORT=false`、`ENABLE_ADMIN_CHAT_ACCESS=false`、`ENABLE_PERSISTENT_CONFIG=false`、`DATABASE_ENABLE_SQLITE_WAL=false`。为了测试原生评分，本轮仅在该实例设置 `USER_PERMISSIONS_CHAT_RATE_RESPONSE=true`；**独立知因入口测试实例仍关闭它**。`CORS_ALLOW_ORIGIN` 仅放行本机实例地址。配置开关不能代替环境级出站限制。

## 实测结果与剩余边界

本轮以 `.invalid` 测试身份、自编中文虚构聊天和本机浏览器完成以下复核；未导入公开数据、生产对话或真实模型：

| 检查 | 补丁实例观测 |
| --- | --- |
| 原生点踩与保存内置原因 | 浏览器实际发送的两次 `/api/v1/evaluations/feedback` 请求均不含 `snapshot` 或问题哨兵。宿主 `/api/v1/chats/{id}` 更新仍含对话内容。SQLite `feedback.snapshot` 的 JSON 类型为 `null`，评分与内置原因保留。 |
| 未更新客户端 | 直接提交非空评分快照返回 422。 |
| 管理读取 | 禁止管理员导出时，评分导出、列表、ID 列表、跨用户明细均返回 401；其他普通用户读该评分返回 404；评分所有者读明细返回 200 且 `snapshot=null`。 |
| 删除单条聊天 | 删除前的一致性 SQLite 备份含 2 条聊天和 1 条评分。逐条删除后活动库 `chat=0`、`feedback=0`；活动 `webui.db` 字节中未找到本轮问答哨兵，但删除前备份仍找到。 |
| 删除用户 | 另建一组虚构聊天和评分，管理员删除普通测试用户返回 200；活动库由 `chat=1, feedback=1` 变为两者及该用户均为 0，活动 DB 字节中未找到该组哨兵。 |

这些结果只证明本轮新建隔离实例的指定路径。未验证文件夹删除、所有扩展/插件的数据流、日志与全部外发目的地，也未证明旧数据迁移或物理擦除。旧版产生的评分快照、备份、导出与底层残留仍按[原版审计](openwebui-s0-data-audit.md)保持 NO-GO；需要单独制定副本清点、留存期限、删除和复验流程。任何真实数据接入、原生点踩复用或产品语义调整，仍须完成数据/隐私责任人签署、环境级网络审计及完整生命周期复测。
