# Laya 本机手动测试契约 v1

2026-09-28，FR-06 / TD-07 的独立本地测试切片。用户明确要求下载 Laya Multilingual 并在本机作为 Jev 原因模型测试；本契约记录这一限定范围。基线 main `a0a069c`。

- checkpoint：`convaiinnovations/laya` 的 `multilingual`，固定修订 `55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851`，Apache-2.0。根目录英文 checkpoint 不下载。
- 仅手动输入问题、回答和可选反馈；不从聊天、数据库、Outbox 取上下文。不调用云端，不读取 API Key，不写反馈/研究事件或用户确认。模型下载阶段需要联网，推理阶段强制离线。
- 复用现有 candidate-v1 八类原因和 Choice/Noul 值校验；提供 primary_reason、factual_error_signal、instruction_failure_signal。结果 provider=laya_local，携带 checkpoint 修订、提示版本和 `model_inferred_unconfirmed`。分数未经本项目校准，不是正确率，不自动附加到现有菜单。
- 入口默认关闭；本机启动命令显式启用。服务仅监听 127.0.0.1，拒绝其他 Host、跨源请求及缺少本页会话令牌的请求。浏览器页面不使用外部资源、不持久化正文；访问日志关闭。测试页只把这次手动输入送入本机模型。
- 单并发，忙时 429，无队列/重试；60 秒超时返回 504，仍在执行的 GPU 工作保持占用直到完成。超时结果丢弃。进程退出即释放模型。
- state 上限 8192 UTF-8 字节、700 tokenizer token；拒绝特殊 token 和过长输入，不静默截断。模型总预算 1024 / 问题预算 256，固定短指令与选项。空输入、无效回包、缺失/null 信号及版本失配拒绝。错误消息不回显正文。
- HTTP `/api/reason` 输入 `question`、`answer`、可选 `feedback`；输出复用 Jev 适配器的规范化字段，并明确本地 provider 与未确认来源。Python 入口 `LocalLaya.evaluate_reason(state_factory, enabled=False)`；只在启用后调用延迟上下文函数。此切片不修改 TypeSafe 云 endpoint，也不将 Laya 标成 jev-1.13.0。
- 新增可选运行环境与页面，不迁移数据库，不改变已有事件、投影或旧数据重放。停止服务/移除本地入口即可回滚；保留模型文件与用户原有数据。

验收范围：离线加载真实 checkpoint、实际 GPU 推理、回环 HTTP、页面手动操作和拒绝/忙/超时契约。开发通路通过不等于模型质量、独立 QA、FR-06 完整验收或生产准入。离线 gold、中文/partial/OOD、校准与非作者批准仍按开发规约执行。
