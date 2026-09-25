# 知因・Whynote

负反馈原因辅助标注的首个开发切片。依据 [技术开发文档 v0.1](https://my.feishu.cn/wiki/GuphweJs3iWwBRkBDjlcljA16bh) 和 [JEV 产品 PRD](https://my.feishu.cn/wiki/XG6SwutL0i3fyWkwmhScEEB86Gg) 建立动作与归因分离的服务端基础。

## 当前实现

- `POST /v1/feedback-actions`：先在同一 SQLite 事务写入动作事件和 Gate Outbox，返回稳定 `event_id`，不等待推断。
- `POST /v1/feedback-actions/{event_id}/retract`：追加动作撤销事件和取消消息；重复撤销不重复写。
- `GET /v1/feedback-actions/{event_id}`：从追加事件重建当前状态。
- `POST /v1/feedback-actions/{event_id}/attribution-events`：只接受绑定到已记录展示的明确用户动作；原因码必须实际展示，手动选择来源为 `user_manual`。当前展示确认由受信任的宿主集成调用 `EventStore.record_display`，公共展示上报接口尚未接入。
- 四维 Gate 规则及可信的入样概率记账；拒绝时原因保持空。当前允许路径以 `pipeline_unconfigured` 拒识，不读取上下文，也不调用供应商。
- TypeSafe Jev Choice/Noul 响应解析与概率校验；尚未接入真实 API。

## 本地验证

```powershell
uv sync --extra dev --no-editable
.venv\Scripts\python -m pytest
```

本地目录包含中文字符，当前 Windows Python 3.11 对 editable 安装生成的 `.pth` 路径解码不正确，因此使用普通安装。改动源码后，可运行 `uv sync --extra dev --no-editable --reinstall-package whynote` 再测试。

`uvicorn whynote.api:app` 可以启动 HTTP 进程并查看 `/health`，但业务接口默认拒绝请求。宿主平台须在 `create_app` 注入经过验证的身份解析和目标对象权限检查后才能处理反馈；不能把客户端传来的身份或目标 ID 直接当成权限凭据。

本仓库尚未绑定产品平台、用户授权、快照与保留策略、预算账本、队列、真实模型调用、校准器或前端。具体接口路径是待平台评审的逻辑契约。请参阅 [开发决策与下一步](docs/development-readiness.md)。

归因状态、展示绑定、UTC 时间及旧事件重放规则见 [归因与展示契约 v2](docs/attribution-contract-v2.md)。当前仅完成 Q-01 至 Q-03 的服务端修复；PRD 的完整链路和产品验收仍未完成。
