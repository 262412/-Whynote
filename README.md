# 知因・Whynote

可审计的负反馈研究 PoC：先可靠保存点踩与用户亲选原因，再验证本机模型建议能否减少补写。模型推测永远不等于用户确认。

[产品 PRD](https://my.feishu.cn/wiki/XG6SwutL0i3fyWkwmhScEEB86Gg) · [技术文档](https://my.feishu.cn/wiki/GuphweJs3iWwBRkBDjlcljA16bh) · [开发规约](docs/development-governance.md) · [质量与剩余门槛](docs/quality-baseline.md)

## 安装与验证

Python 3.11；在核实版本的开发 checkout 中按锁文件安装。Windows 中文路径使用普通安装；源码修改后加 `--reinstall-package whynote`，不要重装现有 Laya/GPU 专用环境。

```powershell
uv sync --extra dev --locked --no-editable
uv run --no-sync ruff check src tests integrations qa scripts/m55_window_validation.py
uv run --no-sync ruff format --check src tests integrations qa scripts/m55_window_validation.py
uv run --no-sync pytest tests qa/test_s0_regressions.py qa/test_manual_v1_acceptance.py -q
```

`uvicorn whynote.api:app` 默认拒绝业务请求，宿主须注入可信身份和对象权限。`python -m whynote.demo` 仅提供本机虚构页面。Open WebUI 的补丁、部署与原生测试见[宿主 README](integrations/openwebui/tests/README.md)。

## 功能与契约

| 功能 / 入口 | 必须保持的边界 |
| --- | --- |
| `POST /v1/feedback-actions`、`/{event_id}/retract`、`GET /{event_id}` | 动作与 Outbox 同事务；推断不阻塞点踩；事件只追加并可重放；动作撤销与推测失效分开 |
| `/{event_id}/attribution-events`、`whynote.measurement` | 来源、权限、对象版本、展示与幂等键绑定；选择/更正、都不是、跳过、拒填、关闭分别记录；无原因与用户选择 unknown 分开 |
| 本机 Laya、Jev 适配器 | 生产推断 Gate 允许路径仍以 `pipeline_unconfigured` 拒识；门控失败零上下文读取/模型调用。Laya 为未校准、未确认推测；Jev 凭据仅从本机私密引用读取 |
| `whynote.source_review`、`task_reasons`、`replay` | 合成映射、理由包和 A/B/C 诊断；反馈/gold 不进入预测材料；未知、无匹配与用户拒绝候选分开；旧合成入口不自动获得真实数据许可 |
| 建议与模板 / `research_report` | 生成、渲染、有效响应和确认分别计数；仅 yes/correct 形成确认。60 秒按可信回调到达时间判断，事务内仍复核撤权/撤销/换版/停用；零点击不确认 |
| `whynote.explore`、`controlled_replay`、`self_review` | 无标签探索与正式评测分开；探索结果不生成质量 PASS，不回填独立 gold，不直接用已暴露组作盲评留出 |

本机 Laya 的安装、启停和固定版本见[运行指南](docs/laya-local.md)。固定模型输入上限为 8192 UTF-8 字节 / 700 token，总预算 1024 token；超限拒绝，不静默截断。新理由 ID 不写回旧八类菜单。常规菜单“都不是”清空原因；模型建议组“都不是”拒绝该组，保留既有确认。

## 三源本机探索

路线：固定来源文件 → 无标签诊断 → 查看报告 → 人工审阅 → 冻结新版理由包和新留出集 → 盲标复标 → 正式评测。访问、许可、数量和 30 天到期规则见[来源使用记录](docs/m55-source-use.md)。

先从固定干净候选非 editable 安装 Whynote，保留可用 Laya/GPU 依赖；每批使用全新输出目录。本机现有入口：

```powershell
$m55Root = 'D:/PythonProject/jev项目/var/research/m55'
$m55Python = 'D:/PythonProject/jev项目/var/laya-runtime/Scripts/python.exe'
$m55Base = 'C:/Users/22826/AppData/Roaming/uv/python/cpython-3.11-windows-x86_64-none'
$m55Model = 'D:/PythonProject/jev项目/var/models/laya/multilingual'
$m55Run = "$m55Root/runs/my-first-batch"
$env:TEMP = "$m55Root/tmp"
$env:TMP = $env:TEMP
& $m55Python -I -B -X utf8 -m whynote.explore prepare --manifest "$m55Root/source-manifest.json" --output $m55Run --records 100
& $m55Python -I -B -X utf8 -m whynote.explore run --output $m55Run --python $m55Python --base-python $m55Base --model-dir $m55Model
& $m55Python -I -B -X utf8 -m whynote.explore report --output $m55Run
& $m55Python -I -B -X utf8 -m whynote.explore view --output $m55Run
```

`--records 100` 是每源最多 100 条源记录；超过时显式给 `--targets`，如 `--records all --targets 1000`。`--sources` / `--schemes` 选择来源和方案，不扩大批准范围。默认单 CUDA worker、常驻模型、batch size 1、方案 C。

`view` 打印带随机 token 的 loopback 地址；案例查看登记探索暴露。报告只放元数据，正文/参考反馈留在受控输入库。取消时在 run 目录创建 `cancel.request`；停止后保留审计副本并改名，用 `resume` 恢复未开始槽。已开始但结果未知不自动重试；版本/hash 不一致另建 run；来源到期拒绝新读取、发送及正文查看。

合成复现还可使用 `fixtures/m5-synthetic/batch.json`、`fixtures/m5-replay.json`、`qa/m52_suggestion_fixture.py`；输出均须为新目录，不接历史数据库或真实聊天。

## 原三源 TypeSafe 全量对照

2026-10-04，用户已允许原 Laya 三源同规模输入外发 TypeSafe：固定 `batch-1000-final`、每源 1000 个目标、seed 42；原 84 个敏感模式目标继续排除。本轮在独立的 `var/research/typesafe-preflight/m55-full-20261004/` 准备，保留原运行和旧单次/2+4 锁。IPv6 规则可能误改代码的12项已剔除；下表为最终原题输入计数。原题计划、完整本机入口和离线核验已准备；助手不读取真实 key、不执行认证请求。下文旧小批说明是历史范围，不能替代本次授权。

| 来源 | 原目标 | 本轮可发送 | 其中脱敏改写 | 排除 |
| --- | ---: | ---: | ---: | ---: |
| HelpSteer3 | 1000 | 809 | 0 | 191 |
| WildFB | 1000 | 870 | 0 | 130 |
| WildFeedback | 1000 | 852 | 0 | 148 |
| 合计 | 3000 | 2531 | 0 | 469 |

469 项排除包括原敏感模式 84、隐私筛查未解决 324、疑似批评文本重叠 47、保守字节预算超限 14。旧 Laya 的 1960 项超长中有 1622 项入选；历史 956 项可运行基线中，909 项输入未改、47 项排除。筛查是本机规则检查，不证明零 PII 或逐条人工审核。问答仅留在 `prep/outbound.sqlite3` 受控库，`prep/plan.json` 保存无正文索引、排除原因、来源与摘要；到期不晚于 **2026-10-29 09:00 UTC**，原数据用途和保留期不延长。

本轮冻结历史 `questions('original')`：五类 route 和三个原理由 Choice，保留原文案与 `yes/no/unknown` 选项、每题完整概率，`primary_reason=null`；`no` 的原义是“证据不支持该缺陷”，不是人工确认无缺陷。原题 canonical SHA 为 `4d7a8c9fad77b5dc391181f7563fb824ce5b7bdd3b88c66d191b70d6e1ac464b`，已逐 stage 重构核对历史 1912 个 stage 的题目和选项。909 个共同输入中，历史 route、指令不遵循、不必要拒绝各问过 909 次，接口改动仅问过 3 次，其余 906 项为 `not_asked`，不生成 `no` 或概率。历史未归一化概率保留原值并标为不能进行严格概率比较。未请求、失败和材料不足分别计数；无 gold，准确率/F1 和正式质量保持 NA，不用于训练或开启生产。

固定 `jev-1.13.0`；[官方模型限制与单价](https://docs.typesafe.ai/models)为总输入 64k token、state 加最长问题 32k token、输入每百万 token 0.042 美元、输出免费。离线规划使用 UTF-8 字节加 4096 包裹余量，未测得服务端 tokenizer 长度，超限停止、不截断。按最大 64k 每次预留 0.002688 美元，原题预算核验后 2531 项一次尝试规划 **6.803328 美元**，每项最多两次规划 **13.606656 美元**。执行时使用本人已同意的费用上限，不追加充值；这些公开费率估计及本地停止线不代替有效账户扣费硬上限。完整入口只供本人手动执行，超时或未知费用不自动重发。

```powershell
$fullEntry = 'D:/PythonProject/jev项目/var/research/typesafe-preflight/m55-full-20261004/start.ps1'
& $fullEntry                  # Preview：检查固定原题批次，不读取 key
& $fullEntry -Mode Mock       # fake key；审计钩子拦截网络和真实 key
# 本人填写 key，并核实服务方有效账户扣费上限不高于本轮已同意预算后，手动执行：
& $fullEntry -Mode Live -ProviderCapConfirmed
& $fullEntry -Mode Compare    # 本地读取本轮 live 结果，无 API 请求
```

不用填写 SHA 或传递 key；入口从 `var/private/provider-keys.json` 的 `typesafe` 字符串读取。`-BudgetUsd` 可调低预算。`-ProviderCapConfirmed` 是本人对账户硬上限的确认，脚本不验证服务方账户，余额不等于扣费硬上限。原题使用独立 `runner/original.*` 冻结描述、request pins 和 mock/live 台账；完成目标不重发，429/529 明确拒绝最多重试一次，超时或未知费用先停止。全量 Mock **2531/2531 完成**，再次执行跳过 2531 项，新增请求和正文读取均为 0；live 本地比较显示 0 真实配对。Mock 对照见 `runner/verification-original-final-20261005/original.mock-comparison.json`。原题全量输入独立核验见 `var/research/dual-route-coordination/m55-original-snapshot-independent-verification.json`：2531 项原文、84 项原敏感排除、0 真实 key 读取、0 认证请求。相关最终回归为 **235 passed、1 skipped**（Windows 无符号链接权限），Ruff 通过；这些工程检查不构成正式质量验收。

## 双路线本机准备

2026-10-05 续跑兼容契约：研究台账新增 `disposition` 事件，仅追加本人明确批准的 `skip_unknown_keep_reservation` 处置，绑定原失败事件摘要、目标、attempt 和批准引用。原失败、原概率判断及未测费用预留不改写；该预留始终占用预算，其他未处置 unknown 仍停批。新读取器兼容旧 pending/outcome；旧读取器遇到新 disposition 会关闭。未来 outcome 可带 `m55-redacted-response-diagnostic-v1` 白名单诊断，保存题号、有限数值概率及原和、usage、合法 request ID、时间与HTTP状态，不保存原问答、认证头或自由文本；诊断 usage 不计作已确认扣费。概率和门槛仍为 `1e-6`。用户已于 03:23:49 UTC 批准跳过本批 `b2fdbd93…1ae84e`，保留其 `$0.002688` 未测预留并继续其他目标；此前丢失的失败响应不补造。此兼容变更仅用于本机研究续跑。

2026-10-04，本机 checkout `7661995`，D-21、FR-05/06/14/15、TD-07/11/14：产品判断后并行落实本机 Laya 合成工程试跑与 TypeSafe 用户运行入口。下列临时脚本位于本机被 Git 忽略的 `var/`，尚未纳入发布；不改变三源无标签诊断用途或生产门控。最近保存的飞书修订为 2026-09-30 的 PRD595 / 技术595；当前修订未核实，以可核验本地资料开展本轮隔离准备。

TypeSafe 密钥由本人填写在 `D:/PythonProject/jev项目/var/private/provider-keys.json` 的 JSON 顶层 `typesafe` 字符串，保留已有其他字段。该文件已存在、被 Git 忽略；不要把密钥写入 README、Git 或对话。适配器 `whynote.jev_provider.evaluate_reason(keys_file=绝对路径)` 经 `provider_keys.load_provider_key` 读取 UTF-8（兼容 BOM）JSON；没有 `.env` 或 `TYPESAFE_API_KEY` 自动加载。`WHYNOTE_PROVIDER_KEYS_FILE` 仅是宿主文件路径指针，现有 `s1_pipe` 只读取 `deepseek`。

```powershell
$pfPython = 'D:/PythonProject/jev项目/.venv/Scripts/python.exe'
$keyCheck = 'D:/PythonProject/jev项目/var/research/typesafe-preflight/preflight.py'
& $pfPython -I -B -X utf8 $keyCheck check-path --keys-file 'D:/PythonProject/jev项目/var/private/provider-keys.json'
$dualAPI = 'D:/PythonProject/jev项目/var/research/typesafe-preflight/dual-route-20261004/research_api.py'
& $pfPython -B -X utf8 $dualAPI preview
& $pfPython -B -X utf8 $dualAPI mock --approved --case all
```

`check-path` 只检查文件元数据，不验证密钥内容。新研究入口默认 `preview`，显示完整固定虚构请求；`mock` 默认关门，`--approved` 仅启用临时假密钥与 `httpx.MockTransport`。`mock --approved --case all` 覆盖18种情况，其中16种故障应被拒绝，整体退出码2是该组合的预期结果；单独成功案例退出码0。助手只运行这两种离线模式；它们不读取用户 key，不证明真实服务可用。用户收费模式的标量同意门控先于上下文和凭据读取；CLI flags 不代签项目审批。

共享契约为 `jev-three-defect-research-v1`：三个 Noul 分别判断明确任务/语言/格式约束违背、必需内容遗漏、当前材料可证明的内部错误。Laya 的 `t/ins/crit` 只改名为 HTTP `type/instructions/criteria`；历史八类 Choice 不映射为三类准确率。输入只含截止目标回答的 `context` 和单个 `response`；标签、批评、分组和未来反馈不进输入。每维监督为 `0/1/null` 和掩码；预测独立保留概率、决策、拒识状态与可见材料门控，阈值拒识不等于材料不足，三维全 0 不代表用户满意。

首轮单次 API 案例 `reg-office-identifiers` 要求给出虚构办公室的 room K7 和 chair B2，回答只有 `Room K7.`；完整三问题请求为 **1374 UTF-8 字节**。向[官方接口](https://docs.typesafe.ai/api)固定发送 `jev-1.13.0`，1 请求/并发1、总超时60秒/连接10秒，无重试、重定向或追加探测，响应上限1 MiB，拒绝压缩正文。真实模式使用持久一次性锁，失败或超时也不自动重试；usage 阈值是响应后的停止检查，不能限制本次扣费。请求与 proposal SHA 由 `preview` 展示，请本人核对，不能自动生成批准依据。单次入口状态以本人接入对话为准；新批次使用独立进度，不复用或删除单次尝试锁。

**收费入口仅供本人手动执行，助手不执行。** 2026-10-04 [公开单价](https://docs.typesafe.ai/models)为输入每百万 token 0.042 美元、输出免费；公开单价和本地规划预算不代表有效账户扣费上限。批量入口要求本人核实有效账户硬限额，并且不高于明确同意的最高收费；余额不能代替硬限额。无需为离线准备另行充值。本轮只发送事先展示的固定虚构 context、单个 response 及三问题，不发送公开训练样本、私人聊天、标签或批评；API输出不进入本机训练gold。单次入口的持久锁保留，批量操作使用下方独立入口。

批量入口为 `var/research/typesafe-preflight/batch-round-20261004/product_batch.py`。固定 8 个合成家族、最多 6 个真实请求：先 Office/Poetry 两条，人工核对后才可运行 Scheduling/Chain/Tea/Code 四条；Telemetry 和 Appendix 两条只计本地门控，不外发。不自动续批、不自动重试；已完成 payload 跳过，未决或未知费用停止。首批已知阴性出现高置信误报会阻止扩批，不能在观察结果后修改 0.2/0.8 阈值。追加式进度仅保存白名单概率、状态、usage、延时和 hash，不复制原始问题、回答、密钥或自由文本。

```powershell
$batchRoot = 'D:/PythonProject/jev项目/var/research/typesafe-preflight/batch-round-20261004'
$batchCLI = "$batchRoot/product_batch.py"
$batchPlan = Join-Path $batchRoot ('user-plan-' + [guid]::NewGuid().ToString('N') + '.json')
# 离线预览全部出站材料，生成全新计划；请先检查输出，再单独运行收费阶段。
& $pfPython -B -X utf8 $batchCLI preview --inspect-synthetic-inputs --output $batchPlan
$batchPlanSHA = (Get-FileHash -LiteralPath $batchPlan -Algorithm SHA256).Hash.ToLowerInvariant()
```

```powershell
# 本人手动执行；占位值必须换成本人决定的预算和已核实账户条件。
& $pfPython -B -X utf8 $batchCLI first --enable --confirm-synthetic-batch --confirm-first-phase `
  --confirm-user-local-execution --accept-charges --plan-file $batchPlan --plan-sha256 $batchPlanSHA `
  --planning-budget-usd '<已授权规划预算>' --reserve-per-attempt-usd 0.002688 `
  --max-charge-consent-usd '<已同意最高收费>' --attest-effective-account-cap-usd '<本人核实的有效硬限额>' `
  --keys-file 'D:/PythonProject/jev项目/var/private/provider-keys.json'
& $pfPython -B -X utf8 $batchCLI review --mode live --inspect-synthetic-inputs --plan-file $batchPlan --plan-sha256 $batchPlanSHA
```

`review` 提供首批结果摘要和扩批门控。确认后用 `continue` 替换 `first`，将 `--confirm-first-phase` 换为 `--confirm-continue-phase --confirm-first-preview-and-structure --first-results-sha256 '<review结果摘要>'`；其余预算、账户、计划和密钥路径参数保持一致。完成或停批后运行 `comparison --mode live --inspect-synthetic-inputs --plan-file $batchPlan --plan-sha256 $batchPlanSHA`，比较固定原模型、保存的 adapter 和 API 三路，保留8家族/24维、未知标签、拒识、未请求及每类误报/漏报分母。已有本机结果沿用历史时序完整性缺口，不重跑 GPU、不宣称盲评或正式质量通过。按公开 64,000 输入 token 模型上限与单价作规划，每次估计 0.002688 美元，六次 0.016128 美元；该估价不约束账户真实扣费，费用门控仍独立检查。

本次独立组合复验为 **264 项 pytest 通过**，13 个代码/测试文件的 Ruff lint 和 format 检查通过。原五项阻断断言文件 hash 保持不变；到期后禁止重读正文、journal 概率/决策语义和费用前置门控已通过。待本人核对的离线计划为同目录 `user-plan-prepared-20261004.json`，SHA256 `cb8d3b90734eb95384618c04690aeb14ba85b5a0b68398a3c067fca06e25d781`，到期 **2026-10-05 06:00 UTC**；源码变化后须重新预览生成计划，不能复用过期 pins。准备过程没有新增认证请求、未打开密钥文件内容，正式质量和完整验收仍未签署。

数据准备固定 HelpSteer3 `f6d145777bcbde96137596340fab89793acd1031`；新目录 `var/research/helpsteer3-review/training-prep-20261003/` 与旧诊断数据分开。发布方 CC BY 4.0 和反馈模型训练用途声明支持本机小样本审查；上游修订/内容权利证据缺口继续列为正式训练前待裁决项，不据此认定数据违法。三类标签为指令/语言不符、必需内容遗漏、内部可验证错误，另区分未见缺陷与证据不足；未知值保留 `null` 和掩码。批评仅用于标签初稿，输入只含 context 与单个目标回答；按案例、原 prompt、对话和重复材料分组，暴露或缺来源历史的组进入 quarantine。自动草稿不是人工 gold，工程划分不是盲独立留出。

实际 Laya 入口为 `var/research/helpsteer3-review/training-prep-20261003/dual-route-20261004/laya_trial.py`，使用原 `var/laya-runtime/Scripts/python.exe`。它只接固定虚构 fixture，五个训练家族与八个回归家族隔离；公共数据训练开关仍为 false。2026-10-04 的单次 `trial-001` 已完成5步、每次前向batch1，实际完整序列86–116 token（上限512），仅训练14,770,945个 head/scorer/type embedding 参数。墙钟18.50秒、peak reserved1558 MiB；161个冻结参数/buffer摘要和五个原模型文件hash未变。独立 `trial-001/head_adapter.safetensors` 保存并重载复现了回归概率和损失；原 checkpoint 保留。该结果只证明本次合成工程运行，质量 NA，不保证5步512实长或真实数据效果；不自动重复训练或扩步。

回归输出同时保存原模型与 adapter 的三维概率/状态。独立复算的18个已知合成标签维度，前后均为3个一致、11个误报、4个拒识，决策没有改善；平均已知loss由1.83716到1.83199，不作为扩大训练的依据。同覆盖下的阴性误报、阳性漏报、未知材料强判、路由和拒识分母须保留。正式质量比较须另有未暴露、独立裁决的gold和事前冻结；本轮合成回归不是盲测。常规原因菜单另作用户效用对照，用户选择不自动成为客观缺陷gold。旧 `prep.py --tiny-step` 是随机tiny CPU单步，与本轮实际Laya入口分开。

试跑使用修复前的 fixture 读取实现；修后已改为每个文件一次有界读取，并对同一份 bytes 校验hash和解析。本轮没有重训。当前及试跑前后hash一致不能排除运行中曾短暂替换，因此历史读取时序完整性不作已签署结论，执行版本hash和修后验证分开保存在结果元数据中。API失败审计也已修复：尝试读取密钥或发出请求后，不再将失败记录重置成零访问，无法确定的阶段保留unknown。首轮独立组合pytest为132项通过、1项涉及公共正文hash而排除，15个代码/测试文件的Ruff lint及format检查通过；当时的preview及18种mock未读取真实密钥或发出真实API请求。复现范围与证据见 `var/research/dual-route-coordination/final-verification.json`；这是首轮工程检查，正式质量仍为NA，未签署完整验收。

本轮新样本位于该目录的 `samples-B/review_samples.json`，无正文审计在同目录 `manifest.json`。从固定 train 小块接收 12 个案例，经机器筛查剔除 1 个联系方式命中案例，保留 11 条 code 域便利样本；中文筛选接口超时，本包不支持中文覆盖或代表性结论。标签为 `rule_draft`，5个遗漏初稿、28个未知维度，全部留在 quarantine，真实训练/校准/独立测试均为0。新机器审查确认输入投影、hash和分组，9条超过本轮512-token上限；没有新增PII规则命中也不等于零PII。无正文待裁决索引为 `dual-route-20261004/review_queue.json`。

仅本人本机账户及 SYSTEM/管理员可访问，无云同步/备份；到期为 **2026-10-10 14:34 UTC**，到期后删除正文和含正文衍生文件，保留无正文审计。`load_review_bundle` 先检查用途、到期和hash再读正文；本轮未创建自动删除任务。公共训练前，本人逐条决定保留/脱敏/剔除，并按证据裁决三维0/1/null，尤其核实5个遗漏是否必需；另明确训练用途和到期衍生物删除。原包hash保留，审定结果另存受控文件；来源/暴露历史及新独立评估仍须补齐，人工裁决不能补造上游身份。

本轮公开数据审核入口为 `var/research/helpsteer3-review/training-prep-20261003/real-round-20261004/admission_gate.py`。11 条仍隔离、9 条完整输入超出 512 token；504、508 两条仅长度合格。先按同目录 `review_batch_guide.json` 在本机查看原包，再填写 `review_batch.pending.json` 的隐私决定、三维 0/1/null 与证据范围、另一复核者意见、来源用途及到期删除承诺。`training_use` 可填 pending/accept/reject，`rights_gap` 可填 pending/accept_bounded_risk/reject；接受有限研究风险不能补造上游或暴露历史。0/1 标签要求 context/response 字符范围及本机审阅者/审阅记录 ID；null 原因为 not_reviewed、insufficient_current_material 或 out_of_scope_dimension。表单不含正文，未填字段保持 pending；人工提交仍是未验证意见，不能替代身份、来源和责任人签署。当前已准入训练、校准和独立测试均为 0；不运行真实训练或将旧 adapter 当作新基线。

```powershell
$reviewRoot = 'D:/PythonProject/jev项目/var/research/helpsteer3-review/training-prep-20261003/real-round-20261004'
$reviewPython = 'D:/PythonProject/jev项目/var/laya-runtime/Scripts/python.exe'
# 后两条仅在本人完成表单后执行；导入不会自动批准训练。
& $reviewPython -I -B -X utf8 "$reviewRoot/admission_gate.py" --audit
& $reviewPython -I -B -X utf8 "$reviewRoot/admission_gate.py" --import-human "$reviewRoot/review_batch.pending.json"
& $reviewPython -I -B -X utf8 "$reviewRoot/admission_gate.py" --freeze --submission "$reviewRoot/review_batch.pending.json"
```

`--freeze` 当前退出 2，列明隐私/标签、来源用途、独立复核、上游及暴露历史缺口，不生成虚假的训练批准。每次正文读取、逐记录复核和输出发布都重新检查到期；到期拒绝继续读取。未来 24 个训练组、8 个验证组和单次最多 24 步只是研究建议，尚无相应已审核数据；正式质量仍为 NA。

## 当前限制

主干版本 `9f467cf` 已合入 PR #43 的 M5-5 与 PR #45 的窗口诊断，后续已同步简洁说明和项目约束。Q-40 与合成案例 UI 在 `382c95a` 指定范围独立通过；合并事实不代替非作者有效批准和适用责任人签署。正式质量、本人效用、完整 FR 与发布尚未通过；生产上下文、auto-attach、自由文本 SLM 和训练导出保持关闭。

历史 3000 槽中 956 可运行，748 建议里 610 对所有当次可用理由均 yes；短输入也有判别反例。先检查输入投影、路由和选择，再决定理由包；不能把 unknown 当理由缺失。本人裁决、未暴露留出集及事前参数冻结仍待完成，详见[质量基线](docs/quality-baseline.md)。

窗口诊断固定 `a3f51f4`：完整输入三窗理论可容纳 1926/2616/2853 项，65 次本机实测无截断；一次问题修正后短例仍误判，已停止该轮Laya调参。历史22目标Jev对照仍等待API、费用及16个真实目标的出站授权，该窗口记录为零Jev调用；与本轮三Noul虚构研究入口分开记录。覆盖提升不是质量通过；应用700/1024 token限制保持。复现入口 `scripts/m55_window_validation.py theory|run`，完整参数与失败证据见[固定历史说明](https://github.com/262412/-Whynote/blob/9f467cfa34ba9979f3523363bc334673f0343142/docs/m55-window-validation.md)。

功能说明维护本 README 和宿主 README；仅规约、合规或脚本依赖另留文件。逐提交进展、回执与比较放对话/PR。完整字段、已签协议和历史复现保存在[固定 Git 文档历史](https://github.com/262412/-Whynote/tree/9246dd5f143a02148a9328f79f5e188049f01563/docs/)；本地可用 `git show 9246dd5:docs/m5-suggestion-contract.md` 查阅，不改变历史验收结论。

清理前尚未推送的本地文档另保存在[恢复提交 `6d3108e`](https://github.com/262412/-Whynote/tree/6d3108e652e6bbd75e48cd6c5d4260084666e078)中；该快照随主干历史保留，内容仅代表当时版本。

## 无模型诊断核验

在上述开发环境运行（D-21、FR-05/06/14/15、TD-07/11/14）：

```powershell
uv run --no-sync python -X utf8 scripts/m55_window_validation.py verify
```

仅只读窗口证据目录的 `schedule.json`、`journal.jsonl`、`hash-reconciliation.json` 和 `summary.json`，可用 `--evidence <目录>` 指定待核验记录。不会加载模型、访问真实输入库或重建真实正文摘要，也不写报告或改历史证据。JSON 输出分列请求完成、历史编码核验标记、两种摘要的元数据对应、指定缺陷符合数、明确阴性误报、阳性漏报、路由不符、材料不足、未设预期和未完成项；这次元数据检查不等于重新逐 token 验证。

复用既有 6 个短例与 6 个长探针，正文、ID、指定缺陷及历史问题不变；补充预期从合成内容判定，独立于预测输入，不是正式 gold。短例跨窗口按同一案例归组，逐请求字段计数不当独立样本数。原版/明确版路由映射分别保留；真实短例仅用历史探索预期，真实长例质量为 NA。退出码 `2` 表示元数据缺失/重复/不一致或非法标签，`1` 表示诊断失败或未完成，`0` 仅表示所列诊断预期全部符合；任何退出码都不代表正式质量或模型采用通过。原 65 条记录预期返回 `1`，指定短例仍可复算为原版 1/6、明确版 5/6，附加误报和路由错误不会被抵消。
