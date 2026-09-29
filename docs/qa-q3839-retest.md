# Q-38／Q-39 独立复验

2026-09-29；固定候选 **PR #42 / `51b8ff1a3a8d2f1b77bc5c3f6e954ab65616b86d`**。
**两项缺陷在本轮所测技术范围内通过，未发现新的阻断缺陷。** 真实评测、完整FR验收和生产批准未完成。

## 基线与契约

- main 为 `589632559221ee05579595bb6db8f055a803796e`，PR #41已合并。PR #42目标main、保持Draft，未取得非作者APPROVED。
- 飞书输入PRD531／技术515；核对FR-05/06/09/14/15、TD-02/07/08/11/14、D-20及[执行契约](m54a-controlled-execution.md)。停止须保留失败、后续未尝试保持缺失；只有加载/配置阶段才记model_load_failed。
- QA工作树在原8a9320d干净、无测试进程占用后复用；固定候选并重新安装锁定非editable包。原目录、真实研究材料、开发工作树及旧审计数据未修改。
- `git diff 8a9320d 51b8ff1 -- tests/test_controlled_replay_independent.py` 为空；原9条失败/正向断言完全未改。[上轮失败报告](qa-m54a-controlled.md)和[失败证据](../qa/evidence/2026-09-29-m54a-controlled/)保留。
- 候选[CI 36556374869](https://github.com/262412/-Whynote/actions/runs/36556374869)实际日志：核心936通过、3项Windows跳过，S0 9、manual31、原生80、探针58通过。本轮本机实际执行Windows专用测试。

## 本轮实际执行

| 检查 | 结果 | 范围 |
|---|---|---|
| 原样完整回归 `pytest tests qa` | **979/979**，132.93秒 | 包含原独立9项、开发新增16项及Windows3项 |
| 原独立9项＋新独立6项 | **15/15**，4.02秒 | 原断言不改，新增首槽/末槽的真实fsync顺序及不可覆盖检查 |
| 固定Open WebUI v0.11.4原生回归 | **80/80** | 实际ORM、FastAPI权限/关联/删除路径，新虚构数据库 |
| 原生契约探针 | **58/58** | 全新SQLite，固定上游8bd8b4f及既有四补丁/显式dialog |
| Ruff lint／format | 通过，119文件 | src/tests/integrations/qa |
| 实际离线环境 | 指定范围通过 | 当前源码指纹、网络正负对照、mutex、Job回收、Laya A/B/C合成调用 |

前两行分两次执行，原9项重复计数；去重为**985项通过**，不写作单次985全套。新增用例文件为[tests/test_controlled_stop_independent.py](../tests/test_controlled_stop_independent.py)。没有业务代码修改。

## √ Q-38：立即停止与持久化

**操作：** 原样三条用例经真实execute_slots分别返回错误信封、ReplayError和完整prediction。另在首槽与第180槽注入这三种错误；观察os.fsync并委托真实系统调用，没有替换日志写入或停止逻辑。

**预期／实际一致：** 原首槽用例均仅调用1次；新首槽/末槽分别调用1／180次，实际落盘顺序均为重复的started→completed，最后stopped。首个uncaught_error在stopped之前完成fsync；stopped保留同一错误分类，completed_slots包括已落盘的失败槽。最后一槽也触发停止，不生成bundle.json或report.json。再次使用同一输出目录抛FileExistsError，调用数及目录内全部文件字节保持不变。

开发边界用例亦独立重跑：第7／179次停止三种形式、五种计划内错误继续至180槽、实际worker策略/初始化故障联动均通过。timeout、model_load_failed、route_failed、invalid_response、worker_failed继续保留计划内结果，没有被改成全局停止。

## √ Q-39：按真实阶段分类

**操作：** 原样worker.main故障注入，分别在模型加载时和已加载后的infer_scheme策略阶段抛未知RuntimeError；实际入口解析、异常处理和JSON输出不替换，SDK/torch和故障点为合成替身。

**预期／实际一致：** 加载阶段输出model_load_failed；已加载后策略阶段输出uncaught_error，私有异常正文不进入stdout/stderr。原9项全部通过。开发联动/阶段用例本轮实际重跑亦通过：随机种子初始化、策略错误进入监督器停止；tokenizer加载失败仍为加载错误，加载后的测量异常进入uncaught_error。

故障阶段测试使用替身，不能写成实际Laya崩溃注入。实际Laya成功路径在以下隔离环境中另测。

## √ 当前源码的离线环境实机核验

调用实际controlled_environment.qualify，以新空scratch、固定本机运行时/模型、当前候选非editable包执行，仅使用合成输入。

- 源码SHA-256：`2ca987447c0f95d583a558a00980be78c84673f244490524245589942963a7da`；运行时：`0fd2fd59eeb1df3d31ba6b00239960b07039193248b520b52842bc7b4f9a3e8f`。不借用修复前源码的资格回执。
- 普通子进程向本机IPv4/IPv6 TCP/UDP四个接收端均到达；AppContainer均未到达。实际token为AppContainer、capability_count=0，文档地址TCP为10013。未执行外部UDP接收实验，不扩大为完整安全认证。
- 持有全局mutex时第二真实进程被拒绝；超时及等待处注入KeyboardInterrupt后Job归零，下一进程成功。该取消证据不等于人工控制台取消。
- A/B/C三次实际Laya/CUDA合成调用均ok，包含启动/加载分别约9.545／9.530／9.328秒；不是180次真实评测、p95或模型质量证据。
- 包、运行时、基础解释器、模型、scratch五处临时ACL前后SDDL完全一致。进程结束，未遗留测试服务。

工具返回VERIFIED_FOR_FREEZE表示其指定环境检查成功；实际执行冻结仍需批次、封存和运行人确认。该离线执行器结果不覆盖在线宿主的出站与并发边界。

## 文档与剩余门槛

飞书FR-06、TD-07及两份M54A-E4行动/退出原条目按最新revision-id局部更新，六次精确读回 **PRD534／技术518**。替换前条目已快照，历史归档和所有旧链接保留。逐项结果与写入回执见[本轮证据](../qa/evidence/2026-09-29-q3839-retest/)；本机原始日志/JUnit留在`var/qa-q3839-retest/`。

Q-38/Q-39从指定技术修复待办退出；仍缺本人内容审阅、新留出批次准入、至少48小时盲复标与标签封存、运行确认/冻结、非作者有效批准及适用责任人结果签署。PR继续Draft且未合并，不代签或追认完整验收。

本轮未展开真实研究正文、重跑400目标预算或启动真实评测；未执行浏览器、前端构建、宿主部署和云模型调用。真实数据目录及旧失败证据保留，生产继续**NO-GO**。
