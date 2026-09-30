# Q-38/Q-39修复前飞书原条目

2026-09-29；PRD528／技术512。历史原文及链接，不作为修复后状态。

## prd / doxc6xRLEQ8cKp9XaVFHNaMoMGf

2026-09-29独立QA（PR #42/44deb39）：√ 新离线AppContainer所测网络/互斥/回收及三次实际Laya合成通过，原样954/954、原生80/80、探针58/58。Q-38/P1阻断：uncaught_error未立即停止，三种返回方式均继续180槽；Q-39/P2阻断：已加载模型后的策略异常误记model_load_failed。新增9项5通过/4失败，修复方须保留断言复验。在线宿主与完整FR未验收，真实评测未开启，生产NO-GO。 历史记录（原范围保留）：2026-09-29独立QA（PR #40/56ec110）：√ Q-36固定错误码与Q-37可信worker指定范围通过：原独立8/8，新边界17/17；实际轻量进程超时/取消后回收，动作/Outbox/菜单保留。两次实际固定Laya合成调用成功。 详见本轮独立报告；完整FR/切片验收、非作者批准及适用签署未完成，生产NO-GO。 历史开发及独立记录（原范围保留）：2026-09-29开发修复（PR #40/5945460，待独立复验）：Q-36数组/对象/null/数字/布尔/未知字符串错误码统一invalid_response，合法固定码保留，动作/Outbox/菜单回归通过；Q-37固定worker模块来源，原60秒超时及取消回收保持。 完整841/841与Ruff通过；运行边界/批准/签署仍阻断，生产NO-GO。 原要求与历史证据保留：2026-09-29独立QA（PR #40/3ae7f30）：√ Q-34/Q-35指定范围通过；新Q-36数组/对象worker错误码缺固定invalid_response，Q-37当前目录同名包可接收合成worker输入。子进程适配仍待修。 详见本轮独立报告；完整FR/切片验收、非作者批准及适用签署未完成，生产NO-GO。 历史开发及独立记录（原范围保留）：2026-09-29开发修复（PR #40/84a5a70，待独立复验）：Q-34构建/启动前拒绝不属于批准集合的非忽略未跟踪源码；Q-35初始化失败不写verified部署记录。Q-31～33独立通过范围保留，不改变推断/菜单回退。 完整816/816与Ruff通过；运行边界/批准/签署仍阻断，生产NO-GO。 原要求与历史证据保留：2026-09-29独立QA（PR #40/8e73c7f）：√ Q-32原两条语义错误现返回invalid_response且保留动作/菜单；Q-33四种运行预检失败在实际宿主均零写入，修正后同实例成功。 详见本轮独立报告；完整FR/切片验收、非作者批准及适用签署未完成，生产NO-GO。 历史开发及独立记录（原范围保留）：2026-09-29开发修复（PR #40/59a0f68，待独立复验）：非法unknown携带理由、路由外理由等语义错误与结构错误均回退固定invalid_response，点踩/Outbox不回滚；宿主初始化仅在运行预检通过后进行。 完整802/802与Ruff通过；运行边界/批准/签署仍阻断，生产NO-GO。 原要求与历史证据保留：2026-09-29独立QA（PR #40/f83a948）：√ Q-30双向后端切换及到达时间交叉校验通过。Q-32非法选择虽安全回退但缺固定invalid_response；Q-33不可用运行路径通过类型预检，运行可用性契约待明确。 详见本轮独立报告；完整FR/切片验收、非作者批准及适用签署未完成，生产NO-GO。 历史开发及独立记录（原范围保留）：2026-09-29 Q-30开发修复（PR #40/6aa60a4，待独立复验）：事件库对称比较生成时与当前的全部准入/版本字段；当前后端不一致时旧render/respond及其库重试拒绝、零追加，双向切换生效。旧fixture无live字段仍兼容，同版本重试幂等；历史确认/事件/Outbox保留，新fixture可用。原两条独立失败未修改，修复后通过；针对性87/87、完整767/767与Ruff通过。本轮未重跑浏览器/模型。Q-28/Q-29既有指定技术范围独立通过；Q-30仍待独立复验，出站隔离/进程并发、非作者批准及签署仍阻断，未合并，生产NO-GO。此前独立复验与失败原文保留：2026-09-29独立复验（PR #40/5add19b，输入PRD432/技术426）：✔ Q-28/Q-29指定技术范围通过；原15项未改、原样756/756，实际原生80/80、探针58/58及Ruff通过。✔ 三种缺参均零宿主写入，补参同实例成功；6种源码/构建变更启动前拒绝且不建库，恢复后校验通过；本轮独立登录浏览器＋实际Laya＋新SQLite确认通过，4事件/1Outbox。前端为校验完整5604产物的开发构建副本，未重建。新增Q-30/P2：事件库直接入口在live切fixture后仍追加旧live渲染/确认；两条独立用例失败，全套756通过/2失败。Action全配置复查仍能挡住所测宿主路径，不称浏览器权限绕过。出站/并发评审机制已核实：connect拦截仍允许本机UDP，evaluate可并行启动2个真实轻量子进程；未外发数据或制造GPU OOM，环境隔离/并发门槛仍开放。独立切片验收未完成，非作者批准及适用签署仍缺；未合并，生产NO-GO。 历史开发记录（原版本范围保留）：2026-09-29 Q-28/Q-29研发修复（PR #40/c89d240，待独立复验）：provision在宿主写入前拒绝缺失/无效模型参数；实际新实例两种缺参均零写入，补参数同实例成功。build成功后绑定源码与完整5604产物；serve拒绝旧/缺失/篡改产物，实际HTML/JS改写均在创建数据目录前拒绝。原独立15项未改且全通过，新增14项、完整756项及Ruff通过；Node22全量构建和新构建浏览器实际Laya确认通过。业务事件/模型策略不变。Q-28/Q-29未独立关闭；非作者批准与适用签署仍缺，未合并，生产NO-GO。此前独立证据及失败原文保留：2026-09-29 M5-3b独立QA（e1c4410）：✔ 实际本机模型确认、离线回退通过；缺原代码返回unknown/空理由，700token及8192字节上限实际进程验证通过。自然/公开回放即使列入白名单仍不调用模型，只保留动作。 Q-28/Q-29开放，非作者批准及适用签署待完成；整体NO-GO。 历史开发记录（原版本范围保留）：当前M5-3b研发状态（2026-09-29，PR #40/2319885，未合并；完整FR未验收）：已实现合成会话实时本机Laya路径：点踩/Outbox先提交，C方案实际任务/理由判断，拒识/超长/离线/超时回退常规菜单；生成前复查。scripted登记及对象版本白名单门控，生产关闭。 原要求与历史证据保留：✔ 固定Laya本机三方案runner、失败状态与合成模板回退已实现；本轮实际33条回放和699项回归通过。replay.py/replay_laya.py、template_action.py。实时Laya→新模板确认尚缺M5-3b；熔断/生产链路与真实质量未完整验收。（2026-09-29，main 5abdc580；本轮699/699与Ruff通过，范围见本轮产品复核；历史证据见归档。）M5-2b合成交付与未完成项 M5-2b独立QA与Q-27 Q-27开发修复与复验条件 Q-27独立复验及范围  [代码]  [用例/证据] 本轮独立验收与限制 本次修复与契约核对 Q-26最终独立复验 Laya本机使用与开发证据 Laya本地契约 PR #31（已合并） 本轮证据及未完成项M5-3b独立QA与Q-28/Q-29Q-28/Q-29开发修复与复验条件Q-28/Q-29独立复验与Q-30Q-30开发修复与剩余门槛Q-30独立复验与Q-31～33Q-31/Q-32/Q-33开发验证及剩余门槛Q-31～33独立复验及Q-34/Q-35Q-34/Q-35开发验证及剩余门槛Q-34/Q-35独立复验及Q-36/Q-37Q-36/Q-37开发验证及剩余门槛Q-36/Q-37独立复验M54A-E4独立复验及Q-38/Q-39

