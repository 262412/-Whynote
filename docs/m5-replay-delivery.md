# M5-1 三方案回放开发交付

2026-09-28；FR-05/06/09/14/15、TD-02/07/08/11、D-18。
输入 PRD317 / 技术文档323；基线 main `441b2a5`。PR #36 已合并，main CI
`36424456923`通过，用户验收 M5-0b 并授权本阶段。先写[契约](m5-replay-contract.md)再实现。

## 当前实现

- `whynote.replay`：同一固定 Laya 的 A八类 / B单领域 / C任务＋通用＋一次跨任务回退；
  固定单候选、顺序、seed与预算，三方案共用完全相同的预测输入。
- 三源沿用 M5-0a 固定版本/文件摘要、准入、关联和分割校验；来源失败隔离。
  解析引用时再次核对hash，输入不含反馈/未来轮次/自动标签或人工任务、原因标签。
  原材料缺失不冒充已提供。另复用7个开发者探索例，预期标签不进入模型。
- `replay_laya`：独立离线模型进程；原SDK/模型SHA校验，网络connect禁用。
  输入和每个完整问题在SDK调用前校验预算，拒绝静默截断。60秒超时终止子进程，
  后续标backend_unavailable；加载失败也给全部样本保留失败记录。
- `replay_metrics`：有独立裁决holdout标签才计算库覆盖、初始/最终路由遗漏、材料缺失、
  最终命中、条件判断错误和误导，另算人工路由参照。该口径仅由单元测试验证；
  合成CLI不接独立标签，当前实际报告quality_metrics=null。
- 输出运行前manifest、逐方案预测/错误/阶段耗时和三源分别报告，目录不可覆盖；
  只保存引用、hash、枚举、模型分数与必要元数据，没有正文、用户确认或数据库/事件写入。

B/C保留完整criteria，每个理由独立判断yes/no/unknown，再按yes概率取1项。
A保留旧Choice/Noul接口。完整11项criteria超出单个256-token Choice问题预算，
因此A→C涉及类别和判断形式变化，不能解释成纯路由收益；B/C的理由判断方式相同。
所有分数未经本项目校准。真实回放中C没有触发二次回退；回退路径的证据来自单元测试。

## 已执行证据

Windows / Python3.11.14，锁文件安装，修改后重装非editable包：

| 检查 | 结果 |
| --- | --- |
| Ruff check / format `src tests integrations qa` | 通过，76个文件 |
| 新增 `tests/test_replay.py` | 44项通过 |
| 完整 `pytest -q tests qa` | 517项通过；1条既有Starlette/httpx弃用警告 |
| 实机固定checkpoint | RTX5070Ti Laptop，Laya0.3.21 / Torch2.11.0+cu128 / Transformers4.57.6 |
| 合成输入 | WildFB1、HelpSteer3按回答2、WildFeedback1、任务探索7，共11对象×3方案 |
| 重复回放 | 4次，选择/状态/概率逐项一致；耗时与run_id按次保留 |
| 实机代码对应 | attempt-4 manifest中9个实现/包文件SHA对应评审修复后的实际执行源码 |

[汇总回执](../qa/evidence/2026-09-28-m5-replay/summary.json)、
[最终manifest](../qa/evidence/2026-09-28-m5-replay/attempt-4/run-manifest.json)、
[最终预测](../qa/evidence/2026-09-28-m5-replay/attempt-4/predictions.jsonl)、
[最终报告](../qa/evidence/2026-09-28-m5-replay/attempt-4/report.json)。
attempt-1为首轮；attempt-2补全来源失败审计；attempt-3对应首个实现提交的统计代码。
前三次未改变提示/输入/模型/协议；attempt-4补齐长度报告与运行绑定校验，预测配置不变，四次选择/概率一致。早期结果全部保留。
随后`7f4f56a`补上全来源HOLD且无探索样本时保留空批报告、不加载模型的边界，并新增实际子进程
超时终止检查；新增测试由34项增至36项。该补修没有改变正常样本的模型输入或预测路径。
后续Codex评审指出长度分层、留出集标签完整性和跨运行混算三项缺口，均已修复并增加8项回归，
现为44项新增/517项完整测试；无标签仍不计算质量。质量函数验证完整holdout标签与单一manifest/协议/模型/输入身份。
最终实机11例全在0–1024字节桶，长输入边界只有单测证据，未报告长输入实机效果。

| 方案 | 合法输出/尝试 | 具体理由 | unknown | no_match | p50 / p95 ms |
| --- | --- | --- | --- | --- | --- |
| A | 11/11 | 10 | 1 | 0 | 16.329 / 310.545 |
| B | 11/11 | 3 | 6 | 2 | 29.900 / 46.856 |
| C | 11/11 | 3 | 8 | 0 | 35.056 / 45.447 |

以上仅为11个合成对象的开发运行结果。没有删掉冷启动首例；模型加载另计。
部分代码例A输出unnecessary_refusal，不符合开发者预期；B/C也存在拒识和与开发预期不符的
选择。没有独立gold，不能把合法输出或重复一致解释为准确率、路由收益或产品效果。
本机Torch提示缺triton仅影响flop counting，推理实际成功；没有修改依赖消除该提示。

## 复现

普通契约测试不加载GPU：

```powershell
uv sync --extra dev --locked --no-editable --reinstall-package whynote
uv run --no-sync pytest tests/test_replay.py -q
```

本机实测复用已安装的专用Laya环境和模型文件，显式从本工作树加载新模块，没有重装或改变
正在使用的Laya服务环境。`--output`必须是新目录；停止本次进程不停止既有服务。

```powershell
$m51PreviousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = (Resolve-Path src).Path
    & 'D:/PythonProject/jev项目/var/laya-runtime/Scripts/python.exe' -X utf8 -m whynote.replay `
      --manifest fixtures/m5-replay.json --output var/m51-new-run `
      --model-dir 'D:/PythonProject/jev项目/var/models/laya/multilingual' --enable-local-model
} finally {
    $env:PYTHONPATH = $m51PreviousPythonPath
}
```

CLI退出0为本批来源与全部方案合法完成；2为部分来源/运行失败且报告已保留；1为入口/manifest/IO拒绝。
没有启用参数时，读取输入和加载模型之前拒绝。

## 剩余与交付门槛

[PR #37](https://github.com/262412/-Whynote/pull/37)首个实现`810e74d`的
[CI 36427121385](https://github.com/262412/-Whynote/actions/runs/36427121385)两项通过；
补修及文档后的最新head CI以PR检查为准。工程实现待非作者有效批准与独立QA/适用签署；
当前Copilot因配额用尽未评审；Codex三项意见已修复，没有有效APPROVED。本批不是完整FR或M5-1质量验收。
真实数据入口沿用M5-0a的synthetic限制：剩余逐源许可/用途及受控存储记录、原文件schema/关联实测、
真实探索后的产品采用理由版本、独立留出集、样本量/接受阈值事前签署。
未启动M5-2展示/确认采集，生产上下文、auto-attach、自由文本SLM和训练导出仍关闭。

复用`whynote-m5-source-mapping`工作树，分支`codex/m5-laya-replay`；主目录其他用户改动与运行数据保留。
合并前保留分支/工作树，不能以开发自审替代非作者批准。
