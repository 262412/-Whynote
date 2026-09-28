# M5-1 三方案本机回放契约 v1

2026-09-28；输入 PRD317 / 技术文档323；FR-05/06/09/14/15、TD-02/07/08/11、D-18。
基线 main `441b2a5`，M5-0b 已合并且用户验收。本契约先于 runner 实现记录。

## 实验范围与固定项

- 第一批仅合成来源和开发探索例，模式 `synthetic`。三源复用 M5-0a 的准入、固定文件 hash、
  目标关联与分割冲突校验；public/HOLD 在读取源正文前拒绝。没有解锁真实语料入口。
- 输入 manifest 固定 source batch 与可选 exploration 文件 SHA-256。全批样本进入运行分母，
  缺审阅/分割冲突、材料不足、超长、非法响应、超时均保留；坏来源不阻塞其他来源。
- 每个对象三方案共用完全相同的 JSON state。三源只解析 `prediction_refs` 中已验证的
  context/target；不输入反馈、自动标签、未来轮次、人工任务标签或原因标签。
  M5-0a 尚无独立原代码/参考材料引用，所以不虚构这些材料资格。
- exploration 仅白名单 request/answer/original_code/reference 进入 state，任务及开发预期标签
  只作分层，绝不送给模型。输入原文只在本地内存和原 fixture 中，结果只存 hash/引用/枚举。
- 固定 Laya multilingual `55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851`、SDK0.3.21、
  Transformers4.57.6、1024总token/256问题token、8192 UTF-8字节/700输入token。
  每题选项≤48token、问题不截断，实际组装预算先检查；超限拒绝，不扩大预算。
- 固定种子42；每例按 A→B→C执行，无热身剔除、重试或答案重生成；输出候选数1。
  记录软件/设备、文件摘要、输入/提示摘要、选项顺序、所有阶段及合计耗时。
  冷加载另计；p50/p95使用实际全部尝试，包括失败，超时不当成模型完成耗时。

## 三个产品方案

| 方案 | 路由与判断 |
| --- | --- |
| A | 现有八类 `LocalLaya.QUESTIONS`，保留两项Noul；other_or_unknown归为unknown，非细理由兜底映射。 |
| B | 同一模型先选单一领域code/general；仅该领域理由，不混入另一包。code=当前4项代码改写，general=7项通用。v1没有其他已审领域。 |
| C | 同一模型选code_rewrite/general/mixed/other/unknown；复用M5-0b的任务＋通用候选。general路由无支持候选时，若存在原代码，再加入代码包重判一次；不重复循环。 |

B/C共用同一11项理由库、criteria、材料门槛及顺序。完整criteria不能塞进一个256-token
Choice问题：每个可选理由独立问同一个三值Choice（yes/no/unknown），一批提交；仅在yes中
按其未校准概率降序取1项，同分依冻结顺序。全no为no_match，存在unknown或缺材料候选则为unknown。
这些概率不是正确率。各题固定criteria全文，未加入模板/引用生成。

A与B/C的理由库及判断形式均不同，因此A/B/C只比较整个产品方案；不能将A→C变化解释为
纯路由收益。B/C保持理由判断一致，单列候选库覆盖、路由遗漏和材料门槛，再分析判断错误。
领域/任务路由无效、拒识或超时不默认变成正确任务。

## 输出、指标与标签

输出新目录：run-manifest.json（运行前先落盘）、predictions.jsonl（逐方案追加）、
report.json及各来源报告；不覆盖旧目录。无事件/队列/数据库写入，预测来源为
model_inferred_unconfirmed；测试替身明确标test_stub，不能作为真实模型证据。

- 所有结果带样本ID、state hash、方案、路由、完整库/路由后/材料可用候选、预测或错误、
  阶段耗时、提示摘要。超时终止专用模型子进程，后续样本报backend_unavailable，保留分母。
- 报告按来源、人工审阅任务、语言分别分层；未知值单列。无独立标签时quality_metrics=null。
- 独立标签通过单独 `m5-replay-labels-v1` 文件读取，只供结果分析：固定input_id、partition=holdout、
  review_origin=independent_adjudicated、两名不同reviewer UUID、adjudication UUID，
  verdict=defect/no_defect/unjudgeable、reason_ids与legacy_reason_codes分别标注。
  开发者合成标签不能升级为该来源；v1合成CLI拒绝附带独立标签，指标函数仅用单元测试检验口径。
- 覆盖分母为可判定有问题的已标对象，完整库至少命中1项才算覆盖；不把unknown算覆盖。
  路由遗漏以完整库覆盖为分母；材料门槛遗漏与路由遗漏分开。人工任务参照用同一候选规则对照。
  最终命中以全部可判定有问题样本为分母，运行失败仍在其中；判断错误仅在正确候选可用且有
  合法模型结果时统计，拒识单列。误导按可判定且输出具体理由的样本计；无缺陷也可被误导。
- 多问题标签允许多个正确理由；本轮输出1项，只报告至少命中1项，不宣称覆盖所有问题。
  用户填写耗时、模板质量、产品接受阈值及真实效果结论不由本批合成运行产生。

工程退出：合成三源/探索集实际运行、失败/超时和无泄漏测试、Ruff/pytest、独立QA/非作者批准。
真实评测退出仍需逐源准入与原文件实测、真实探索后的理由库采用版本、独立留出集、事前样本量和
效果阈值签署。用户已授权本机模型，不重复请求此前联调许可，不开启M5-2真实确认采集。
