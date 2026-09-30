# 知因・Whynote

可审计的负反馈信号闭环研究 PoC：先可靠记录点踩和用户亲选原因，再验证模型建议能否增加有用反馈、减少填写成本，并避免错误归因。

[产品 PRD](https://my.feishu.cn/wiki/XG6SwutL0i3fyWkwmhScEEB86Gg) · [技术文档](https://my.feishu.cn/wiki/GuphweJs3iWwBRkBDjlcljA16bh) · [开发计划](docs/development-readiness.md) · [开发规约](docs/development-governance.md)

## 当前进度

2026-09-28 产品复核基线：远端 main `47212fb058b3769bfb9a922fe33dbc7fad3e9e79`，PR #30（S1-3）及 #31（本地 Laya）均已合并。本表描述该主干；旧本地工作区和已有安装包可能是其他版本。此次核对代码、已有开发/独立 QA 报告和 CI，未重新运行业务测试或云调用。

| 能力 | 已实现范围 | 仍缺什么 |
| --- | --- | --- |
| 点踩与常规原因菜单 | 动作/Outbox 同事务、展示绑定、选因、更正、跳过、拒填、都不是、关闭及撤销；S0 与 S1 宿主入口 | 完整 FR 验收和适用责任人签署 |
| S1-1 / S1-2：个人云聊天 | 已签 v1 契约；DeepSeek 生成适配、准入、预算、保存后可信回执、动态对象权限和常规反馈；虚构提供方已有独立 HTTP/浏览器证据 | 当前实际 DeepSeek 实例的有效配置、受控出站、真实浏览器与事件闭环仍须核验 |
| S1-3：研究记录 | 来源登记、全部合格回答台账、自然/脚本/公开模拟分组及只读报告；PR #30 已合入 | `research_enabled=true` 仅允许 `mode=mock`；真实云研究记录尚未开放，不能用改模式绕过 |
| Laya Multilingual | 固定 checkpoint 的本地 GPU 适配器及手动测试页；输出 `model_inferred_unconfirmed` | 尚未接 Open WebUI 原因建议、聊天读取或 Outbox 消费；尚无合格的业务效果评测 |
| TypeSafe Jev | 默认关闭的传输与响应校验适配器；MockTransport 测试 | 真实调用及菜单/推断消费者未接通 |
| S1-0：公开数据 | 三源版本、来源审查表与 manifest 模板 | 逐源准入、实际文件映射、筛选及最小样本；没有三数据集自动回放程序 |

证据入口：[S1-2 独立复验](https://github.com/262412/-Whynote/blob/47212fb/docs/qa-s1-q23-q25-retest.md)、[S1-3 独立 QA](https://github.com/262412/-Whynote/blob/47212fb/docs/qa-s1-research-independent.md)、[Laya 开发记录](docs/laya-local.md)、[main CI](https://github.com/262412/-Whynote/actions/runs/36390546955)。S1-3 的独立核心/QA 297/297、原生 75/75 与新增边界 8/8 有记录；该轮新登录 HTTP/浏览器因执行限制未运行。Laya 的 329/329、HTTP 10/10、GPU 与浏览器为开发证据；三个虚构例子中两个分类不符预期，不构成效果合格。非作者批准缺口保留。

## 现在可以怎样测试

| 测试方式 | 当前判断 |
| --- | --- |
| 虚构问答 + 常规菜单 | GO：使用已有 demo 或固定 Open WebUI S1 mock 环境回归动作和交互 |
| DeepSeek 聊天 + 知因常规菜单 | 可进入限定联调准备；用户报告已连通，本轮未验证实际云闭环。完成下述 S1-2R 验证后进行本人非敏感文本试用 |
| 独立 Laya 手动分析 | 已安装，可启动后测试；它与聊天模型各自运行，不会自动读取 Open WebUI |
| 点踩后自动调用 Laya 并显示候选 | 尚未实现。先离线评测，再交付 M5-2 的显式候选入口 |
| 三个公开数据集自动跑 Laya | 方案可行；须先完成准入、映射和离线 runner，不能把 S1-3 的 `public_replay` 枚举当作数据集导入能力 |

Open WebUI 固定 `0.11.4`，保留平台品牌，使用独立知因入口。[S1-2 启动与迁移说明](https://github.com/262412/-Whynote/blob/47212fb/docs/s1-dynamic-delivery.md)中的 `qa/s1_browser_host.py` 是虚构 mock 宿主工具，不是 DeepSeek 正式启动器。启动前核对干净候选的 Whynote 包、宿主补丁、前端构建、数据目录和有效配置；不要在旧且有未提交改动的工作区直接 pull 或重装覆盖现场。

本机已有的 Laya 安装可运行 `D:\PythonProject\jev项目\var\laya\start.cmd`，健康后访问 <http://127.0.0.1:8766/>；关闭使用同目录 `stop.cmd`。2026-09-28 此次检查未发现 8766 监听或 Python 服务进程；8080 被其他应用占用，不能据端口号认定 Open WebUI 已启动。操作、输入限制与来源说明见 [本机说明](docs/laya-local.md)和[契约](docs/laya-local-contract.md)。安装成功与服务当前运行分别判断。

## 公开数据自动化测试

建议先做 **受控样本 → Laya → 结构化结果与错误报告**，不必经过 Open WebUI，也不需要 DeepSeek 重新生成回答。现有本机 GPU 已有实际运行证据，小批回放无需先租显卡；批量吞吐仍待实测。

| 来源 | 适合用途 | 映射与解释限制 |
| --- | --- | --- |
| [THU-KEG/WildFB](https://huggingface.co/datasets/THU-KEG/WildFB) | 中文自然反馈优先核查，保留问题、目标回答及后续反馈关联 | 中英语料，满意度标签由自动流程产生；卡标 MIT 并要求遵守 WildChat 上游条款。当前 Viewer 报错，需固定文件验证 schema；不是没有数据，也不是已完成准入 |
| [nvidia/HelpSteer3](https://huggingface.co/datasets/nvidia/HelpSteer3) | 建议作为首个 runner 的小批字段映射来源 | 必须选 `feedback`：`context` 为消息列表，`response1/2` 为文本，`feedback1/2` 为字符串列表；评价员反馈不能冒充原用户原因。CC-BY-4.0；含中文问答，反馈统一英文，见[论文](https://arxiv.org/html/2503.04378v1) |
| [microsoft/WildFeedback](https://huggingface.co/datasets/microsoft/WildFeedback) | 英文补充，检查多轮反馈关联 | `wildfeedback` 为 conversations/chosen/rejected 偏好对；原反馈线索须关联 `sat_dsat_annotation` 等来源，不能把 chosen 当用户反馈。自动满意度及模型生成内容分开；ODC-BY |

以上 schema 来自当前 Viewer 元数据核对，不等于固定原文件逐行验收。本轮未导入语料正文，中文可用数和敏感内容筛查未完成，三源继续 HOLD。WildFB/WildFeedback 有共同 WildChat 来源，须按会话及近重复防止跨集合泄漏。

主评测任务是“看到目标回答后预测原因”：输入只含获准的目标回答及此前上下文，隔离后续反馈、标签、修改答案与未来轮次。另行测试“给定反馈后分类”时单独标记用途，不能将输入的反馈再当独立 gold。Laya 当前限制为 8192 UTF-8 字节、700 输入 token；超限明确拒绝并计覆盖率，任何裁剪规则先冻结，不能静默截断。

数据正文留在受控非代码存储。manifest 记录来源、revision、config/split、原行与目标轮次、校验和、许可链、筛选/分割版本；结果记录模型/criteria 版本、输入引用、结构化预测、延迟、拒绝与错误码。不得把原文、密钥复制进事件、普通日志或队列。公开标签不直接成为用户确认原因或独立 gold。

## 下一开发安排

以下为产品交接任务，尚未实现的新任务不标完成；详细 FR/TD/D/Q 映射与退出条件见[开发计划](docs/development-readiness.md)。

1. **S1-2R：本人 Open WebUI 云聊天闭环复验。** 宿主/后端核对有效版本与配置，继承已签 DeepSeek v1/累计 100 CNY 边界；QA 用受控非敏感文本验证生成保存→点踩→菜单→选因/更正/撤销，并对账出站、预算、事件、删除与停用。保留非作者评审及运行结果签署缺口。
2. **S1-3R：真实个人试用记录。** 先冻结 cloud 下来源登记、合格分母及保留/删除契约，再实现限定范围开关；不能直接取消现有 mock 限制。QA 验证研究停用、重生成、删除/撤权、重试与只读报告；实际采集前确定观察窗、时长/样本和指标阈值。
3. **M5-0 / M5-1：公开材料准入与 Laya 离线 runner。** 数据/产品先完成一个来源的小批审查，算法/后端实现版本化映射及批量本机回放；建议先用 HelpSteer3 feedback 跑通结构，同时审查 WildFB 中文。报告映射覆盖、超长拒绝、有效输出、延迟和失败案例；独立原因标签到位后再评价分类质量。样本数与合格阈值在运行前确定。
4. **M5-2：显式候选入口。** 仅在上述效果门槛及最小数据/权限契约满足后，接入知因独立“获取原因建议”行为；动作先成功，模型失败仍可用常规菜单。模型来源可见、无默认确认、可更正/都不是，验证旧版本/撤权/撤销后零非法写入。

个人试用与离线模型回放可并行。先判断 Laya 是否满足项目的中文原因辅助目标；发现具体质量缺口后才考虑替代模型。产品交付目标是可用的原因辅助工具。本人多次聊天仍为 N=1；离线数据不能证明真实填写率、填写耗时或业务价值。

## 服务端接口与本地验证

- `POST /v1/feedback-actions`：动作与 Gate Outbox 同事务，返回稳定 `event_id`，不等待推断。
- `POST /v1/feedback-actions/{event_id}/retract`：幂等追加动作撤销及取消消息。
- `GET /v1/feedback-actions/{event_id}`：从追加事件重建当前状态。
- `POST /v1/feedback-actions/{event_id}/attribution-events`：验证展示、原因码、显式操作与权限；用户选择与模型推测分开。
- 四维推断 Gate 的允许路径仍以 `pipeline_unconfigured` 拒识；独立 Laya 手动调用没有接入该流水线。聊天生成预算与原因推断预算分别判断。

在已核对版本的开发 checkout 中按锁文件安装并验证：

```powershell
uv sync --extra dev --locked --no-editable
uv run --no-sync ruff check src tests
uv run --no-sync ruff format --check src tests
uv run --no-sync pytest -q
```

本地中文路径曾触发 Windows Python 3.11 editable `.pth` 解码问题，因此使用普通安装。修改源码后运行 `uv sync --extra dev --locked --no-editable --reinstall-package whynote` 再验证；不要据本 README 自动更换已有 Laya 专用环境。

`uvicorn whynote.api:app` 默认业务拒绝，宿主必须注入可信身份与对象权限校验。在包含 demo 的候选版本中，`uv run --no-sync python -m whynote.demo` 提供本机虚构页面 <http://127.0.0.1:8765/demo>；这不是生产身份或云聊天验收。

完整 FR、公开来源准入、模型质量与发布批准分别维护。[质量基线](docs/quality-baseline.md)保留 2026-09-25 历史快照；当前进度以本页固定主干、开发计划和飞书原需求条目为准。生产上下文、auto-attach、自由文本 SLM 和训练导出保持关闭。
