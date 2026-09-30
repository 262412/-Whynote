# M54A-E 数据、标签与门槛报告契约

> **D-21适用范围更新（2026-09-29）**：以下为后置正式评测协议/既有实现契约，保留历史，不再作为首次无标签自动诊断的前置。新的explore入口由[M5-5研发交接](dataset-batch-exploration-plan.md)定义：移除labels、48小时复标、固定样本配额和人工冻结签署依赖；来源与输入/运行保护继续执行。该解除尚待研发实现，不可直接改旧mode绕过。本协议的旧状态和参数按原日期保留，重启正式评测时核对新版范围/计时，所有新阈值须在正式运行前确定。

2026-09-29；FR-05/06/09/14/15、TD-02/07/08/11/14、D-20。实现口径承接
[M54A-E-v0.1](m54a-freeze-protocol.md)。旧 replay-v1、独立标签和历史报告不变。

## 范围和入口

新增 `whynote.self_review` 离线核验/报告入口，接受一个受控 JSON bundle，输出新文件，
不连接模型、事件库、网络或用户确认入口。预测程序只应接收预测材料；本分析程序在预测结束后
读取标签。真实执行器及其 OS 出站隔离、跨进程锁/取消释放仍需 E4 就绪，不能把本入口当成运行许可。
`mode=synthetic` 只能产生 `synthetic_accounting_only`；真实报告还要求批次文件摘要、执行冻结及
运行环境回执校验。输入中的摘要证明内容一致，不能代替对盲审、准入、环境与签署的实质审阅。

## 版本与材料

bundle 固定为 `manifest/labels/run/predictions` 四项。各层严格字段白名单，普通报告仅输出
摘要、计数、固定失败码和样本 ID；原评价、依据正文、问题和回答保存在单独受控材料中。

- manifest：`m54a-self-review-batch-v1`，模式、协议 ID、当前理由包 SHA256、seed=42、
  admissions 与 samples。每批有来源/revision、批准者和批准/到期时间、文件路径/摘要、受控目录、
  访问人、用途、许可链、保留/删除/备份规则、排除记录摘要及含端点的行范围。
- samples：探索30/验证60，20/40个代码任务；每项固定目标、输入摘要、原生语言、字节/token预算
  测量、材料前置、暴露标记以及来源/会话/近重复组摘要。组摘要跨来源统一计算；任何重复组拒绝。
  token计数应为固定 tokenizer 对全部可能问题（含回退）的最大值，不能用字符数估计。
- labels：`m54a-self-reviewed-blind-v1`，来源 `self_reviewed_blind`，同一个真实标注者、
  封存时间，first/repeat/final 三组；每条有 verdict、完整 reason_ids、legacy_reason_codes、
  参考任务/领域、标注时间及受控依据摘要。固定重标20例（代码13/其他7，seed42），间隔>=48h。
  分歧比较 verdict 与两个理由集合；不确定最终保留 unjudgeable。文件整体摘要由 run 绑定，
  三组均保存，不用最终裁决抹去初次差异。manifest/labels/run_id 使用既有 `source_mapping.digest`
  的规范 JSON 摘要（非任意缩进文本的字节摘要）；源文件则核对原始文件字节 SHA256。
- run：`m54a-self-review-run-v1`，绑定 manifest、labels、协议/源码/运行环境摘要、模型版本、
  开始时间、六种排列各10次的固定 schedule。run_id 是去除自身后的规范 JSON SHA256。
  real 模式还需运行前 `FROZEN_FOR_SELF_REVIEW` 回执：运行人、确认时间、上述摘要及出站隔离、
  跨进程单并发和取消释放三项证据摘要。缺项只给就绪缺口，绝不补默认批准。
- predictions：验证60例×A/B/C恰好180行，run_id/输入摘要一致，顺序符合 schedule；
  路由状态与初始/最终候选、材料可用候选、结果、固定错误码、总耗时、引用存在/支持性审阅、
  安全事件各自记录。无重试或多run拼接；超时记录实际总耗时，失败结果不带具体建议。

## 统计与判定

分母与阈值按产品协议。路由失败单列；成功路由中的遗漏单独计算，材料不足不算遗漏。
选错是在有正确可用候选且运行成功时选了非正确理由；拒识只计合法 unknown/no_match。
端到端命中保留所有缺陷，包括超长、材料不足和运行失败。预算合格与材料齐备分开，
技术失败/等待成本使用预算合格分母，输入适用范围同时要求材料齐备。

所有比例附95% Wilson区间，零分母为NA，门槛所需分母不足为INCONCLUSIVE；p95用nearest-rank。
引用存在和支持性分别计数，无引用为NA，有未审引用为HOLD。A/B只作诊断，C决定退出。
状态优先级：安全事件STOP（gate_status=HOLD、stop_required=true）→已测硬失败FAIL→待审HOLD→
证据/分母不足INCONCLUSIVE→PASS_SELF_REVIEW_PILOT。合成数据即使数值全过也只报告
SYNTHETIC_VERIFIED，另列模拟 gate_status，不能生成真实通过结论。

## 兼容与剩余门槛

新模块不放宽 independent_adjudicated，不创建用户确认事件，不修改旧指标分母或模型策略。
字段、版本、摘要、顺序、封存或白名单不合法时退出固定错误码；不回显输入内容。
真实执行、真实质量、本人效用、独立验收、非作者批准和生产发布分别记录，不能互相替代。

## 使用与核验范围

安装锁定包后执行，输出路径必须尚不存在：

```powershell
uv run --no-sync python -m whynote.self_review --check-setup <受控setup.json> <新检查报告.json>
uv run --no-sync python -m whynote.self_review <受控bundle.json> <新统计报告.json>
```

setup 恰含 manifest/labels/run；bundle 再含 predictions。`--check-setup` 可在尚无预测时验证，
不会调用模型；`CONTRACT_VALIDATED` 同时带 `execution_authorized=false`、`environment_probed=false`。
标签分歧>4时退出1；契约无效退出2。分析成功且C门槛全过退出0，其他结论退出1；格式/绑定错误退出2。

`package_frozen_at < selected_at <= first.labeled_at`，先冻结理由包、再选择验证样本、再标注。
标注时间必须带时区。审批证据、抽样框/近重复分组、真实 tokenizer 测量、引用审阅与运行环境回执
由实际责任人提供；本程序验证字段、配额、摘要、时间与关联，不能从一串摘要证明其背后的事实。
准入文件核验为只读、不解析正文；尚未实现真实数据提取→预测进程的适配及固定180次执行器，
须在E4按获准真实schema和环境约束接通。旧synthetic回放入口不因本次修改获得真实输入能力。

精确字段白名单见 `self_review_contract.py` 的 `fields` 调用，结果枚举见 `self_review_metrics.py`。
完整可执行合成构造见 `tests/test_self_review.py::bundle`；其中所有人名引用、时间、准入与环境回执
均为测试值，包含真实模式结构测试也不构成任何真实准入/批准。
