# S1-2 动态回答与常规菜单：开发交付

状态：开发实现与虚构数据复测；浏览器、独立验收、非作者批准及限定试用结果签署未完成。真实云调用、本人数据采集和生产未放行。

## 1. 基线、授权与范围

- 用户于 2026-09-27 明确确认 [S1-1 契约 v1](s1-cloud-contract.md)，要求推进 S1-2；DeepSeek、100 CNY 总限额及 v1 参数沿用该确认。
- 分支 `codex/s1-dynamic-feedback` 依赖 PR #25 的 `1475e2c3c727b25610ff84caf8e9e7e1dd8b9d35`。合并顺序为 #25 → 本片；不能把依赖视为已合并或已获独立批准。底层 main 基线为 `cdc0673fe933ddb9c0686aa09cdeb7eef7b76eb2`。
- 映射 FR-01/02/11/13/14、TD-01/04/05/06/10/15、D-13/15/17、Q-09/21/22。此片只接单人文本生成与已有常规菜单，不做原因推断、auto-attach、来源指标或研究结论。
- 固定 Open WebUI 0.11.4，SHA `8bd8b4fac5e059578ac0c74b3c18d11139f88b7d`，顺序应用 native、manual timing、S1 三份补丁。测试检查整个补丁栈与磁盘实际源码一致。

## 2. 已实现行为与位置

| 行为 | 实现 |
| --- | --- |
| 受控动态生成 | `integrations/openwebui/s1_pipe.py`：宿主认证用户、指定实例用户、新建自有会话，读取服务端保存的当前分支；最多4个已完成文本历史轮次。复用宿主 OpenAI 连接配置和 aiohttp pool |
| 固定请求 | `src/whynote/s1_provider.py`：唯一批准端点、禁重定向、非思考、1024输出token、10/20/60秒连接/读取/总超时；单请求，无自动重试。输入正文合计最多8192 UTF-8字节 |
| 回答资格 | `src/whynote/s1.py`：自然 stop + DONE + 非空正文才产生可信完成回执。反馈再检查宿主 done、归属、父消息与回答 HMAC、模型、客户端显示内容和当前回执；length、断流、中止和错误均不合格 |
| 菜单复用 | `s1_action.py` 复用原常规操作与 Q-21/Q-22 逻辑；S0 只增加标题、提示、渠道及存储获取扩展点。前端为 S1 点踩生成 click UUID；重试和重连不重开已完成菜单 |
| 撤回 | `s1_retract_action.py`：独立撤回当前对象版本的点踩；重复撤回幂等，再点踩产生新动作 |
| 失效 | `s1_host.py` + 宿主补丁：编辑已有消息会使该会话全部当前回执失效；重生成替换当前 attempt；删除/撤权先撤销回执。保守失效不影响历史审计 |
| 事务与竞态 | 动作/Outbox 仍同事务。每个反馈事务先检查准入与回执版本，避免验证后删除/换版仍写入；单并发和费用预留在 SQLite 写事务中检查 |
| 出站与副本边界 | 隔离实例关闭后台标题/标签/追问和内置工具。禁止直连 OpenAI/Ollama/Anthropic、嵌入、工具、检索、上传、分享/导出/导入/复制/压缩及事件 webhook 路由；宿主普通事件去除 data/message，仅保留本机 Socket sink |

宿主聊天库仍保存正常聊天正文。浏览器显示、WebSocket 流和用户自行复制内容不可能等同于服务端零副本；本片没有证明浏览器缓存清除、系统备份排除或供应商删除。关闭分享/导出的运行标志由专用启动器设置，不能只安装 Pipe 就声称边界生效。

## 3. 费用与恢复

