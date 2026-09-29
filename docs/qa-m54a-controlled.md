# M54A-E4 独立 QA：所测环境通过，执行器阻塞

日期：2026-09-29。候选 **PR #42 / `44deb39282aeb09f0ef9153764afc7baee7a36f8`**。
结论：原样回归与指定离线环境检查通过；新增 **Q-38/P1、Q-39/P2**，执行器整体未通过。真实评测未开启，生产 NO-GO。

## 基线和范围

- 目标分支为 `codex/m54a-evaluation-protocol`，依赖 PR #41 / `affcc6c`；本轮核对两 PR 均未合并。main 仍为 `1d2abd7c876de4a856c712c4fb00b558976b5eb6`。PR #42 保持 Draft，不能先于 #41 合并。
- 文档输入：飞书 PRD 525／技术 509；按 D-20/M54A-E-v0.1、FR-05/06/09/14/15、TD-02/07/08/11/14 核对[执行契约](m54a-controlled-execution.md)、[准入记录](helpsteer3-review-admission.md)与[开发回执](m54a-controlled-delivery.md)。
- 使用空闲 QA detached 工作树，重装锁定非 editable 包；原目录、开发目录、审计库、缓存和已有模型均保留。候选 [CI 36552230087](https://github.com/262412/-Whynote/actions/runs/36552230087) 两项通过；新独立断言揭示的失败另记，不能用候选绿灯覆盖。

## 本轮测试

| 执行 | 实际结果 | 证据范围 |
|---|---|---|
| 原样核心＋保留 S0/manual QA | **954/954**，122.24 秒 | 含开发新增32项和本机Windows隔离3项；原断言未改 |
| 新独立用例 | **5通过／4失败** | 9项；Q-38三条失败，Q-39一条失败 |
| 实际 Open WebUI 原生 ORM/FastAPI | **80/80** | 固定v0.11.4 / 8bd8b4f，新虚构数据 |
| 原生契约探针 | **58/58** | 全新SQLite，权限、关联、删除和副作用 |
| Ruff：src/tests/integrations/qa | 通过 | 117文件格式检查 |
| 实际离线环境CLI | 所测检查通过 | AppContainer网络、互斥、Job回收、3次Laya/CUDA合成推理 |
| 实际固定tokenizer交叉核验 | **3/3** | 合成输入；每例与SDK全部16种问题预算比较 |

原样954项与新增9项分开执行，合计959通过／4失败。原生和预算探针不重复计入。Linux CI跳过Windows专用3项，本轮在Windows实际执行。

### Q-38／P1：未捕获错误未立即停止后续槽位

**契约：** [协议§4～5](m54a-freeze-protocol.md)要求未捕获异常或未记录失败立即停止；[新执行契约](m54a-controlled-execution.md)要求停止后未尝试槽位保持缺失。`self_review_metrics.gates` 将 `uncaught_error` 视为全局停止事件。

**操作：** 合成60例／180槽位经真实 `execute_slots`，首槽及其后调用分别由三种形式返回同一错误：`{"error":"uncaught_error"}`、`ReplayError("uncaught_error")`、完整prediction中 `error=uncaught_error`。不替换循环、journal、报告或fsync；没有真实模型调用。

**预期：** 首次发现该错误就保留失败、追加停止记录，不再调用后续槽位，不生成完整180槽位结果。

**实际：** 三种形式均调用 **180次**，journal均为180条started＋180条completed、**0条stopped**；180条错误被保留，但仍写完整bundle，最后报告才给 `HOLD / stop_required=true`。离线报告中的停止标志未转为执行器的及时停止。

**定位：** `controlled_replay.execute_slots` 将整个 `ERRORS` 集合作为可继续异常，并在生成prediction后无停止类别检查；`uncaught_error` 在集合中。原生隔离通过不能修复此控制流问题。

**复验：** 三条原断言保持；须核对首个失败可追溯、后续零调用／零started、不伪造完成或完整bundle；普通timeout／worker_failed等计划内错误继续按既有契约保留并运行。责任角色：离线执行器维护者。

### Q-39／P2：加载完成后的策略异常被误记为模型加载失败

**契约：** [执行契约“失败阶段”](m54a-controlled-execution.md)规定模型尚未加载才使用 `model_load_failed`；未捕获异常应进入停止语义。

**操作：** 通过实际 `controlled_worker.main` 注入两个阶段：加载函数抛异常；以及模型已成功返回、device/cfg核验通过后，`infer_scheme` 抛出未知RuntimeError。仅替换SDK/torch依赖和故障点，无模型或真实数据；入口解析、阶段顺序、异常分类和JSON输出不替换。

**预期／实际：** 加载阶段正确输出 `model_load_failed`；策略阶段也输出了 `model_load_failed`，预期应保留为未捕获执行错误（既有 `uncaught_error`）。私有异常正文未输出，该子项通过。

**影响和定位：** `controlled_worker.main` 最外层 `except Exception` 将加载前后所有未知异常归为加载失败。监督器继而记 `route_status=not_attempted` 并允许继续，掩盖真实失败阶段。

**复验：** 保持加载失败正向对照；对已加载后的策略故障保留正确分类、无正文回显，并经执行器验证立即停止。SDK可预期推理异常的既有 `worker_failed` 规则保持。责任角色：模型worker维护者。

## √ 指定离线环境的独立实机结果

实际运行 `whynote.controlled_environment`，使用固定本机Laya运行时和模型、QA候选包、新空scratch；未使用真实研究材料。

- 源码指纹 `1923f89a931b9b1a10d8130cd4fc1bd339d596448632290ff4763190b61cab34`、运行时指纹 `0fd2fd59eeb1df3d31ba6b00239960b07039193248b520b52842bc7b4f9a3e8f` 与开发固定回执一致。
- 普通子进程向本机IPv4/IPv6 TCP/UDP四个受控接收端均到达；AppContainer四项均未到达。实际token `appcontainer=true`、`capability_count=0`，文档地址TCP为10013。未开展外部UDP接收实验，不扩大为全部网络/安全认证。
- 持有全局mutex时第二真实进程被拒绝；超时及等待处注入KeyboardInterrupt后Job活跃数归零，后续进程成功。不是人工控制台取消证据。
- 同一合成输入的A/B/C三次真实Laya/CUDA调用均返回ok；包含启动／加载约 **10.006／9.906／9.758秒**。它们不代表真实180次评测、p95达标或模型质量改善。
- 五处临时读授权根（包、运行时、基础解释器、模型、scratch）的前后ACL读回完全相同；临时AppContainer授权撤回。Windows用例还实测旁侧合成私有文件不可读、scratch不可写。

本次资格CLI返回 `VERIFIED_FOR_FREEZE`，仅表示该工具的环境检查成功；Q-38/Q-39及真实数据／封存／签署缺口仍阻断实际冻结和评测。在线宿主未使用此执行器，其运行边界缺口保持。

## √ 投影／预算及受控文件核对范围

- 独立读取压缩字节核对大小 **5,275,463** 与SHA-256 `b86f71581a83f610ec5416029422f84ff1fc005e77b56c7634486ef9f6f12ceb`，一致；没有解压或展开真实正文，没有生成本人内容标签。
- 读取开发无正文汇总：前200条、400目标、112合预算、最大7371输入token／98 head token、6个original_code、evaluator来源、model_calls=0。**这些数量是开发留存汇总，本轮没有独立重跑400目标的真实预算测量或内容筛查。**
- 本轮实际固定tokenizer对三个合成输入交叉核验：Unicode代码样本18 token合格；900 token样本因input_tokens_exceeded拒绝；保留特殊token样本因reserved_token拒绝。每例测量值与既有SDK全部16种问题的预算判断一致，无模型推理；Job已回收。
- 原样合成用例通过：双回答选择、另一回答／评价隔离、200行读取边界、规范化暴露、目标／组／输入／排除清单摘要绑定、review-only拒绝真实执行。未据此宣布本人近重复或敏感内容审阅完成。

## 文档与证据

飞书原M5-4a-E行动／退出及FR-06、TD-07条目局部更新，使用最新revision-id、逐次精确读回，历史归档和旧链接保留。最终 **PRD 528／技术 512**；[写入回执](../qa/evidence/2026-09-29-m54a-controlled/feishu-writes.json)。

提交证据位于 `qa/evidence/2026-09-29-m54a-controlled/`：汇总、Q-38三路径journal统计、Q-39阶段结果、去路径环境回执、token预算对账、ACL一致结果、原生逐项结果和文档回执。
本地 `var/qa-m54a-controlled/` 保留JUnit、原始日志、完整新合成journal、探针脚本和虚构数据库。没有提交源正文、标签、认证材料或缓存。

**下一步：** 开发修复Q-38/Q-39后原样复验，不删断言或把错误降为可继续；本人内容审阅、新留出批次准入、至少48小时盲复标封存及责任人冻结仍待完成。非作者批准和适用签署未补，PR #42继续Draft；依赖顺序先41后42。本轮未执行浏览器、宿主部署、前端构建或真实评测。生产继续NO-GO，分支／工作树／历史数据保留。
