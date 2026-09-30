# M5 产品二次交叉验证与下一阶段决定

当前跟进（2026-09-29，D-20）：PR #41契约/统计指定工程范围已独立通过。HelpSteer3已获准固定文件前200条探索审查并完成下载/hash/关联/tokenizer核验；新增离线180槽位执行器和本机AppContainer合成开发证据见[回执](https://github.com/262412/-Whynote/blob/deb9649/docs/m54a-controlled-delivery.md)。真实留出准入、本人48小时盲复标封存、新切片独立工程/环境复验及运行确认仍待完成；既有在线宿主运行边界待补。作者合成测试不替代独立验收，M5-4b另立协议。下文产品二次交叉验证及其补充保持当时原文。

日期：2026-09-29，Asia/Shanghai。输入PRD387 / 技术384；写后读回PRD403 / 技术401；决定D-19，承接D-18。

## 1. 结论与范围

2026-09-29产品二次交叉验证：main 5abdc580已包含PR #35～39，M5-0a、M5-0b、M5-1、M5-2a/b的合成工程切片均已实现并合并。本轮固定相同文件树独立执行699项核心/保留QA、Ruff以及11个合成对象×A/B/C共33条实际本机Laya回放，全部运行通过；无独立gold，质量指标为空。Q-27指定技术修复通过；旧失败保留。现阶段为“工程链路已具备，真实适用性与用户收益待验证”。新模板确认分支仍从suggestion_fixture取任务/理由/引用，未接实时Laya输出；既有真实单按钮分类链路与新合成确认链路须分别描述。下一步执行D-19：M5-3a真实案例/逐源准入与M5-3b实时Laya合成集成并行，再M5-4a独立质量评测、M5-4b限定本人效用试验。PR #35～39均未见GitHub非作者APPROVED，适用责任人签署仍缺；这属于已合并后的治理欠项，不能再写“待合并”。生产上下文、auto-attach、自由文本SLM、训练导出及新增真实研究开关保持关闭。

产品判断：路线符合“可审计的负反馈闭环研究PoC”的预期，已从记录动作推进到可验证建议/确认的工程基础。下一步把现有部件连成真实模型的合成闭环，并用获准实际材料检验适用性，再测试是否减少补写。当前不足以证明任务理由方案优于常规菜单，也不足以认定Laya不适用。

## 2. 固定证据与执行身份

