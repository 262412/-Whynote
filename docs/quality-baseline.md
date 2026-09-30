# 知因・Whynote 质量与验收基线

以下状态基于 `9246dd5` 的原证据及主干 `9f467cf` 的窗口诊断；最近保存的飞书读回为 2026-09-30 的 PRD595 / 技术595。本次核实 PR #43、#45 已合并，但未重新读取飞书，也未修改需求或事件/字段/状态语义；新修订须按编号重新核对。

## 当前门槛与责任

| 范围 | 已有证据 | 尚缺 / 责任 |
| --- | --- | --- |
| PR #43、Q-40 和合成案例 UI | PR #43 已合入 `25754a5`；`382c95a` 原两条独立断言及10条边界通过；完整1039、原生80、探针58、隔离 Laya 发送2/1/0及合成 UI/HTTP通过 | 合并不代签批准；最新提交 CI、非作者有效批准及适用签署分别核验；不称完整 FR |
| PR #45 窗口与判别诊断 | 已合入 `9f467cf`；三窗理论覆盖1926/2616/2853；65请求逐token无截断；作者1052唯一用例及Ruff通过 | 一次修正后短例仍误判，停止Laya调参；新研究路径缺独立QA及质量签署。22目标Jev对照的API/费用/真实数据出站未授权，调用0；默认窗口/正式参数待决策 |
| 无标签探索 D-21 | 历史3000槽：748建议/208拒识/1960超限/84跳过，技术失败0；956可运行。取消恢复、常驻隔离与合成耐久在各自固定版本执行 | 不产生准确率、gold或质量 PASS；本人/产品裁决输入投影、路由及理由包下一版 |
| 首轮案例诊断 | 48目标/48组探索审阅；748建议中610全候选yes，短输入亦有反例；事实/时效理由因缺reference过滤 | 助手审阅不是本人签署或独立标签；unknown不等于理由缺失，超长与敏感过滤保留分母 |
| 正式质量 M5-4a | self_review/controlled_replay工程能力和合成报告有固定证据 | 本人/研究责任人落实新未暴露组、理由包、盲标/48小时复标、阈值/样本/环境及运行前签署；单人来源不得冒充独立gold |
| 本人效用 M5-4b、生产发布 | 常规菜单、模型建议、用户确认与来源可分别对账 | 本人/产品冻结常规菜单对照和主指标/停止条件；生产上下文、auto-attach、自由文本SLM、训练导出保持关闭 |
| 历史治理与宿主/供应商边界 | 已合并代码、CI和指定技术子项分别有证据 | 非作者批准与适用结果签署欠项不能倒签；完整宿主生命周期、真实数据/供应商准入、效用和发布各自验收 |

GO 仅覆盖已批准范围内的本机无标签探索、报告查看与合成复现。完整 FR、正式质量、本人效用和生产保持 NO-GO。合并事实、开发复测、独立指定技术通过、完整验收和发布批准分别判断。

## 历史缺陷与证据入口

