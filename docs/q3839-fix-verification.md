# Q-38 / Q-39 开发修复回执

2026-09-29；FR-05/06/09/14/15、TD-02/07/08/11/14、D-20。
输入：PR #42 / QA提交8a9320d，独立基线44deb39；飞书PRD528／技术512。
Q-38/P1与Q-39/P2已开发修复，待独立原样复验、非作者批准及适用签署。PR保持Draft，生产NO-GO。

## 复现与改动

原[独立报告](https://github.com/262412/-Whynote/blob/8a9320d/docs/qa-m54a-controlled.md)、9条独立断言和原始失败证据保持不变。
修复前失败[CI36554882934](https://github.com/262412/-Whynote/actions/runs/36554882934)与本机修复前复现一致：5通过/4失败。

- Q-38：错误信封、ReplayError和完整prediction统一记录首个uncaught_error结果，fsync之后追加stopped并抛出固定错误；后续零调用、零started，不生成完整bundle/report。completed_slots包括已落盘的失败槽；不自动续跑或覆盖。普通timeout/model_load_failed/route_failed/invalid_response/worker_failed仍按计划继续。
- Q-39：只在tokenizer/SDK/模型加载与配置核验期间使用model_load_failed；加载完成后的随机种子初始化、材料准备、策略未知异常用uncaught_error，交由监督器立即停止。tokenizer加载后的测量异常也不再冒充加载失败。SDK预测原有worker_failed语义不变，异常正文不进入JSON或journal。
- 兼容：沿用journal原字段；停止记录的error新增实际已有固定分类uncaught_error，旧记录不重写。统计报告、独立标签及产品事件契约不变。

## 开发验证

重装锁定非editable包后，原9条独立用例9/9；新增16条边界通过，连同既有相关用例54/54。Ruff check/format通过（118文件）。
新增用例覆盖第7/179次错误的三种形式、持久化顺序和不可覆盖、五种计划内错误继续、实际worker.main到监督器的策略/初始化异常联动、tokenizer加载/测量阶段区分。
固定修复提交 `c1aecae120c6c33eefdfc8be12346549077598fb`：本机 `uv run --no-sync pytest tests qa -q` 完整 **979/979**（129.13秒，1条既有Starlette弃用警告）。

[修复CI36555733374](https://github.com/262412/-Whynote/actions/runs/36555733374)质量及原生任务通过：核心936通过/3个Windows专用跳过，保留S0 9/9，manual 31/31；原生80/80、探针58/58。后两项为本轮CI执行，不冒充本机重跑。

[实际环境回执](evidence/q3839-environment.json)绑定修复后源码与固定运行时：VERIFIED_FOR_FREEZE；本机IPv4/IPv6 TCP/UDP正对照送达、沙箱均未送达、零网络capability；第二进程互斥拒绝；超时和注入取消后Job归零，下一进程可成功；三次实际Laya/CUDA合成A/B/C均ok。它是开发者重新核验的指定技术范围，不是外部UDP接收实验、在线宿主边界或完整安全认证；也不等于已批准执行冻结。旧环境回执保留，不能替代当前源码的回执。

失败、聚焦和完整测试原始日志保留于工作树 `var/q3839/`，修复前CI日志在 `var/q3839-ci-before.log`；无真实上下文进入本轮调用。

## 当前基线与剩余项

PR #41已于2026-09-29合并main5896325；PR #42目标分支已是main，无需再等待41合并。旧独立报告中的依赖说明是其执行时状态，原文保留。
本轮继续原PR/分支/工作树，不变更在线宿主、浏览器或前端构建；不展开真实材料、不启动真实评测或本人试用。
独立QA原样复验Q-38/Q-39、获准留出批次、本人至少48小时盲复标与封存、运行冻结、非作者批准及适用签署仍待完成。
原工作区代码、研究材料、审计数据和失败日志保留；本PR仍在评审使用，分支与工作树不清理。

## 文档同步

飞书原FR-06、TD-07及两份M54A-E4行动/退出条目局部替换，逐条精确读回：PRD528→531、技术512→515；六处历史链接全部保留，原文见[归档](archive/q3839-feishu-before.md)，[读回回执](evidence/q3839-feishu-readback.json)。更新明确为开发修复，未写作独立验收或生产批准。
