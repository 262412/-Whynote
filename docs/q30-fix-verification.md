# Q-30：回滚后旧 live 回执的开发修复

2026-09-29；PR #40，同一分支；QA基线369b26f，输入PRD440/技术433。
FR-11/13/14、TD-04/10；M5-3b/D-19。Q-28/Q-29指定技术范围独立通过，原证据保留。

## 根因与修复

_current只遍历当前准入binding。切回fixture时当前binding不再含live的模型/来源/推断版本，
因此旧live binding多出的字段被忽略。原独立两例在真实TrialStore/SQLite均复现非法追加。

修复排除建议ID、展示ID、候选集合、理由、结果和presentation这些生成快照字段后，对剩余准入/版本
字典作完整相等比较，包含字段存在性。当前后端与生成后端不一致时，旧render/respond及其直接库重试
均抛ConflictError且零追加；反方向同样拒绝。没有新增事件字段、修改历史或迁移数据库。
旧fixture缺少live专属字段仍合法，未显式设置backend仍默认fixture；同版本生成/回执重试保持幂等。
既有Action同点击重放只返回原结果而不追加回执，其语义不变。已确认的历史不因配置回滚被删除，
回滚后可用新建议/展示ID继续fixture。Q-27回调到达时间不能绕过当前版本复核。

## 开发证据

- 原两条Q-30独立用例未修改：在369b26f重新安装非editable包后均失败，与CI36518299212一致，日志保留。
- 修复后重新安装包，原两条独立用例及新增9项、既有建议/独立边界合计87/87通过。
- 新增覆盖反向切换拒绝、双向切换后的已记录回执重试拒绝、live/旧fixture同版本幂等，以及
  已确认历史保留、回滚后新fixture可用、Outbox仍1条、固定as_of报告两次相同且只读。
- Ruff lint/format通过，94文件。完整回归767/767通过（87.83秒，保留既有Starlette/httpx警告）。远端CI另核对最新head。

```powershell
uv sync --extra dev --locked --no-editable --reinstall-package whynote
uv run --no-sync pytest tests/test_live_rollback_independent.py tests/test_live_rollback.py tests/test_suggestions.py tests/test_m53_independent.py -q
uv run --no-sync pytest tests qa/test_s0_regressions.py qa/test_manual_v1_acceptance.py -q
uv run --no-sync ruff check src tests integrations qa
uv run --no-sync ruff format --check src tests integrations qa
```

## 剩余门槛

本轮只修事件库/本地操作者API的Q-30，不将它表述为浏览器或公共HTTP越权修复。
未启动浏览器/本机模型或重新构建前端；未做外网泄漏或GPU耗尽试验。
Q-30待独立复验；出站隔离及跨进程并发评审仍阻断，需运行环境维护者处理后由QA核验。
非作者APPROVED、来源/兼容/回滚及结果签署仍缺，未合并，生产NO-GO。
原主目录代码、旧数据库、失败日志和QA报告均保留；原PR分支/工作树继续用于复验。

## 交付回执

修复提交6aa60a4的[CI36519989314](https://github.com/262412/-Whynote/actions/runs/36519989314)通过：
quality为727核心＋9保留S0＋31manual，共767；native为80原生及58探针。
追加文档后仍核对最终head，不引用旧绿色代替。
飞书按原需求/字段/当前任务局部更新并读回PRD440→448、技术433→440；
[逐条回执](../qa/evidence/2026-09-29-q30-fix/feishu-sync.json)。原独立失败原文及链接保留。
本轮不启动临时宿主服务；分支和工作树保留等待复验。主目录只同步当前开发计划，其他脏/未跟踪文件受hash保护。