| 范围 | 原问题 / 指定复验边界 | 固定证据 |
| --- | --- | --- |
| Q-01～Q-08 | 原服务端局部通过仍缺来源/展示、状态字典、快照与真实宿主准入；非作者APPROVED/签署不足 | [2026-09-25快照及原基线](https://github.com/262412/-Whynote/blob/9246dd5f143a02148a9328f79f5e188049f01563/docs/quality-baseline.md) |
| Q-11～Q-16 | 票据小数期限、最新展示、访问/删除归属；Q-16伪关联原失败不能用旧绿色结果覆盖 | [原失败与复验](https://github.com/262412/-Whynote/blob/9246dd5f143a02148a9328f79f5e188049f01563/docs/qa-q11-q15-retest.md)、[Q-16独立复验](https://github.com/262412/-Whynote/blob/9246dd5f143a02148a9328f79f5e188049f01563/docs/qa-q16-acceptance.md) |
| Q-17～Q-22 | 撤销后点击、通知、超大计时/重试、统计；重复点击与SQLite/调度等待导致有效菜单误过期 | [manual原失败](https://github.com/262412/-Whynote/blob/9246dd5f143a02148a9328f79f5e188049f01563/docs/qa-manual-v1.md)、[Q-22最终技术复验](https://github.com/262412/-Whynote/blob/9246dd5f143a02148a9328f79f5e188049f01563/docs/qa-q22-acceptance.md) |
| Q-23～Q-26 | 回执复活、入口关闭清理、model_item旁路；Jev被请求Noul返回null曾被静默接受 | [S1复验](https://github.com/262412/-Whynote/blob/9246dd5f143a02148a9328f79f5e188049f01563/docs/qa-s1-q23-q25-retest.md)、[Jev复验与原失败](https://github.com/262412/-Whynote/blob/9246dd5f143a02148a9328f79f5e188049f01563/docs/qa-jev-native-interface.md) |
| Q-27 | 59.5秒到达、复查跨60秒被误拒；指定到达时间/竞态独立通过，不等于真实效用 | [原失败](https://github.com/262412/-Whynote/blob/9246dd5f143a02148a9328f79f5e188049f01563/docs/qa-m5-templates-independent.md)、[独立复验](https://github.com/262412/-Whynote/blob/9246dd5f143a02148a9328f79f5e188049f01563/docs/qa-q27-retest.md) |
| Q-28～Q-37 | 缺参写入、旧构建、切后端旧回执、优化校验、错误码/预检、未跟踪源码、部署来源和worker隔离 | [完整历史基线及逐次复验](https://github.com/262412/-Whynote/blob/9246dd5f143a02148a9328f79f5e188049f01563/docs/quality-baseline.md) |
| Q-38 / Q-39 | 异常未立即停止、加载后异常误记加载失败；原9断言不变，指定范围独立通过 | [原失败](https://github.com/262412/-Whynote/blob/9246dd5f143a02148a9328f79f5e188049f01563/docs/qa-m54a-controlled.md)、[独立复验](https://github.com/262412/-Whynote/blob/9246dd5f143a02148a9328f79f5e188049f01563/docs/qa-q3839-retest.md) |
| Q-40 | 来源运行中到期仍发送后续102次；作者修复后原断言不变，独立指定边界及合成UI通过 | [原失败](https://github.com/262412/-Whynote/blob/9246dd5f143a02148a9328f79f5e188049f01563/docs/qa-m55-independent.md)、[独立复验](https://github.com/262412/-Whynote/blob/9246dd5f143a02148a9328f79f5e188049f01563/docs/qa-q40-retest.md) |
| 窗口诊断与Laya退出 | 完整输入仍误判，不用扩窗或压缩掩盖判别失败；旧JSON字符串摘要与worker原始UTF-8摘要已逐项对账，原证据不改写 | [固定窗口说明](https://github.com/262412/-Whynote/blob/9f467cfa34ba9979f3523363bc334673f0343142/docs/m55-window-validation.md)、[摘要对账](../qa/evidence/2026-09-30-m55-window/hash-reconciliation.json) |

历史报告的“待合并/通过”只适用于其原日期、版本和范围，不能当当前批准。完整报告与原失败保存在固定 Git 提交；本地 `git show 9246dd5:docs/quality-baseline.md` 可恢复原文，不删除历史或放宽断言。

原始元数据、JSON、截图及复现脚本继续保留于 `qa/evidence/` 和 `docs/evidence/`；真实正文/缓存/运行库仅在原受控 `var/`。`*.py.txt` 是历史执行脚本，UTF-8/LF摘要按原manifest核对，复制复跑必须用新目录；不能覆盖旧运行或扩大批准范围。保留备份、日志、失败及暴露记录的原有边界。

已签来源与规则仍以原记录为准：[Q-16/H-02](https://github.com/262412/-Whynote/blob/9246dd5f143a02148a9328f79f5e188049f01563/docs/q16-association-contract-proposal.md)、[manual-v1](https://github.com/262412/-Whynote/blob/9246dd5f143a02148a9328f79f5e188049f01563/docs/manual-reason-contract-proposal.md)、[S1云配置](https://github.com/262412/-Whynote/blob/9246dd5f143a02148a9328f79f5e188049f01563/docs/s1-cloud-contract.md)、[D-20正式协议](https://github.com/262412/-Whynote/blob/9246dd5f143a02148a9328f79f5e188049f01563/docs/m54a-freeze-protocol.md)。签契约不等于签实际结果；本清理不代任何责任人验收。
