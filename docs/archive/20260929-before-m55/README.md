# 知因・Whynote

可审计的负反馈信号闭环研究 PoC：先可靠记录点踩和用户亲选原因，再验证模型建议能否增加有用反馈、减少填写成本，并避免错误归因。

[产品 PRD](https://my.feishu.cn/wiki/XG6SwutL0i3fyWkwmhScEEB86Gg) · [技术文档](https://my.feishu.cn/wiki/GuphweJs3iWwBRkBDjlcljA16bh) · [开发计划](docs/development-readiness.md) · [开发规约](docs/development-governance.md)

## 当前进度

2026-09-29产品核对：PR #41/#42均已合并main `cdcfb15fec8474969d36e2955d70ba28ade01a06`，[合并CI](https://github.com/262412/-Whynote/actions/runs/36566681483)通过。已有实时Laya合成确认、HelpSteer3真实投影/token预算、固定60×3执行器、封存校验和统计报告；指定工程及隔离复验见[Q-38/Q-39独立报告](https://github.com/262412/-Whynote/blob/cdcfb15/docs/qa-q3839-retest.md)。没有真实批量质量或用户收益结论。

**D-21新路线：三源下载与统一输入 → 本机无标签批量诊断 → 查看报告 → 人工审阅与正式冻结评测。** 新explore入口、另两源真实适配、常驻/恢复和可读报告尚待开发，不能把下述规划当现有命令。完整交接见[三源自动化计划](docs/dataset-batch-exploration-plan.md)。

## 现在可以怎样测试

现有本机Laya和虚构Open WebUI确认链路可按各自交付版本测试。HelpSteer3文件已落盘，前200行结构与预算检查完成，但对其模型调用为0。当前真实执行入口要求正式封存与固定60×3；旧三源runner仅synthetic。新的任意数量、无人工标签探索入口尚未实现。

原项目目录仍是旧main且有改动；Laya专用依赖/CUDA可用，但其Whynote安装包缺新执行器模块。后续必须从核对后的干净候选安装业务包，不能在原目录直接pull或重装覆盖。Open WebUI和DeepSeek不参与此次离线批量数据集执行。

## 公开数据自动化测试

目标支持HelpSteer3、WildFB、WildFeedback三个固定文件，默认候选C、A/B可选。来源/使用范围与文件校验在下载前明确；人工标签、48小时复标、固定样本/中文配额及质量冻结从explore入口移出，保留在后置evaluate流程。

模型输入仅含目标回答及此前上下文；原反馈、另一候选答案、自动标签与未来轮次隔离。自动报告统计关联、预算、分类、拒识、失败、耗时和资源；无人工标签时准确率/真实理由覆盖为NA。已探索材料不能重新称盲评留出。

本机RTX5070Ti Laptop约12GB显存、31.19GiB内存，CUDA/BF16可用，适合先实现串行常驻批量；吞吐/大批量峰值尚待实测，不需先租卡。当前模型state700/total1024 token边界保留，超限如实统计。所有正文、下载缓存、临时文件与本地查看材料位于D盘项目var/research，不提交Git或发云模型。

来源文件、版本、字段、配置和验收详见[D-21交接](docs/dataset-batch-exploration-plan.md)。

## 下一开发安排

1. M5-5a：三个数据集的真实文件适配、统一输入与反馈隔离。
2. M5-5b：无标签explore入口，数量可配，evaluate保持独立。
3. M5-5c：常驻模型、流式处理、进度/取消/恢复及固定候选安装与持续测试。
4. M5-5d：JSONL＋汇总JSON＋本地可读诊断报告，三源真实小批全链交付。

每源100记录冒烟后验证1000目标和持续运行，再支持指定规模或显式all。人工内容审阅、理由包冻结、留出集、盲标复标及正式质量判断后置；用户效用与生产另行判断。[当前任务及退出条件](docs/development-readiness.md)。

## 服务端接口与本地验证

- `POST /v1/feedback-actions`：动作与 Gate Outbox 同事务，返回稳定 `event_id`，不等待推断。
- `POST /v1/feedback-actions/{event_id}/retract`：幂等追加动作撤销及取消消息。
- `GET /v1/feedback-actions/{event_id}`：从追加事件重建当前状态。
- `POST /v1/feedback-actions/{event_id}/attribution-events`：验证展示、原因码、显式操作与权限；用户选择与模型推测分开。
- 四维推断 Gate 的允许路径仍以 `pipeline_unconfigured` 拒识；独立 Laya 手动调用没有接入该流水线。聊天生成预算与原因推断预算分别判断。

在已核对版本的开发 checkout 中按锁文件安装并验证：

```powershell
uv sync --extra dev --locked --no-editable
uv run --no-sync ruff check src tests
uv run --no-sync ruff format --check src tests
uv run --no-sync pytest -q
```

本地中文路径曾触发 Windows Python 3.11 editable `.pth` 解码问题，因此使用普通安装。修改源码后运行 `uv sync --extra dev --locked --no-editable --reinstall-package whynote` 再验证；不要据本 README 自动更换已有 Laya 专用环境。

`uvicorn whynote.api:app` 默认业务拒绝，宿主必须注入可信身份与对象权限校验。在包含 demo 的候选版本中，`uv run --no-sync python -m whynote.demo` 提供本机虚构页面 <http://127.0.0.1:8765/demo>；这不是生产身份或云聊天验收。

完整 FR、公开来源准入、模型质量与发布批准分别维护。[质量基线](docs/quality-baseline.md)保留 2026-09-25 历史快照；当前进度以本页固定主干、开发计划和飞书原需求条目为准。生产上下文、auto-attach、自由文本 SLM 和训练导出保持关闭。