- `s1_generations` 只记对象 ID、带密钥摘要、状态、模型/模式/价格版本、token计数及费用；不保存问题、回答、云密钥或供应商错误正文。
- 每次按 **2,000,000 输入 token × 2微元 + 1024输出token × 8微元 = 4.008192 CNY** 预留。输入预留高于供应商文档的1M上下文上限，不依赖汉字/token估算；按高峰无缓存优惠计费。实际输入仍限8192字节。
- 有合法 usage 时按同一保守单价结算；缺失/非法 usage、失败和取消保留完整预留。所有状态均参与100元累计检查，重启不清零；最多允许24次全部费用未知的请求。
- 单价基于2026-09-27官方人民币价格。项目账本不等同于供应商发票；实际调用前核验账户价格、其他应用是否共用key及平台扣费，变价先停止并更新契约/测试。
- 崩溃遗留 pending 会阻断新生成。恢复须先关闭入口，确认请求已终止，对账供应商后由责任人记录处置；不得删除账本或把未知费用设为0。该人工恢复流程尚未做真实供应商演练。

## 4. 配置与运行

`fixtures/s1-runtime.example.json` 是实际加载器的配置形状，默认关闭；原 `s1-cloud-config.example.json` 是交接清单。云key仍只放宿主专用连接，不放配置、提交或聊天。

运行要求：唯一进程/worker、独立数据目录、绑定127.0.0.1、仓库根目录和 `src` 在 PYTHONPATH 中、已打补丁并构建的固定宿主。仓库根目录用于加载共用 Action；不能只安装 wheel 后忽略宿主集成目录。

虚构复现（native-venv 由锁定 requirements 安装）：

```powershell
var/native-venv/Scripts/python.exe -m qa.s1_mock_provider --port 8126
var/native-venv/Scripts/python.exe qa/s1_browser_host.py serve --source var/s1-upstream --data-dir var/new-s1-synthetic --port 8127
var/native-venv/Scripts/python.exe qa/s1_browser_host.py provision --data-dir var/new-s1-synthetic --base-url http://127.0.0.1:8127
var/native-venv/Scripts/python.exe -m qa.s1_http_probe --data-dir var/new-s1-synthetic --output var/new-s1-http.json
```

启动器仅生成 mock 配置，不读取云key、不接受已有数据目录。它创建虚构 admin/alice/bob，只有 alice 可用试用模型。模拟服务只接受固定虚构文本及最多4轮固定历史。脚本回调不是浏览器交互计时证据。

## 5. 迁移与回滚

- EventStore 原 schema/事件语义不变。首次加载追加 `s1_instance`、`s1_chats`、`s1_generations`、`s1_current`；重复加载安全。费用审计列通过缺列检测追加，旧开发记录保留 NULL，不反推历史 token/模型。
- 历史 S0/常规动作仍可按原逻辑重放；不会自动升级为 S1 回执。缺少可信回执、旧 NULL 模式/模型的记录均不开放动态反馈。
- 同一库不接受另一个 instance ID；试用撤权不能通过重新 enroll 恢复。重新生成是新 attempt，保留旧费用和事件。
- 宿主与 Whynote 是两个 SQLite 库，未声称分布式原子事务。编辑/删除先失效回执，因此宿主删除失败可能导致仍存在的回答暂失反馈资格，安全地要求新生成；不补写旧回执。
- 回滚将 enabled 设为 false，停生成与新反馈，停宿主和模拟/云连接，保留账本与事件；拆除 S1 补丁和 Functions 后可使用旧 S0。不要删除新增表或为回滚重写历史事件。
- 真实试用结束后7天内清除本地正文/运行记录仍须运行处置演练和签署；本轮虚构审计证据保留，未执行实际用户数据清除。

## 6. 证据与剩余门槛

证据在 `qa/evidence/2026-09-27-s1-dynamic/`。本轮为作者开发复测，不称独立验收。

- 核心及既有 QA 回归、原生宿主/流式协议回归、真实登录 HTTP/WebSocket 对账分别记录，不把套件相加称独立样本。
- 前端已从固定源码构建；浏览器工具连接返回 `nodeRepl.fetch request failed`，没有浏览器 UI 通过结论。
- 供应商实际数据条款、账户设置/扣费、真实超时与停止/留存处置，S1-3 来源/指标、独立动态对象回归及非作者 APPROVED、限定试用结果签署仍是开放项。
- 未使用云密钥、未调用真实模型、未导入公开语料、未开始本人试用。完整 FR 和生产继续 NO-GO。