- [M5-2b合成交付与未完成项](https://github.com/262412/-Whynote/blob/648f586/docs/m5-template-delivery.md)
- [M5-2b独立QA与Q-27](https://github.com/262412/-Whynote/blob/codex/m5-template-confirmation/docs/qa-m5-templates-independent.md)
- [Q-27开发修复与复验条件](https://github.com/262412/-Whynote/blob/c00408b/docs/q27-fix-verification.md)
- [Q-27独立复验及范围](https://github.com/262412/-Whynote/blob/codex/m5-template-confirmation/docs/qa-q27-retest.md)
- [ [代码]](https://github.com/262412/-Whynote/blob/f560c4abe03a6c8579eae95e433766825c50f230/src/whynote/domain.py#L272)
- [ [用例/证据]](https://github.com/262412/-Whynote/blob/f560c4abe03a6c8579eae95e433766825c50f230/tests/test_feedback.py)
- [本轮独立验收与限制](https://github.com/262412/-Whynote/blob/b1c7c76/docs/qa-jev-native-interface.md)
- [本次修复与契约核对](https://github.com/262412/-Whynote/blob/0d2e2af/docs/jev-native-interface.md)
- [Q-26最终独立复验](https://github.com/262412/-Whynote/blob/32fffc2/docs/qa-jev-native-interface.md)
- [Laya本机使用与开发证据](https://github.com/262412/-Whynote/blob/11b67e3/docs/laya-local.md)
- [Laya本地契约](https://github.com/262412/-Whynote/blob/11b67e3/docs/laya-local-contract.md)
- [PR #31（已合并）](https://github.com/262412/-Whynote/pull/31)
- [本轮证据及未完成项](https://github.com/262412/-Whynote/blob/2319885/docs/m53-live-laya-delivery.md)
- [M5-3b独立QA与Q-28/Q-29](https://github.com/262412/-Whynote/blob/codex/m5-live-laya-synthetic/docs/qa-m53-independent.md)
- [Q-28/Q-29开发修复与复验条件](https://github.com/262412/-Whynote/blob/c89d240/docs/q2829-fix-verification.md)
- [Q-28/Q-29独立复验与Q-30](https://github.com/262412/-Whynote/blob/codex/m5-live-laya-synthetic/docs/qa-q28-q29-retest.md)
- [Q-30开发修复与剩余门槛](https://github.com/262412/-Whynote/blob/6aa60a4/docs/q30-fix-verification.md)
- [Q-30独立复验与Q-31～33](https://github.com/262412/-Whynote/blob/codex/m5-live-laya-synthetic/docs/qa-q30-retest.md)
- [Q-31/Q-32/Q-33开发验证及剩余门槛](https://github.com/262412/-Whynote/blob/59a0f68/docs/q313233-fix-verification.md)
- [Q-31～33独立复验及Q-34/Q-35](https://github.com/262412/-Whynote/blob/codex/m5-live-laya-synthetic/docs/qa-q313233-retest.md)
- [Q-34/Q-35开发验证及剩余门槛](https://github.com/262412/-Whynote/blob/84a5a70/docs/q3435-fix-verification.md)
- [Q-34/Q-35独立复验及Q-36/Q-37](https://github.com/262412/-Whynote/blob/codex/m5-live-laya-synthetic/docs/qa-q3435-retest.md)
- [Q-36/Q-37开发验证及剩余门槛](https://github.com/262412/-Whynote/blob/5945460/docs/q3637-fix-verification.md)
- [Q-36/Q-37独立复验](https://github.com/262412/-Whynote/blob/codex/m5-live-laya-synthetic/docs/qa-q3637-retest.md)
- [M54A-E4独立复验及Q-38/Q-39](https://github.com/262412/-Whynote/blob/codex/m54a-controlled-replay/docs/qa-m54a-controlled.md)

## prd / doxc6uHlLLUHo31hjkIzIfpLb9e

2026-09-29独立QA（PR #42/44deb39，输入PRD525/技术509）：√ 原样954/954（含Windows实测3项）、实际原生80/80、探针58/58与Ruff通过。√ 新离线环境的本机IPv4/IPv6 TCP/UDP接收对照、零网络capability、全局互斥、超时/注入取消Job回收与A/B/C三次实际Laya合成调用通过；五处ACL读回一致。√ 固定tokenizer三个合成输入与SDK全部16种问题预算逐项一致。受控压缩文件大小/hash独立核对，200/400/112为读取开发汇总，本轮未展开真实正文或重跑真实材料预算。新增独立9项5通过/4失败：Q-38/P1，三种uncaught_error路径均执行180槽才HOLD，缺立即停止；Q-39/P2，模型已加载后的策略异常仍报model_load_failed。执行器整体未通过，原断言/失败保留。修复后原样复验停止留痕、零后续调用与按实际阶段分类。无真实评测/浏览器/部署/前端构建；本人内容审阅、留出准入、48小时盲复标封存及运行冻结仍待完成。PR保持Draft，依赖未合并PR41；批准/签署、在线宿主边界及生产仍NO-GO。 历史记录（原范围保留）：当前M54A-E4开发状态（2026-09-29，PR #42/f010923，Draft，依赖PR #41/affcc6c；FR-05/06/09/14/15、TD-02/07/08/11/14）：本人已确认HelpSteer3固定版本小文件的有限探索准入；5,275,463字节落在项目受控目录，SHA256一致，最多前200条，30天保留、不另备份。400目标关联及实际tokenizer测量完成，112目标合预算，74源记录至少一个目标合规；全部已审查材料及6个卡片示例排除出留出集。原评价保持evaluator，不是用户点踩动机。预测投影、固定180槽位/无重试/fsync日志和新离线AppContainer执行器已实现；实际A/B/C本机Laya合成调用、接收端网络对照、跨进程互斥及超时/注入取消回收通过。上述为开发证据，待独立工程/环境复验。真实留出准入、本人48小时盲复标封存、近重复/中文配额及运行人执行冻结未就绪；未执行真实评测。在线宿主边界、非作者批准/适用签署、完整FR及生产仍NO-GO。 历史链接（范围以原记录为准）： 新执行器失败映射：SDK推理异常保留失败阶段；未返回完整回执的超时/传输错误记路由结果不可用，不伪称从未尝试、不计条件遗漏。模型未加载/材料预算不符才not_attempted；绑定新源码的环境核验再次通过，三方案合成总耗时约9.36/9.43/9.17秒。审阅输入草案c153fb6（现行参数见D-20） 本轮开发证据及剩余项替换前原条目快照（原版本范围）M54A-E独立工程复验本切片交付新执行契约替换前全文归档M54A-E4独立复验及Q-38/Q-39

- [审阅输入草案c153fb6（现行参数见D-20）](https://github.com/262412/-Whynote/blob/b3d8f6f/docs/m54a-freeze-protocol.md)
- [本轮开发证据及剩余项](https://github.com/262412/-Whynote/blob/d4ed35bbc47554decf19daf39ed7d4310675958c/docs/m54a-self-review-delivery.md)
- [替换前原条目快照（原版本范围）](https://github.com/262412/-Whynote/blob/codex/m54a-evaluation-protocol/qa/evidence/2026-09-29-m54a-self-review/feishu-prior-blocks.json)
- [M54A-E独立工程复验](https://github.com/262412/-Whynote/blob/codex/m54a-evaluation-protocol/docs/qa-m54a-independent.md)
- [本切片交付](https://github.com/262412/-Whynote/blob/197203d/docs/m54a-controlled-delivery.md)
- [新执行契约](https://github.com/262412/-Whynote/blob/197203d/docs/m54a-controlled-execution.md)
- [替换前全文归档](https://github.com/262412/-Whynote/blob/197203d/docs/archive/m54e4-feishu-before.md)
- [M54A-E4独立复验及Q-38/Q-39](https://github.com/262412/-Whynote/blob/codex/m54a-controlled-replay/docs/qa-m54a-controlled.md)

## prd / doxc6k0MndExihe980MVBi4W9jd

2026-09-29独立QA（PR #42/44deb39，输入PRD525/技术509）：√ 原样954/954（含Windows实测3项）、实际原生80/80、探针58/58与Ruff通过。√ 新离线环境的本机IPv4/IPv6 TCP/UDP接收对照、零网络capability、全局互斥、超时/注入取消Job回收与A/B/C三次实际Laya合成调用通过；五处ACL读回一致。√ 固定tokenizer三个合成输入与SDK全部16种问题预算逐项一致。受控压缩文件大小/hash独立核对，200/400/112为读取开发汇总，本轮未展开真实正文或重跑真实材料预算。新增独立9项5通过/4失败：Q-38/P1，三种uncaught_error路径均执行180槽才HOLD，缺立即停止；Q-39/P2，模型已加载后的策略异常仍报model_load_failed。执行器整体未通过，原断言/失败保留。修复后原样复验停止留痕、零后续调用与按实际阶段分类。无真实评测/浏览器/部署/前端构建；本人内容审阅、留出准入、48小时盲复标封存及运行冻结仍待完成。PR保持Draft，依赖未合并PR41；批准/签署、在线宿主边界及生产仍NO-GO。 历史记录（原范围保留）：当前退出条件（2026-09-29，D-20/M54A-E-v0.1数值不变）：探索30（代码20/其他10）、验证60（代码40/其他20），至少30原生中文、10例>1024字节，至少48小时后固定20例盲复标。HelpSteer3本次仅ADMITTED_FOR_REVIEW_ONLY，不能充当ADMITTED留出批次。新增32项开发用例覆盖200条边界、关联/暴露/组/hash、180槽位成功/失败/拒识及真实Windows进程控制；本地完整首批946/946、补充关联6项后的952/952保留；再补失败阶段2项，聚焦29/29通过，最新全量/CI见PR #42。独立工程复验、真实数据/本人标签/环境及运行确认齐备后才能执行固定180次真实评测。仅候选C通过协议全部门槛后准备M5-4b本人对照；自评不改写独立gold，缺分母INCONCLUSIVE，风险HOLD，硬失败FAIL。PR #41已转Ready但未合并，本PR仍Draft；本轮未重跑浏览器/宿主/前端构建，原独立QA结论在历史记录中保留。 历史链接（范围以原记录为准）：审阅输入草案c153fb6（现行参数见D-20） 本轮开发证据及剩余项替换前原条目快照（原版本范围）M54A-E独立工程复验本切片交付新执行契约替换前全文归档M54A-E4独立复验及Q-38/Q-39

- [审阅输入草案c153fb6（现行参数见D-20）](https://github.com/262412/-Whynote/blob/b3d8f6f/docs/m54a-freeze-protocol.md)
- [本轮开发证据及剩余项](https://github.com/262412/-Whynote/blob/d4ed35bbc47554decf19daf39ed7d4310675958c/docs/m54a-self-review-delivery.md)
- [替换前原条目快照（原版本范围）](https://github.com/262412/-Whynote/blob/codex/m54a-evaluation-protocol/qa/evidence/2026-09-29-m54a-self-review/feishu-prior-blocks.json)
- [M54A-E独立工程复验](https://github.com/262412/-Whynote/blob/codex/m54a-evaluation-protocol/docs/qa-m54a-independent.md)
- [本切片交付](https://github.com/262412/-Whynote/blob/197203d/docs/m54a-controlled-delivery.md)
- [新执行契约](https://github.com/262412/-Whynote/blob/197203d/docs/m54a-controlled-execution.md)
- [替换前全文归档](https://github.com/262412/-Whynote/blob/197203d/docs/archive/m54e4-feishu-before.md)
- [M54A-E4独立复验及Q-38/Q-39](https://github.com/262412/-Whynote/blob/codex/m54a-controlled-replay/docs/qa-m54a-controlled.md)

## tech / doxc6lgyQwkQwCeyPmuaYbMhTCb

2026-09-29独立QA（PR #42/44deb39，输入PRD525/技术509）：√ 原样954/954（含Windows实测3项）、实际原生80/80、探针58/58与Ruff通过。√ 新离线环境的本机IPv4/IPv6 TCP/UDP接收对照、零网络capability、全局互斥、超时/注入取消Job回收与A/B/C三次实际Laya合成调用通过；五处ACL读回一致。√ 固定tokenizer三个合成输入与SDK全部16种问题预算逐项一致。受控压缩文件大小/hash独立核对，200/400/112为读取开发汇总，本轮未展开真实正文或重跑真实材料预算。新增独立9项5通过/4失败：Q-38/P1，三种uncaught_error路径均执行180槽才HOLD，缺立即停止；Q-39/P2，模型已加载后的策略异常仍报model_load_failed。执行器整体未通过，原断言/失败保留。修复后原样复验停止留痕、零后续调用与按实际阶段分类。无真实评测/浏览器/部署/前端构建；本人内容审阅、留出准入、48小时盲复标封存及运行冻结仍待完成。PR保持Draft，依赖未合并PR41；批准/签署、在线宿主边界及生产仍NO-GO。 历史记录（原范围保留）：当前M54A-E4开发状态（2026-09-29，PR #42/f010923，Draft，依赖PR #41/affcc6c；FR-05/06/09/14/15、TD-02/07/08/11/14）：本人已确认HelpSteer3固定版本小文件的有限探索准入；5,275,463字节落在项目受控目录，SHA256一致，最多前200条，30天保留、不另备份。400目标关联及实际tokenizer测量完成，112目标合预算，74源记录至少一个目标合规；全部已审查材料及6个卡片示例排除出留出集。原评价保持evaluator，不是用户点踩动机。预测投影、固定180槽位/无重试/fsync日志和新离线AppContainer执行器已实现；实际A/B/C本机Laya合成调用、接收端网络对照、跨进程互斥及超时/注入取消回收通过。上述为开发证据，待独立工程/环境复验。真实留出准入、本人48小时盲复标封存、近重复/中文配额及运行人执行冻结未就绪；未执行真实评测。在线宿主边界、非作者批准/适用签署、完整FR及生产仍NO-GO。 历史链接（范围以原记录为准）： 新执行器失败映射：SDK推理异常保留失败阶段；未返回完整回执的超时/传输错误记路由结果不可用，不伪称从未尝试、不计条件遗漏。模型未加载/材料预算不符才not_attempted；绑定新源码的环境核验再次通过，三方案合成总耗时约9.36/9.43/9.17秒。审阅输入草案c153fb6（现行参数见D-20） 本轮开发证据及剩余项替换前原条目快照（原版本范围）M54A-E独立工程复验本切片交付新执行契约替换前全文归档M54A-E4独立复验及Q-38/Q-39

- [审阅输入草案c153fb6（现行参数见D-20）](https://github.com/262412/-Whynote/blob/b3d8f6f/docs/m54a-freeze-protocol.md)
- [本轮开发证据及剩余项](https://github.com/262412/-Whynote/blob/d4ed35bbc47554decf19daf39ed7d4310675958c/docs/m54a-self-review-delivery.md)
- [替换前原条目快照（原版本范围）](https://github.com/262412/-Whynote/blob/codex/m54a-evaluation-protocol/qa/evidence/2026-09-29-m54a-self-review/feishu-prior-blocks.json)
- [M54A-E独立工程复验](https://github.com/262412/-Whynote/blob/codex/m54a-evaluation-protocol/docs/qa-m54a-independent.md)
- [本切片交付](https://github.com/262412/-Whynote/blob/197203d/docs/m54a-controlled-delivery.md)
- [新执行契约](https://github.com/262412/-Whynote/blob/197203d/docs/m54a-controlled-execution.md)
- [替换前全文归档](https://github.com/262412/-Whynote/blob/197203d/docs/archive/m54e4-feishu-before.md)
- [M54A-E4独立复验及Q-38/Q-39](https://github.com/262412/-Whynote/blob/codex/m54a-controlled-replay/docs/qa-m54a-controlled.md)

## tech / doxc6riEXLz59ZbMBs6kje3JgEb

2026-09-29独立QA（PR #42/44deb39，输入PRD525/技术509）：√ 原样954/954（含Windows实测3项）、实际原生80/80、探针58/58与Ruff通过。√ 新离线环境的本机IPv4/IPv6 TCP/UDP接收对照、零网络capability、全局互斥、超时/注入取消Job回收与A/B/C三次实际Laya合成调用通过；五处ACL读回一致。√ 固定tokenizer三个合成输入与SDK全部16种问题预算逐项一致。受控压缩文件大小/hash独立核对，200/400/112为读取开发汇总，本轮未展开真实正文或重跑真实材料预算。新增独立9项5通过/4失败：Q-38/P1，三种uncaught_error路径均执行180槽才HOLD，缺立即停止；Q-39/P2，模型已加载后的策略异常仍报model_load_failed。执行器整体未通过，原断言/失败保留。修复后原样复验停止留痕、零后续调用与按实际阶段分类。无真实评测/浏览器/部署/前端构建；本人内容审阅、留出准入、48小时盲复标封存及运行冻结仍待完成。PR保持Draft，依赖未合并PR41；批准/签署、在线宿主边界及生产仍NO-GO。 历史记录（原范围保留）：当前退出条件（2026-09-29，D-20/M54A-E-v0.1数值不变）：探索30（代码20/其他10）、验证60（代码40/其他20），至少30原生中文、10例>1024字节，至少48小时后固定20例盲复标。HelpSteer3本次仅ADMITTED_FOR_REVIEW_ONLY，不能充当ADMITTED留出批次。新增32项开发用例覆盖200条边界、关联/暴露/组/hash、180槽位成功/失败/拒识及真实Windows进程控制；本地完整首批946/946、补充关联6项后的952/952保留；再补失败阶段2项，聚焦29/29通过，最新全量/CI见PR #42。独立工程复验、真实数据/本人标签/环境及运行确认齐备后才能执行固定180次真实评测。仅候选C通过协议全部门槛后准备M5-4b本人对照；自评不改写独立gold，缺分母INCONCLUSIVE，风险HOLD，硬失败FAIL。PR #41已转Ready但未合并，本PR仍Draft；本轮未重跑浏览器/宿主/前端构建，原独立QA结论在历史记录中保留。 历史链接（范围以原记录为准）：审阅输入草案c153fb6（现行参数见D-20） 本轮开发证据及剩余项替换前原条目快照（原版本范围）M54A-E独立工程复验本切片交付新执行契约替换前全文归档M54A-E4独立复验及Q-38/Q-39

- [审阅输入草案c153fb6（现行参数见D-20）](https://github.com/262412/-Whynote/blob/b3d8f6f/docs/m54a-freeze-protocol.md)
- [本轮开发证据及剩余项](https://github.com/262412/-Whynote/blob/d4ed35bbc47554decf19daf39ed7d4310675958c/docs/m54a-self-review-delivery.md)
- [替换前原条目快照（原版本范围）](https://github.com/262412/-Whynote/blob/codex/m54a-evaluation-protocol/qa/evidence/2026-09-29-m54a-self-review/feishu-prior-blocks.json)
- [M54A-E独立工程复验](https://github.com/262412/-Whynote/blob/codex/m54a-evaluation-protocol/docs/qa-m54a-independent.md)
- [本切片交付](https://github.com/262412/-Whynote/blob/197203d/docs/m54a-controlled-delivery.md)
- [新执行契约](https://github.com/262412/-Whynote/blob/197203d/docs/m54a-controlled-execution.md)
- [替换前全文归档](https://github.com/262412/-Whynote/blob/197203d/docs/archive/m54e4-feishu-before.md)
- [M54A-E4独立复验及Q-38/Q-39](https://github.com/262412/-Whynote/blob/codex/m54a-controlled-replay/docs/qa-m54a-controlled.md)

## tech / doxc6RHMtFWQLlTdaCR8RaGh53b

2026-09-29独立QA（PR #42/44deb39）：√ 新离线AppContainer所测网络/互斥/回收及三次实际Laya合成通过，原样954/954、原生80/80、探针58/58。Q-38/P1阻断：uncaught_error未立即停止，三种返回方式均继续180槽；Q-39/P2阻断：已加载模型后的策略异常误记model_load_failed。新增9项5通过/4失败，修复方须保留断言复验。在线宿主与完整FR未验收，真实评测未开启，生产NO-GO。 历史记录（原范围保留）：2026-09-29独立QA（PR #40/56ec110）：√ Q-36非法错误类型固定invalid_response，动作与Outbox保留且回退常规菜单；Q-37启动隔离及真实轻量子进程超时/取消回收通过。两次真实本机合成调用结束；未将-I等同OS出站控制。 详见本轮独立报告；完整FR/切片验收、非作者批准及适用签署未完成，生产NO-GO。 历史开发及独立记录（原范围保留）：2026-09-29开发修复（PR #40/5945460，待独立复验）：Q-36错误信封只接收固定集合内字符串，数组/对象/null/数字/布尔/未知字符串均invalid_response；无正文泄漏。Q-37保留8192字节输入、60秒超时、取消时kill/wait，真实轻量子进程回收通过；输入预算/SDK/权重校验不变。 完整841/841与Ruff通过；运行边界/批准/签署仍阻断，生产NO-GO。 原要求与历史证据保留：2026-09-29独立QA（PR #40/3ae7f30）：√ Q-33四种预检失败实机仍零写入且同实例修正成功。新增Q-36错误码类型、Q-37worker模块解析缺口；预检通过不证明实际worker来源或环境级出站隔离。 详见本轮独立报告；完整FR/切片验收、非作者批准及适用签署未完成，生产NO-GO。 历史开发及独立记录（原范围保留）：2026-09-29开发修复（PR #40/84a5a70，待独立复验）：源码准入/清单修复不改变固定SDK/五文件模型预检、输入预算及失败码。原Q-31～33指定范围独立通过；出站隔离与跨进程并发仍开放。 完整816/816与Ruff通过；运行边界/批准/签署仍阻断，生产NO-GO。 原要求与历史证据保留：2026-09-29独立QA（PR #40/8e73c7f）：√ 固定失败码及模型预检指定范围独立通过；SDK/五文件摘要及超时边界回归通过，实际新宿主验证拒绝零写入。无模型推理/质量评测新结论；环境级出站与并发仍开放。 详见本轮独立报告；完整FR/切片验收、非作者批准及适用签署未完成，生产NO-GO。 历史开发及独立记录（原范围保留）：2026-09-29开发修复（PR #40/59a0f68，待独立复验）：非法结构及任务/理由/拒识语义冲突固定invalid_response；子进程预检失败不外显正文。出站隔离与跨进程单并发保持开放待验。 完整802/802与Ruff通过；运行边界/批准/签署仍阻断，生产NO-GO。 原要求与历史证据保留：2026-09-29独立QA（PR #40/f83a948）：√ Q-30聚焦87/87及交叉边界8/8；Q-32模型语义非法时常规菜单和动作保留，但缺固定失败码。出站隔离与跨进程并发仍待处理；本轮未跑实际模型或质量评测。 详见本轮独立报告；完整FR/切片验收、非作者批准及适用签署未完成，生产NO-GO。 历史开发及独立记录（原范围保留）：2026-09-29 Q-30开发修复（PR #40/6aa60a4，待独立复验）：事件库对称比较生成时与当前的全部准入/版本字段；当前后端不一致时旧render/respond及其库重试拒绝、零追加，双向切换生效。旧fixture无live字段仍兼容，同版本重试幂等；历史确认/事件/Outbox保留，新fixture可用。原两条独立失败未修改，修复后通过；针对性87/87、完整767/767与Ruff通过。本轮未重跑浏览器/模型。Q-28/Q-29既有指定技术范围独立通过；Q-30仍待独立复验，出站隔离/进程并发、非作者批准及签署仍阻断，未合并，生产NO-GO。此前独立复验与失败原文保留：2026-09-29独立复验（PR #40/5add19b，输入PRD432/技术426）：✔ Q-28/Q-29指定技术范围通过；原15项未改、原样756/756，实际原生80/80、探针58/58及Ruff通过。✔ 三种缺参均零宿主写入，补参同实例成功；6种源码/构建变更启动前拒绝且不建库，恢复后校验通过；本轮独立登录浏览器＋实际Laya＋新SQLite确认通过，4事件/1Outbox。前端为校验完整5604产物的开发构建副本，未重建。新增Q-30/P2：事件库直接入口在live切fixture后仍追加旧live渲染/确认；两条独立用例失败，全套756通过/2失败。Action全配置复查仍能挡住所测宿主路径，不称浏览器权限绕过。出站/并发评审机制已核实：connect拦截仍允许本机UDP，evaluate可并行启动2个真实轻量子进程；未外发数据或制造GPU OOM，环境隔离/并发门槛仍开放。独立切片验收未完成，非作者批准及适用签署仍缺；未合并，生产NO-GO。 历史开发记录（原版本范围保留）：2026-09-29 Q-28/Q-29研发修复（PR #40/c89d240，待独立复验）：provision在宿主写入前拒绝缺失/无效模型参数；实际新实例两种缺参均零写入，补参数同实例成功。build成功后绑定源码与完整5604产物；serve拒绝旧/缺失/篡改产物，实际HTML/JS改写均在创建数据目录前拒绝。原独立15项未改且全通过，新增14项、完整756项及Ruff通过；Node22全量构建和新构建浏览器实际Laya确认通过。业务事件/模型策略不变。Q-28/Q-29未独立关闭；非作者批准与适用签署仍缺，未合并，生产NO-GO。此前独立证据及失败原文保留：2026-09-29 M5-3b独立QA（e1c4410）：✔ 实际缺原代码返回unknown；无坐标只显示类别。真实OS子进程取消/加速超时回收通过；环境级出站控制与真实材料矩阵未验收。 Q-28/Q-29开放，非作者批准及适用签署待完成；整体NO-GO。 历史开发记录（原版本范围保留）：当前M5-3b研发状态（2026-09-29，PR #40/2319885，未合并；完整FR未验收）：缺原代码沿用证据门控，实际模型无可靠坐标则类别降级。真实问答只在本机进程stdin短期传递，固定错误码无正文；超时/取消终止子进程。真实质量/材料矩阵仍待M5-4a。 原要求与历史证据保留：✔ task_reasons.py按证据种类筛选候选，replay.py保留材料不足/unknown/no_match及超长错误。4代码＋7通用理由只为开发种子；真实语料覆盖、缺材料/长代码拒绝率及partial-feature质量待M5-3a/4a。 ✔ 固定Laya本机三方案runner、失败状态与合成模板回退已实现；本轮实际33条回放和699项回归通过。replay.py/replay_laya.py、template_action.py。实时Laya→新模板确认尚缺M5-3b；熔断/生产链路与真实质量未完整验收。（2026-09-29，main 5abdc580；本轮699/699与Ruff通过，范围见本轮产品复核；历史证据见归档。）M5-2b合成交付与未完成项 M5-2b独立QA与Q-27 Q-27独立复验及范围 本轮证据及未完成项M5-3b独立QA与Q-28/Q-29Q-28/Q-29开发修复与复验条件Q-28/Q-29独立复验与Q-30Q-30开发修复与剩余门槛Q-30独立复验与Q-31～33Q-31/Q-32/Q-33开发验证及剩余门槛Q-31～33独立复验及Q-34/Q-35Q-34/Q-35开发验证及剩余门槛Q-34/Q-35独立复验及Q-36/Q-37Q-36/Q-37开发验证及剩余门槛Q-36/Q-37独立复验M54A-E4独立复验及Q-38/Q-39

- [M5-2b合成交付与未完成项](https://github.com/262412/-Whynote/blob/648f586/docs/m5-template-delivery.md)
- [M5-2b独立QA与Q-27](https://github.com/262412/-Whynote/blob/codex/m5-template-confirmation/docs/qa-m5-templates-independent.md)
- [Q-27独立复验及范围](https://github.com/262412/-Whynote/blob/codex/m5-template-confirmation/docs/qa-q27-retest.md)
- [本轮证据及未完成项](https://github.com/262412/-Whynote/blob/2319885/docs/m53-live-laya-delivery.md)
- [M5-3b独立QA与Q-28/Q-29](https://github.com/262412/-Whynote/blob/codex/m5-live-laya-synthetic/docs/qa-m53-independent.md)
- [Q-28/Q-29开发修复与复验条件](https://github.com/262412/-Whynote/blob/c89d240/docs/q2829-fix-verification.md)
- [Q-28/Q-29独立复验与Q-30](https://github.com/262412/-Whynote/blob/codex/m5-live-laya-synthetic/docs/qa-q28-q29-retest.md)
- [Q-30开发修复与剩余门槛](https://github.com/262412/-Whynote/blob/6aa60a4/docs/q30-fix-verification.md)
- [Q-30独立复验与Q-31～33](https://github.com/262412/-Whynote/blob/codex/m5-live-laya-synthetic/docs/qa-q30-retest.md)
- [Q-31/Q-32/Q-33开发验证及剩余门槛](https://github.com/262412/-Whynote/blob/59a0f68/docs/q313233-fix-verification.md)
- [Q-31～33独立复验及Q-34/Q-35](https://github.com/262412/-Whynote/blob/codex/m5-live-laya-synthetic/docs/qa-q313233-retest.md)
- [Q-34/Q-35开发验证及剩余门槛](https://github.com/262412/-Whynote/blob/84a5a70/docs/q3435-fix-verification.md)
- [Q-34/Q-35独立复验及Q-36/Q-37](https://github.com/262412/-Whynote/blob/codex/m5-live-laya-synthetic/docs/qa-q3435-retest.md)
- [Q-36/Q-37开发验证及剩余门槛](https://github.com/262412/-Whynote/blob/5945460/docs/q3637-fix-verification.md)
- [Q-36/Q-37独立复验](https://github.com/262412/-Whynote/blob/codex/m5-live-laya-synthetic/docs/qa-q3637-retest.md)
- [M54A-E4独立复验及Q-38/Q-39](https://github.com/262412/-Whynote/blob/codex/m54a-controlled-replay/docs/qa-m54a-controlled.md)

