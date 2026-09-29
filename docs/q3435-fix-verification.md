# Q-34/Q-35开发修复与验证

2026-09-29；QA基线2a23dd4，PR #40。对应FR-13、TD-04/10，责任：本轮开发执行Codex，独立QA及签署待补。
先补[源码准入和部署清单契约](m5-live-laya-contract.md)，不改变业务事件、模型推断或生产开关。

## 修复

- Q-34：临时索引应用四补丁后检查未跟踪集合，额外仅允许显式复制的suggestion_dialog.js；其他非忽略新增文件拒绝。补丁新增文件仍校验内容和存在性。依赖/缓存/构建输出沿用Git忽略规则。
- Q-35：M5-3清单v2区分计划与已验证部署。启动action_sha256为空、状态not_verified；初始化前后校验支持文件快照。全部初始化成功后，用管理员HTTP GET读回Action全文、激活/非全局状态，再原子写verified及实际摘要。
- Action摘要明确为统一换行至LF后的UTF-8文本；支持文件摘要为Whynote包和Open WebUI集成Python/JSON原始文件字节。旧S1入口不改变，旧M5-3清单不补签历史，需用新工具启动新实例。
- 失败初始化不产生verified清单；部分初始化仍可能写宿主，不能把此保证当成Q-33预检零写入。

## 已执行

| 检查 | 结果 |
| --- | --- |
| 原独立Q-34用例 | 修复前3失败；断言未改，修复后通过。旧失败CI36525573881保留。 |
| 相关回归 | 59/59，含Q-31～33预检和源码/构建相邻路径。 |
| 新增回归 | 11/11，补丁新增文件允许/缺失拒绝、部署读回错误/未激活/全局/模块漂移/HTTP失败不签发、换行规范、初始化失败与旧快照。 |
| 完整核心＋保留QA | 816/816，Ruff check及format通过；1条既有Starlette弃用警告。 |
| 实际固定补丁源 | 普通/-O/PYTHONOPTIMIZE下正常源均通过、唯一额外路由均拒绝；测试文件移除后已有完整构建记录通过。未编译或启动该额外路由。 |
| 实际新宿主8143 | 启动清单not_verified且action_sha256=null；完整初始化退出0，清单变verified。HTTP/SQLite/本地Action全文相等，实际摘要cdb89cdba449592b4563b7132e8a2e942bcd88391ddc20e846bf914d0ae094da；42个支持文件与启动快照及磁盘一致。 |

实际上游8bd8b4f及四补丁，沿用已有完整前端构建；未手工补写构建记录。
[汇总](../qa/evidence/2026-09-29-q3435-fix/summary.json)、[源码探针](../qa/evidence/2026-09-29-q3435-fix/source.json)、[实际部署读回](../qa/evidence/2026-09-29-q3435-fix/runtime.json)。
详细日志留在隔离工作树var/q3435-*.log，新合成库保留；临时8143已关闭。原目录改动及历史审计未清理。

复跑：`uv run --no-sync pytest tests qa/test_s0_regressions.py qa/test_manual_v1_acceptance.py -q`，及Ruff check/format检查src、tests、integrations、qa。
本轮只修改QA宿主工具及文档测试，无包代码或依赖变更。新增集合测试使用微型真实Git fixture，部署拒绝测试使用HTTP transport替身；实际成功路径另用新宿主HTTP/SQLite验证。

## 未完成与范围

本轮为开发验证，Q-34/Q-35须独立复验；Q-28～33各自指定技术范围独立通过结论保留。
未重跑浏览器、实际模型推理或前端构建。模块摘要标识启动与初始化时的本仓库源文件，不是运行时内存、第三方依赖、OS隔离或独立签署证明。
出站隔离、跨进程并发、非作者APPROVED及适用签署仍阻断。未合并，真实研究与本人试用未开启，完整FR和生产NO-GO。
