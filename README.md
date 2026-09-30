# 知因・Whynote

可审计的负反馈研究 PoC：先可靠保存点踩与用户亲选原因，再验证本机模型建议能否减少补写。模型推测永远不等于用户确认。

[产品 PRD](https://my.feishu.cn/wiki/XG6SwutL0i3fyWkwmhScEEB86Gg) · [技术文档](https://my.feishu.cn/wiki/GuphweJs3iWwBRkBDjlcljA16bh) · [开发规约](docs/development-governance.md) · [质量与剩余门槛](docs/quality-baseline.md)

## 安装与验证

Python 3.11；在核实版本的开发 checkout 中按锁文件安装。Windows 中文路径使用普通安装；源码修改后加 `--reinstall-package whynote`，不要重装现有 Laya/GPU 专用环境。

```powershell
uv sync --extra dev --locked --no-editable
uv run --no-sync ruff check src tests integrations qa
uv run --no-sync ruff format --check src tests integrations qa
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

## 当前限制

当前主干 `9f467cf` 已合入 PR #43 的 M5-5 与 PR #45 的窗口诊断；本次提交同步简洁说明和项目约束。Q-40 与合成案例 UI 在 `382c95a` 指定范围独立通过；合并事实不代替非作者有效批准和适用责任人签署。正式质量、本人效用、完整 FR 与发布尚未通过；生产上下文、auto-attach、自由文本 SLM 和训练导出保持关闭。

历史 3000 槽中 956 可运行，748 建议里 610 对所有当次可用理由均 yes；短输入也有判别反例。先检查输入投影、路由和选择，再决定理由包；不能把 unknown 当理由缺失。本人裁决、未暴露留出集及事前参数冻结仍待完成，详见[质量基线](docs/quality-baseline.md)。

窗口诊断固定 `a3f51f4`：完整输入三窗理论可容纳 1926/2616/2853 项，65 次本机实测无截断；一次问题修正后短例仍误判，已停止 Laya 调参。下一步 22 目标 Jev 对照仍等待 API、费用及 16 个真实目标的出站授权，目前零 Jev 调用。覆盖提升不是质量通过；应用 700/1024 token 限制保持。复现入口 `scripts/m55_window_validation.py theory|run`，完整参数与失败证据见[固定历史说明](https://github.com/262412/-Whynote/blob/9f467cfa34ba9979f3523363bc334673f0343142/docs/m55-window-validation.md)。

功能说明维护本 README 和宿主 README；仅规约、合规或脚本依赖另留文件。逐提交进展、回执与比较放对话/PR。完整字段、已签协议和历史复现保存在[固定 Git 文档历史](https://github.com/262412/-Whynote/tree/9246dd5f143a02148a9328f79f5e188049f01563/docs/)；本地可用 `git show 9246dd5:docs/m5-suggestion-contract.md` 查阅，不改变历史验收结论。
