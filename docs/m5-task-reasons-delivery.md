# M5-0b 理由包开发交付

2026-09-28；FR-05/06/09/11/14、TD-02/07/09、D-16/18。
本轮读取飞书 PRD **312**、技术文档 **314**。M5-0a PR #35 已合并至 main `453fca7`，
合并 CI `36410641269` 成功；用户已验收该交付并明确授权进入本阶段。

## 已实现范围

- `task_reasons.json`：4 个代码改写细理由＋7 个通用理由；每项有稳定 ID、统计家族、
  任务/领域、定义、正反例、criteria、证据要求、固定模板和旧码映射。
- `task_reasons.py`：可复用的离线候选构建、跨任务回退、材料门槛、选择结果验证及 CLI。
  mixed/unknown/other 保留现有专用包，所有路由保留通用库；路由后的理由与证据不足的理由分报。
- 包/criteria/模板版本和规范 JSON SHA-256 固定，候选集 ID 绑定路由与材料种类。
  过期或被修改的候选集、重复/未知/超量 ID、材料不足选择均拒绝。
- `unknown`、`no_match` 和用户的 `none_matched` 分开；新接口拒绝用户确认状态。
  代码细项旧码为 null，通用旧码只供离线统计对照，不写入旧接口或重标历史。
- [7 个合成探索案例](../fixtures/m5-task-reasons/exploration.json)与
  [实际契约校验回执](../qa/evidence/2026-09-28-m5-task-reasons/synthetic-review.json)：
  代码改写、混合任务、否定约束、缺原材料、无匹配候选、跨任务回退、按要求只给代码片段。
  选择结果是开发者提供的标注，未运行模型；没有独立留出集或准确率。

完整字段、兼容和回滚规则见[契约](m5-task-reasons-contract.md)。版本：
`m5-task-reasons-v1` / `m5-task-criteria-v1` / `m5-task-templates-v1`。
包内容摘要：`ef5743a48090692e424335b4d11099f3ac9be6316c2b1c646a56f2dab65cf814`。

## 可运行入口

```powershell
uv sync --extra dev --locked --no-editable
uv run --no-sync python -X utf8 -m whynote.task_reasons --task code_rewrite --evidence request --evidence answer --evidence original_code
```

该输入路由11项、可选9项；事实/过时缺 reference，单列不可选。
增加 `--selection-file <JSON>` 可验证离线选择；文件只含 `candidate_set_id/outcome/reason_ids`。
CLI/包没有模型、网络或事件写入路径。M5-1 再接固定 Laya；M5-2 再接引用、展示与确认。

## 本轮验证

Windows / CPython 3.11.14，锁文件安装，代码更新后强制重装 whynote wheel。

| 已执行检查 | 结果 |
| --- | --- |
| Ruff check `src tests integrations qa` | 通过 |
| Ruff format --check 同范围 | 71 文件通过 |
| `pytest tests/test_task_reasons.py -q` | 46 passed |
| `pytest -q tests qa` | 450 passed，包含新增46项 |
| 7个合成探索案例的已安装包校验 | 7 passed；没有模型预测或用户确认 |
| 安装包从仓库外临时目录运行CLI | 包内JSON可加载，导出及校验成功；未产生数据库/额外文件 |
| 旧事件兼容 | 全部11个新ID被旧菜单拒绝且不追加事件；旧unknown选择与重放保持不变 |
| M5-0a联接边界 | 旧映射只有context/target，不虚构original_code/reference材料资格 |

存在1条既有 Starlette/httpx 弃用警告。新增单测包含缺材料、旧版本/摘要/候选篡改、
多任务和显式回退、0/重复/超过3项候选、错误来源状态、JSON重复键/超限/坏文件及CRLF兼容。
本地没有启动真实 Laya、云供应商、浏览器或运行实例；原生宿主由 PR CI 单独核验。
实现提交 `e48f43522ab88f0cb39412333a5a302b3a969bd5` 的
[CI 36412489303](https://github.com/262412/-Whynote/actions/runs/36412489303)
已通过 quality 与 native-regression。后续文档提交的 CI 以 PR 最新 head 为准。

自动评审指出重复参数超过枚举数量时被提前拒绝，与去重契约不一致。`27c4e51` 已修复，
补充任务/回退/证据种类3项回归：重复输入候选身份不变，混入非法值仍拒绝。
重装包后理由包46项、完整450项及Ruff复验通过；原43/447为首个实现提交的结果。

## 当前交付与剩余事项

[PR #36](https://github.com/262412/-Whynote/pull/36) 已推送，代码提交 CI 通过；
工程实现待非作者有效批准、适用独立QA及签署。Copilot 配额用尽未评审；Codex 的参数去重意见已修复，
当前没有有效 APPROVED，因此按开发规约保留为待合并；不据开发通过写成完整 FR 或模型质量验收。
飞书已在原 M5-0b 任务、字段/TD-02 及实现对照条目局部回填并读回：PRD **316** / 技术文档 **321**。
真实探索案例/三源准入和独立标签仍缺，7个合成案例只能验证结构和门槛。当前理由细分是
开发种子库，真实探索审阅后需再冻结产品采用版本，再建立独立留出集。
样本量、门槛、候选数量比较与Laya输出质量属于 M5-1；宿主白名单、持久化和真实确认属于 M5-2a/b。

复用已有 `whynote-m5-source-mapping` 工作树，新建 `codex/m5-task-reasons` 分支自 main `453fca7`。
原目录未提交代码、运行数据、既有审计文件均保留。新PR未合并前保留本分支和工作树。