- 远端main：`5abdc580c7c510b247fa2e1db1d0fcb40f96416e`；合并CI [36456950323](https://github.com/262412/-Whynote/actions/runs/36456950323)成功。PR #35/#36/#37/#38/#39均MERGED。
- 复验使用空闲且干净的QA工作树detached `28ac4b70ee6cdf78d0fa64dd20f868507ffbecfa`，其Git文件树与main完全相同：`5b90a666af987eb0af624c031fc2b6f167853d01`。未切换/同步原目录。
- 显式PYTHONPATH指向该树src及根目录，打印实际导入位置；未沿用原目录旧包。用新临时目录和SQLite，保留先前QA失败及运行库。
- 本轮由产品审阅会话独立执行既有测试和实际本机模型回放；未改业务代码/测试断言。下表区分本轮实测与历史证据。

## 3. 本轮实际执行

| 检查 | 结果 | 可支持的结论 |
| --- | --- | --- |
| 核心与保留QA：tests、qa/test_s0_regressions.py、qa/test_manual_v1_acceptance.py | 699/699，79.74秒；1条既有Starlette/httpx弃用警告 | 包含原Q-27独立断言、可信到达时间、SQLite锁等待、权限/版本竞态及M5全部回归 |
| Ruff check / format | 通过，87文件格式一致 | 静态检查；不代表产品效果 |
| 三源source-shaped合成映射CLI | 全部mapped，3行→4回答目标 | 适配与引用/统计管线可执行；未下载真实语料 |
| 固定Laya真实GPU回放 | 11对象×3方案=33合法输出 | RTX5070Ti Laptop，本地权重，工作进程禁止网络；状态、选择及逐阶段概率与先前独立实际回放一致 |
| 独立重算回放计数 | A:10建议/1unknown；B:3建议/6unknown/2no_match；C:3建议/8unknown | 与report一致；quality_metrics及user_cost_metrics均null，无gold不能报准确率 |
| M5-2a建议fixture重新建库 | CLI完成，新库和报告留证 | 事件/来源/漏斗合成回归，不能据此产生真实确认率 |

本批实际C回退0次，全部输入在0–1024字节层；相关失败/边界由单测覆盖，不把单测称为真实模型长输入/回退实测。本轮未重复运行浏览器、原生Open WebUI整套或前端生产构建，也未读取私人聊天、调用DeepSeek或增加云费用。

执行命令（在固定QA树，输出全部使用本轮新目录）：

```powershell
$env:PYTHONPATH = "$PWD/src;$PWD"
$env:PYTHONDONTWRITEBYTECODE = '1'
.venv/Scripts/python.exe -X utf8 -m pytest tests qa/test_s0_regressions.py qa/test_manual_v1_acceptance.py -q -p no:cacheprovider --basetemp <新临时目录> --junitxml=<新报告>
.venv/Scripts/ruff.exe check src tests integrations qa
.venv/Scripts/ruff.exe format --check src tests integrations qa
.venv/Scripts/python.exe -X utf8 -m whynote.source_review --manifest fixtures/m5-synthetic/batch.json --output <新目录>
D:/PythonProject/jev项目/var/laya-runtime/Scripts/python.exe -X utf8 -m whynote.replay --manifest fixtures/m5-replay.json --output <新目录> --model-dir D:/PythonProject/jev项目/var/models/laya/multilingual --enable-local-model
.venv/Scripts/python.exe -X utf8 -m qa.m52_suggestion_fixture --output <新目录>
```

本轮日志、JUnit、PR检查快照、逐条GPU预测、源码/工作区保护与飞书回执位于：`C:/Users/22826/.codex/visualizations/2026/09/26/01a0dd64-44a2-7603-a006-facd922486bd/m5-product-crosscheck-20260929`。详见[审计摘要](C:/Users/22826/.codex/visualizations/2026/09/26/01a0dd64-44a2-7603-a006-facd922486bd/m5-product-crosscheck-20260929/summary.json)；此路径为本机产物，未伪称已提交仓库。

## 4. 逐项代码与需求交叉对照

| 切片 | 已实现并复验的范围 | 当前真实缺口 |
| --- | --- | --- |
| M5-0a / PR35；FR-04/05/14/15、TD-02/06/07/11/14 | source_mapping.py/source_review.py的三源合成映射、来源角色、目标关联、拒绝/HOLD与统计；CLI强制synthetic | 固定真实文件schema、许可/内容筛查、真实用户选定案例和近重复分割未完成；不能称三源已自动评测 |
| M5-0b / PR36；FR-05/06/09/11/14、TD-02/07/09 | task_reasons.py/json的4代码＋7通用理由、证据要求、稳定ID/版本、回退与选择校验 | 7个开发合成探索例不是用户原因分布；理由库采用版本和独立留出标签未定 |
| M5-1 / PR37；FR-05/06/09/14/15、TD-02/07/08/11 | replay.py/replay_laya.py/replay_metrics.py及三方案实机runner | 当前入口仅synthetic；真实语料评测、独立标签与事前阈值未完成；33条合法输出不证明质量 |
| M5-2a / PR38；FR-11/13/14/15、TD-04/10/11 | suggestions.py的生成/渲染/响应/失效、绑定、CAS、幂等和只读as_of报告 | mock-only；真实模型来源及新增真实记录准入未接通 |
| M5-2b / PR39；FR-06/09/10/11/13、TD-02/04/07/09/10 | template_suggestions.py、template_action.py、suggestion_dialog.js的精确引用、模板、确认更正与常规回退；Q-27修复 | task/reason_ids/citations仍从suggestion_fixture供给，没有实时Laya→任务理由→引用→用户确认端到端；用户成本未测 |

代码固定链接：[source_review](https://github.com/262412/-Whynote/blob/5abdc580c7c510b247fa2e1db1d0fcb40f96416e/src/whynote/source_review.py)、[任务理由](https://github.com/262412/-Whynote/blob/5abdc580c7c510b247fa2e1db1d0fcb40f96416e/src/whynote/task_reasons.py)、[回放](https://github.com/262412/-Whynote/blob/5abdc580c7c510b247fa2e1db1d0fcb40f96416e/src/whynote/replay.py)、[Action](https://github.com/262412/-Whynote/blob/5abdc580c7c510b247fa2e1db1d0fcb40f96416e/integrations/openwebui/template_action.py)、[模板引用](https://github.com/262412/-Whynote/blob/5abdc580c7c510b247fa2e1db1d0fcb40f96416e/src/whynote/template_suggestions.py)、[建议事件](https://github.com/262412/-Whynote/blob/5abdc580c7c510b247fa2e1db1d0fcb40f96416e/src/whynote/suggestions.py)。

历史独立证据：[M5-0a](https://github.com/262412/-Whynote/blob/5abdc580c7c510b247fa2e1db1d0fcb40f96416e/docs/qa-m5-source-independent.md)、[M5-0b](https://github.com/262412/-Whynote/blob/5abdc580c7c510b247fa2e1db1d0fcb40f96416e/docs/qa-m5-task-reasons-independent.md)、[M5-1](https://github.com/262412/-Whynote/blob/5abdc580c7c510b247fa2e1db1d0fcb40f96416e/docs/qa-m5-replay-independent.md)、[M5-2a](https://github.com/262412/-Whynote/blob/5abdc580c7c510b247fa2e1db1d0fcb40f96416e/docs/qa-m5-suggestions-independent.md)、[M5-2b初验](https://github.com/262412/-Whynote/blob/5abdc580c7c510b247fa2e1db1d0fcb40f96416e/docs/qa-m5-templates-independent.md)、[Q-27复验](https://github.com/262412/-Whynote/blob/5abdc580c7c510b247fa2e1db1d0fcb40f96416e/docs/qa-q27-retest.md)。Q-27复验曾独立运行699/699、原生80/80、探针58/58；本轮不将后两项重记为本轮执行。早期浏览器证据是合成宿主桥，不是当前完整登录Open WebUI UI验收。旧报告“待合并”描述当时状态，本次以main已合并为准，历史文件不改写。

## 5. 产品发现与下一步

1. **工程完成与用户目标分开收口。** 五个合成切片退出开发待办，真实材料、质量和研究缺口转入M5-3/4，避免反复重做同一基础能力。
2. **最短缺口是实时模型接新确认链路。** 下一研发切片M5-3b，先只用合成聊天，不能只把mock开关改为true/false；必须定义实际模型来源/版本和失败兼容。M5-3a并行完成真实材料准备。
3. **暂不宣布C获胜。** 同一固定Laya比较A旧八类、B单领域、C任务＋通用＋回退。当前A为多选一Choice，B/C为逐理由yes/no/unknown，故A/C同时改变候选库、提问形式和路由，只能作整套产品方案比较；B/C保持理由判定形式一致，但仍须固定输入和预算。本轮33条合成输出：A具体建议10/11、unknown 1；B具体3、unknown 6、no_match 2；C具体3、unknown 8，全部合法。具体输出多不等于正确，拒识多也不等于安全；本批C未触发实际fallback，全部输入属于0–1024字节层。真实质量评测前先探索修订理由包，再锁定独立留出集和协议；按会话/共同来源/近重复分组防泄漏。预测不得读取事后反馈、gold、未来轮次或修改答案。覆盖不足、路由遗漏、材料不足、判断错误和多问题单候选损失分列；审阅问题标签不冒充原用户动机。无独立裁决标签时质量指标保持空。样本量、任务/语言/长度配额、候选数、上下文及调用预算、模型/criteria/理由库版本、接受阈值、误导上限、观察窗和停止规则事前冻结；失败不删分母。保持8192 UTF-8字节/700输入token约束，真实代码超长拒绝率须先测，不能静默截断或靠关键词丢掉约束。
4. **模板正确引用不等于理由正确。** 字符串在原文中，只能证明引文准确；还须独立判断该片段是否支持该指控。无证据不编造行号或运行错误，不执行用户代码来假装已验证。
5. **真实用户收益尚无测量。** 新增“是否还需补写/必要补充次数/来源理解/能否处理”最小字段和预注册协议；常规菜单为对照。N=1可以决定是否继续，不能证明普遍效果。
6. **治理欠项如实保留。** PR35～39仅见COMMENTED无APPROVED，已合并不抹去非作者评审和适用签署缺口；安排M5-G补充审查，不倒签。此缺口不阻止已授权的合成研发。

## 6. 当前契约解释与文档修正

M5-2a/b合成契约已落地：建议生成、客户端渲染报告、有效响应、用户确认分别计数；回执不证明用户实际看见。yes/correct才形成用户确认；no否定指定建议、none_matched拒绝当前整组，二者均保留此前确认，不撤销点踩。旧manual-v1“都不是”仍按原契约清空原因，两套投影不得混用。skip/decline/close/未响应/unknown/no_match各自分开；0点击不确认。主体、对象版本、event/suggestion/display、候选顺序与模型/理由包/模板/UI版本绑定。60秒按可信服务端回调到达时间判断，事务内继续核对撤权/删除/换版/停用/撤销；Q-27已修复，迟到响应不获得续期。引用只落source/start/end及摘要/对象版本，原文不入事件；active_ms/server_total_ms与timing_status分开，缺失不补零；报告只读可按as_of重放。现有研究来源为synthetic_model，真实Laya接入需新版本与来源契约，不能只移除mock开关。

本轮将飞书旧进度、历史未合并说明移入原归档区，在原FR/字段/实施/决策条目更新当前状态；实现勾仅限上述有代码与复验支持的合成子项。完整FR仍未由本轮批准。PRD/技术历史修订及Q-27原失败保留。原目录旧main及其余用户改动不动；当前计划是未提交的本地产品文档修改。

## 7. 决定与交接

**GO：M5-3a来源/案例准备与M5-3b真实模型的合成集成。**

**条件GO：** 逐源获准的真实小批、独立质量评测；通过后再限定本人新模板试验。

**NO-GO：** 宣称少补写/真实质量已通过、完整FR验收或发布；生产上下文、auto-attach、自由文本SLM、训练导出继续关闭。

具体责任、依赖与退出条件见[当前开发计划](development-readiness.md)。样本量、阈值、试验时长及新增真实记录范围尚待事前决定；既有DeepSeek授权不重复询问。本轮未推送、合并、部署、切换原工作区或扩大真实数据。


## 2026-09-29补充：转入M5-4a准备（保留上文历史范围）

本次只做当前状态和源码交叉核对：PR #40已于2026-09-29合入main1d2abd7，合并CI36534464895成功；Q-36/Q-37独立复验提交7bd3954已包含在主干。上文5abdc580和“下一步M5-3”的描述属于当时快照。

当前安排为M5-4a冻结独立标签、样本量/阈值，达到质量门槛后再M5-4b与常规菜单对照。此前841/80/58及两次模型调用来自既有独立QA，本次未重做该报告的实机模型/UI操作；文档PR自动CI另列，不作为质量证据。

本次读取飞书PRD504/技术489，仍见三源HOLD、样本/阈值待定；replay.py入口和报告仍synthetic-only，replay_metrics.py要求独立裁决标签但没有真实批次和盲审流程证明。现有judgment_error含有可用理由时的拒识，misleading排除unjudgeable，routing_omission分母含路由不可用例；正式阈值前须明确口径并补相应拆分。

已整理[冻结协议草案](m54a-freeze-protocol.md)和只列剩余任务的[当前计划](development-readiness.md)。协议仍DRAFT，人员、N、配额、数值阈值及签署留空；没有下载/标注真实数据或执行质量评测。M5-4b未开启，出站隔离/并发、治理欠项及完整FR/生产门槛保留。


同步回执：本轮按用户确认仅准备协议，参数随后确认。飞书在原M5-4a/4b及D-19条目共11处局部更新并读回PRD504→510、技术489→494；旧文本与链接保留。草案PR #41/b3d8f6f的自动CI36535328194两项通过；无本地模型/UI实测或真实质量评测。见[同步证据](../qa/evidence/2026-09-29-m54a-preparation/feishu-sync.json)。
