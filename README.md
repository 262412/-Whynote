# 知因・Whynote

负反馈原因辅助标注的首个开发切片。依据 [技术开发文档 v0.1](https://my.feishu.cn/wiki/GuphweJs3iWwBRkBDjlcljA16bh) 和 [JEV 产品 PRD](https://my.feishu.cn/wiki/XG6SwutL0i3fyWkwmhScEEB86Gg) 建立动作与归因分离的服务端基础。

## 当前实现

- `POST /v1/feedback-actions`：先在同一 SQLite 事务写入动作事件和 Gate Outbox，返回稳定 `event_id`，不等待推断。
- `POST /v1/feedback-actions/{event_id}/retract`：追加动作撤销事件和取消消息；重复撤销不重复写。
- `GET /v1/feedback-actions/{event_id}`：从追加事件重建当前状态。
- `POST /v1/feedback-actions/{event_id}/attribution-events`：只接受绑定到已记录展示的明确用户动作；原因码必须实际展示，手动选择来源为 `user_manual`。本地测试宿主可登记菜单回执；真实宿主的可信展示上报接口尚未接入。
- 四维 Gate 规则及可信的入样概率记账；拒绝时原因保持空。当前允许路径以 `pipeline_unconfigured` 拒识，不读取上下文，也不调用供应商。
- TypeSafe Jev Choice/Noul 响应解析与概率校验；尚未接入真实 API。

查询、撤销与归因写入会用持久化的目标引用重新检查当前对象权限；撤权后返回 404 且不追加事件。权限边界与宿主待接接口见 [对象归属复核契约](docs/access-boundary.md)。

## 本地验证

### 三源无标签本机探索（M5-5 / D-21）

新增统一入口 `python -m whynote.explore`，支持 `prepare/run/resume/report/view`。
使用获准且固定版本的 HelpSteer3、WildFB、WildFeedback 文件；不要求人工标签或正式评测封存。
当前配置为单 CUDA worker、常驻模型、batch size 1，默认方案 C；输出为未确认诊断，质量指标为 NA。
下载与访问范围见[三源使用记录](docs/m55-source-use.md)，字段、失败和恢复语义见[探索契约](docs/m55-exploration-contract.md)。

下面是本机现有路径。先从核验过的干净候选构建并以非 editable 方式安装 Whynote；保留已可用的 Laya/GPU 依赖。
不要从有未提交改动的原目录重装。每次新批次使用新的输出目录。

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

`view` 打印本机带随机 token 的地址；用该地址打开报告，筛选结果并主动查看单个案例。
JSONL、汇总 JSON、HTML 默认仅包含元数据；正文和参考反馈留在受控输入库。查看会登记探索暴露。

- 数量：`--records 100` 表示每源最多 100 条源记录。每源 1000 目标用 `--records all --targets 1000`；实际扫描量与解析/关联排除单列。
- 范围：`--sources helpsteer3 wildfb` 选择来源；`--schemes A B C` 选择方案。数量必须落在使用记录的授权范围内，显式 `all` 不扩大授权。
- 进度：终端输出进度，`journal.jsonl` 逐槽持久化。可另用 `report` 刷新汇总；加载、失败、超限、拒识和中断各自计数。
- 取消：在该 run 目录创建 `cancel.request`。停止后保留该文件的审计副本并改名，再把上述 `run` 命令改为 `resume`。恢复只处理未开始槽；已开始但结果未知的槽不自动重试。
- 版本：恢复要求源文件、输入、模型、理由包、源码、运行时与配置一致；变更后创建新 run。源文件到期后拒绝执行或查看正文。
- 正式评测：原 `controlled_replay` / `self_review` 入口与标签规则保留；探索报告不产生质量 PASS，也不能直接作为盲评留出。

```powershell
uv sync --extra dev --locked --no-editable
.venv\Scripts\python -m pytest
```

本地目录包含中文字符，当前 Windows Python 3.11 对 editable 安装生成的 `.pth` 路径解码不正确，因此使用普通安装。改动源码后，可运行 `uv sync --extra dev --locked --no-editable --reinstall-package whynote` 再测试。

`uvicorn whynote.api:app` 可以启动 HTTP 进程并查看 `/health`，但业务接口默认拒绝请求。宿主平台须在 `create_app` 注入经过验证的身份解析和目标对象权限检查后才能处理反馈；不能把客户端传来的身份或目标 ID 直接当成权限凭据。

Open WebUI `v0.11.4` 的固定虚构数据联调使用独立 Action/Pipe；安装边界、事件证据和未完成项见 [S0 宿主联调](docs/openwebui-s0-integration.md)。该联调没有启用原生评分作为知因入口。

## 本地交互测试

Laya Multilingual 已提供[独立本机原因测试页](docs/laya-local.md)：在本机 GPU 上运行固定 checkpoint，手动输入问题/回答，输出带真实模型版本的未确认原因推测。该入口不消费聊天或反馈事件，未启用自动归因；效果与运行证据分开记录。

S1 个人云聊天的[接入契约与配置清单](docs/s1-cloud-contract.md)已准备待冻结。运行 `uv run --no-sync python -m qa.s1_mock_provider` 可启动本地虚构回包工具，覆盖完成、截断、断流、取消和服务失败；它不连接云服务，实际动态回答接入仍待 S1-2 实现。

安装开发依赖后运行 `uv run --no-sync python -m whynote.demo`，在本机打开 <http://127.0.0.1:8765/demo>。页面用虚构对象和每次启动独立的临时身份走点踩、常规原因选择、更正及撤销流程；菜单展示回执在渲染帧后登记，只有 `actionable=true` 时才可提交原因。仅监听本机，不读取真实问题/回答，也不调用模型。流程与限制见 [本地反馈闭环测试宿主契约](docs/local-demo-contract.md)。

本仓库尚未绑定真实产品平台、用户授权、快照与保留策略、预算账本、队列、真实模型调用、校准器或生产前端。具体接口路径是待平台评审的逻辑契约。请参阅 [开发决策与下一步](docs/development-readiness.md)。

Open WebUI 隔离实例的虚构数据实测与原生评分复用结论见 [S0 数据审计](docs/openwebui-s0-data-audit.md)；这份审计尚不构成知因与宿主的联调验收。

归因状态、展示绑定、UTC 时间及旧事件重放规则见 [归因与展示契约 v3](docs/attribution-contract-v3.md)。当前仅完成 Q-01 至 Q-03 的服务端修复；PRD 的完整链路和产品验收仍未完成。

### S1-2 动态回答开发切片

已确认的v1契约现有[动态回答接入与迁移说明](docs/s1-dynamic-delivery.md)。仅完成作者虚构复测；独立浏览器、评审和真实出站门槛仍待完成。运行配置默认关闭，见 `fixtures/s1-runtime.example.json`。
