# Q-40 与合成案例 UI：独立复验通过

2026-09-30。固定 [PR #43](https://github.com/262412/-Whynote/pull/43) 候选 **`382c95a18edd5d67fa79f8ee183828abddc15be3`**，业务修复 `40215eb`；main **`cdcfb15fec8474969d36e2955d70ba28ade01a06`**。候选 CI [36595226167](https://github.com/262412/-Whynote/actions/runs/36595226167) 两项通过，PR 为 Draft，无非作者 APPROVED。

按 D-21、FR-05/06/09/14/15、TD-02/07/08/09/11/14 核对飞书输入 PRD **580**／技术 **576**及[探索契约](m55-exploration-contract.md)。本轮只关闭 Q-40 与合成案例 UI 的指定技术复验缺口，不代签完整 FR、正式质量、本人效用或生产批准。D-21 正常无标签探索不新增人工标签前置。

## 基线与方法

复用干净的 QA 工作树，在 detached 候选上用锁文件重装非 editable 包。QA 环境与已安装 Laya 包的业务源码摘要均为 `f41a4f28dd1bce79ceb681563723d07bcdaa00fecf9a1f52e987d949ae72d295`。原独立测试 `tests/test_explore_independent.py` 对比 `84aa76f` 无差异；本次没有修改业务代码或测试断言。

实际模型验证复用经阅读的作者合成边界脚本，仅改独立输出目录与候选标识。仅控制期限谓词和记录实际发送数，真实隔离 worker、模型、互斥、IPC、journal、SQLite 和报告均运行。输入为新合成材料，没有读取真实三源正文或延长其授权。

## 本轮执行结果

| 项目 | 操作与预期 | 实际与证据 |
|---|---|---|
| Q-40 原两条独立断言 | 启动前到期零调用；首槽后到期停止下一槽 | **2/2**；后者仅 1 次调用、101 个 `not_started`，原失败断言不变 |
| 10 条到期扩展 | 首/末槽正常、超时、取消终态保留；多源整体停止；拒绝过期恢复；加载/发送和正文读取边界 | **10/10**；journal 终态及报告分母、恢复零追加通过 |
| 完整回归 | `PYTHONUTF8=1 uv run --no-sync python -X utf8 -m pytest tests qa -q` | **1039/1039**，182.86 秒；含上述 12 项，未重复计数 |
| Open WebUI 实际 ORM/FastAPI | 固定 v0.11.4 / `8bd8b4f`、既有四补丁和 dialog、新虚构数据库 | **80/80**；权限、关联及删除路径通过 |
| 原生契约探针 | 新 SQLite，管理员他人明细 404 契约及数据副作用 | **58/58** |
| Ruff | 全工作树 `ruff check .` / `ruff format --check .` | 通过，253 个格式检查文件 |

完整 JUnit 的 SHA-256、Q-40 属性、原生结果及路径见[机器摘要](../qa/evidence/2026-09-30-q40-independent/validation.json)。原始日志/JUnit 在 QA 工作树 `var/qa-q40-retest/`。

## √ 实际隔离 Laya 到期边界

每个场景新建 2 个合成目标，各实际加载模型 1 次：

| 场景 | 实际发送 | 预期和实际终态 | 未开始 |
|---|---:|---|---:|
| 未到期对照 | 2 | COMPLETED；2 个真实拒识 | 0 |
| 首个真实返回后到期 | 1 | STOPPED / source_expired；保留 1 个真实拒识 | 1 |
| 冷加载完成时到期 | 0 | STOPPED / source_expired；已开始槽 skipped/source_expired | 1 |

后两项再次恢复均报 `source_changed_or_expired`，journal 字节不变、零后续发送；全部技术失败/中断为 0。加载后拒绝仍保守保留先登记的探索暴露，符合契约。见[实际调用回执](../qa/evidence/2026-09-30-q40-independent/actual-laya.json)。新材料在原项目 `var/research/m55/q40-independent-382c95a/`，旧 run 未覆盖。

## √ 合成案例浏览器与 HTTP

独立启动仅含合成数据的 102 目标报告，模型替身返回计划内 timeout。Codex IAB 显示 102 技术失败及质量 NA；折叠汇总、筛选 `8f0c529f96b8` 后仅余一个案例，点击后实际显示上下文、目标答案、参考反馈和“未获用户确认”的模型结果。随后只读核对 `exposure.jsonl`，对应 run/input 的 `case_view` 恰好新增 1 条。

另行 HTTP 负向验证：缺/错令牌、错误 Host 均 403；未知案例 404；这四项无暴露追加。合法案例 200、字段齐全并仅新增 1 条暴露，no-store/nosniff/CSP 生效。HTTP 与浏览器各自计数，未混作同一次操作。浏览器显示的 `FEEDBACK_SECRET` 是固定合成测试字符串。

见[界面截图](../qa/evidence/2026-09-30-q40-independent/browser-case.png)、[浏览器回执](../qa/evidence/2026-09-30-q40-independent/browser.json)与[HTTP回执](../qa/evidence/2026-09-30-q40-independent/http.json)。这次没有重跑 Open WebUI 宿主浏览器或前端构建，也不是实际用户质量评估。旧轮案例 UI 未完成的记录保留，不倒改为旧轮通过。

## 历史与剩余门槛

[修复前独立报告](qa-m55-independent.md)、[作者修复报告](q40-fix-verification.md)和失败 CI **36591123929** 保留。本轮没有重新运行真实 350/3000 槽、真实 20 目标取消恢复、完整隔离协议或 30 分钟耐久；它们的历史范围仍按原候选，不冒充本次实测。

PR 继续 Draft、未合并。非作者有效 APPROVED、适用责任人对来源/兼容/运行边界的结果签署及正式合并收尾仍缺；完整 FR、正式质量、本人效用和生产继续 **NO-GO**。原目录改动、旧失败和研究数据保留。临时测试服务结束后保留证据目录，未清理工作树。

## 飞书同步与只读核验

按原功能/实施条目局部替换22个块，每次携带准确revision-id并立即读回：**PRD 580→589／技术576→589**。全部旧链接及历史归档正文保持；替换前块归入[原文快照](../qa/evidence/2026-09-30-q40-independent/feishu-prior-blocks.json)，逐次回执见[同步记录](../qa/evidence/2026-09-30-q40-independent/feishu-writes.json)。本地开发计划与质量基线同步。
