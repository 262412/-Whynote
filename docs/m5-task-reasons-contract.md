# M5-0b 任务理由包契约 v1

2026-09-28；FR-05/06/09/11/14、TD-02/07/09、D-16/18。
输入：PRD 312、技术文档 314；基线 main `453fca7`。用户已接受 M5-0a 合成切片并明确要求
进入 M5-0b。本文件冻结本次离线实现的字段、枚举和包内容；不代表真实样本效果或 UI 上线签署。

## 最小理由库

新增独立包 `m5-task-reasons-v1`、criteria `m5-task-criteria-v1`、模板 `m5-task-templates-v1`。
仅增加 code_rewrite 专用包：接口改变、行为改变、改写不完整、超出修改范围。
通用包保留事实错误、未按指令、不完整、无关、表达方式、过时、不必要拒绝七种问题。
每项包含稳定 reason_id、reason_family、task_types、可选 domains（v1 为空）、名称、定义、
正例/反例、criteria、证据要求、固定模板、旧码映射及包版本。尚无真实探索数据，种子理由
依据用户指定代码改写任务及 M5-0a 合成接口改名案例制定，必须在真实探索集上再审阅。

通用七项可用于旧八类的**离线统计对照**；代码细项全部 `legacy_reason_code=null`，
不以“含义相近”强行投影到旧码。reason_family 是新包内部的汇总维度，不是旧码回写规则。
旧 `other_or_unknown` 仍原样保留在 manual-v1；新包的无法判断是独立 outcome，不能回写为
用户选择了旧 unknown，也不能合并为用户拒绝候选。

包文件随 wheel 发布，使用规范 JSON 的 SHA-256 固定内容，跨 CRLF/LF 得到相同摘要。
相同版本换内容必须失败；内容变更时同时升级版本与摘要，不原地重标历史记录。

## 候选构建

`prepare_candidates(task_types, evidence_kinds, fallback_tasks=())` 是离线纯接口，不读取会话或模型。
task_types 使用 code_rewrite/general/other/mixed/unknown；禁止关键词猜任务。

- code_rewrite 纳入四个专用理由，所有路由均保留通用库。
- mixed/unknown/other 纳入当前所有专用包，避免不确定任务提前排除正确理由。
- general 仅用通用库；需要跨任务回退时显式传 fallback_tasks=[code_rewrite]。
- 多任务与回退取并集、去重；顺序使用包内固定顺序，与调用方任务顺序无关。
- 证据种类只有 request/answer/original_code/reference。每条候选必须满足包内要求；
  缺原代码时四个代码细项不可选，事实/过时还需 reference。缺 request 或 answer 时不能给问题建议。
  返回完整路由 reason_ids、可选候选、各项缺失证据，区分路由排除与证据不足。
- 证据种类由后续受控上下文构建者提供，只表示材料存在，不能证明候选判断正确。此处既不
  读取正文，也不验证引用的真实性；引用匹配、对象版本和因果支持由 M5-1/2 分别验证。

候选集 ID 绑定版本、包摘要、任务/回退、证据种类和有序候选；同输入重跑一致。
固定模板为可确认的短问题，不插入自由文本、伪造行号或未执行的运行结论；M5-2 再接引用和展示。

## 选择结果

`validate_selection` 接受当前候选集及 `{candidate_set_id, outcome, reason_ids}`。
候选集版本/摘要/内容重算验证，不信任被修改的候选列表。outcome：

| 值 | 语义 | reason_ids |
| --- | --- | --- |
| suggested | 模型/离线选择出的待确认问题 | 当前可选集合中的 1–3 个不重复 ID |
| unknown | 材料不足或无法判断 | 空 |
| no_match | 有 request/answer，但当前可选理由无合适匹配 | 空 |

`none_matched` 是既有用户显式“都不是”的行为，此接口拒绝该值，也不接受 confirmed/selected。
unknown/no_match 不写任何动作、推测或用户事件，不清空旧原因。校验结果只标
`selection_origin=caller_supplied`、`confirmation_status=unconfirmed`；不把人工提供的测试选择
冒充实际模型预测。M5-1 另记真实模型调用来源。验证通过只说明符合结构和材料门槛，不说明理由正确。
过期 candidate_set_id、未知 ID、重复/过多候选、缺证据选择均拒绝，无持久化副作用。

## 运行与兼容

`python -m whynote.task_reasons --task code_rewrite --evidence request --evidence answer --evidence original_code`
输出包身份、路由及可选项、criteria/定义/模板和缺失证据。CLI 不接收正文，不发网络请求、不加载
Laya，也不写数据库；用于开发审阅与 M5-1 runner 接入准备。退出 0 表示构建成功，2 表示参数/契约拒绝。

可加 `--selection-file <JSON>` 校验人工提供的离线选择结果（只含候选集ID、outcome、reason_ids，
上限8192字节，重复JSON键拒绝），仍不调用模型或写事件。此命令检验结构与材料门槛，不预测理由。

本阶段不改 host 白名单、旧 `REASON_CODES` 或 `LocalLaya.QUESTIONS`。新 ID 直接传旧接口仍应拒绝；
旧 `other_or_unknown` 和 `none_matched` 的重放/幂等规则继续按 manual-v1 验证。
新版持久化和展示接口必须在 M5-2a 另定，不能自动用本包替换旧菜单。回滚只需停用离线包；无迁移。

## 验证与退出

合成探索表记录代码改写、否定约束、混合任务、跨任务回退、无原材料、无匹配候选和无法判断，
每项明确开发审阅理由，独立留出集状态保持“未建立”，不把这些开发用例再称为独立质量评测。
M5-0b 的工程交付包含包/候选/校验/CLI及兼容回归；真实探索审阅、产品 taxonomy/criteria 签署、
独立留出集与效果结论仍需证据。M5-1 的输入/候选数/阈值必须另行事前冻结。
