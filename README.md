# 知因・Whynote

可审计的负反馈信号闭环研究 PoC：先可靠记录点踩和用户亲选原因，再验证模型建议能否增加有用反馈、减少填写成本，并避免错误归因。

[产品 PRD](https://my.feishu.cn/wiki/XG6SwutL0i3fyWkwmhScEEB86Gg) · [技术文档](https://my.feishu.cn/wiki/GuphweJs3iWwBRkBDjlcljA16bh) · [开发计划](docs/development-readiness.md) · [开发规约](docs/development-governance.md)

## 当前进度

主干基线 `cdcfb15` 已合入 PR #41/#42。[PR #43](https://github.com/262412/-Whynote/pull/43) 的独立 QA 已执行；发现的 Q-40 保留期缺陷已在 `40215eb` 修复，待独立复验、非作者批准和适用签署，保持 Draft。作者完整 **1039/1039**、实际隔离 Laya 到期边界和合成案例 UI 通过，见[修复记录](docs/q40-fix-verification.md)。

历史运行代码 `7ef5fa8`：完整 **1027/1027**、Ruff 通过；实际三源每源 100 源记录冒烟、每源 1000 目标批量、取消恢复及指定隔离核验已执行。3000 槽：建议 748、拒识 208、超限 1960、跳过 84，技术失败/中断/未开始均 0。合预算 **956/3000（31.87%）**；不能把未标注诊断当作正确答案。本次 Q-40 修复未重跑这些批次。

另在预热后连续运行合成输入 **1800.031 秒 / 16050 次调用**，模型加载 1 次，指纹和回收核验通过。详细分母、资源、历史失败及限制见[开发验证记录](docs/m55-delivery.md)。

**路线：三源文件 → 本机无标签诊断 → 查看报告 → 人工审阅 → 理由包和新留出集 → 盲标复标 → 正式评测。** 正式质量和本人效用尚未通过；生产上下文、auto-attach、自由文本 SLM、训练导出继续关闭。

本机专用环境已从固定干净候选 `40215eb` 安装 Whynote；Laya/GPU 依赖保持原版本。原项目 checkout 及无关改动保留，实际代码在隔离工作树。源文件/缓存/投影/结果留在项目 D 盘研究目录，遵守[来源使用记录](docs/m55-source-use.md)的 30 天保留期。

## 三源无标签本机探索（M5-5 / D-21）


新增统一入口 `python -m whynote.explore`，支持 `prepare/run/resume/report/view`。
使用获准且固定版本的 HelpSteer3、WildFB、WildFeedback 文件；不要求人工标签或正式评测封存。
当前配置为单 CUDA worker、常驻模型、batch size 1，默认方案 C；输出为未确认诊断，质量指标为 NA。
下载与访问范围见[三源使用记录](docs/m55-source-use.md)，字段、失败和恢复语义见[探索契约](docs/m55-exploration-contract.md)。

下面是本机现有路径。先从核验过的干净候选构建并以非 editable 方式安装 Whynote；保留已可用的 Laya/GPU 依赖。
不要从有未提交改动的原目录重装。每次新批次使用新的输出目录。

```powershell
$m55Root = 'D:/PythonProject/jev项目/var/research/m55'
$m55Python = 'D:/PythonProject/jev项目/var/laya-runtime/Scripts/python.exe'
$m55Base = 'C:/Users/22826/AppData/Roaming/uv/python/cpython-3.11-windows-x86_64-none'
$m55Model = 'D:/PythonProject/jev项目/var/models/laya/multilingual'
$m55Run = "$m55Root/runs/my-first-batch"
$env:TEMP = "$m55Root/tmp"
$env:TMP = $env:TEMP

& $m55Python -I -B -X utf8 -m whynote.explore prepare --manifest "$m55Root/source-manifest.json" --output $m55Run --records 100
& $m55Python -I -B -X utf8 -m whynote.explore run --output $m55Run --python $m55Python --base-python $m55Base --model-dir $m55Model
& $m55Python -I -B -X utf8 -m whynote.explore report --output $m55Run
& $m55Python -I -B -X utf8 -m whynote.explore view --output $m55Run
```

`view` 打印本机带随机 token 的地址；用该地址打开报告，筛选结果并主动查看单个案例。
JSONL、汇总 JSON、HTML 默认仅包含元数据；正文和参考反馈留在受控输入库。查看会登记探索暴露。

- 数量：`--records 100` 表示每源最多 100 条源记录。超过 100 条源记录时显式给出 `--targets`；每源 1000 目标用 `--records all --targets 1000`。实际扫描量与解析/关联排除单列；各源仍受 manifest 的记录和目标上限约束。
- 范围：`--sources helpsteer3 wildfb` 选择来源；`--schemes A B C` 选择方案。数量必须落在使用记录的授权范围内，显式 `all` 不扩大授权。
- 进度：终端输出进度，`journal.jsonl` 逐槽持久化。可另用 `report` 刷新汇总；加载、失败、超限、拒识和中断各自计数。
- 取消：在该 run 目录创建 `cancel.request`。停止后保留该文件的审计副本并改名，再把上述 `run` 命令改为 `resume`。恢复只处理未开始槽；已开始但结果未知的槽不自动重试。
- 版本：恢复要求源文件、输入、模型、理由包、源码、运行时与配置一致；变更后创建新 run。源文件到期后拒绝执行或查看正文。
- 正式评测：原 `controlled_replay` / `self_review` 入口与标签规则保留；探索报告不产生质量 PASS，也不能直接作为盲评留出。

## 剩余安排

- 独立 QA 复验三源关联、统计分母、恢复和运行边界；随后完成非作者批准及适用签署。
- 查看超限、拒识与正常对照案例，决定下一版输入策略和理由包；当前 700/1024 token 边界不变。
- 正式评测另选未暴露材料，冻结理由包、标签和协议。本人效用对照后置；本人自评不写成独立验收。

具体责任和退出条件见[当前开发计划](docs/development-readiness.md)。

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
