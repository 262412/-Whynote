# M54A-E4 受控材料与固定执行器契约

2026-09-29；FR-05/06/09/14/15，TD-02/07/08/11/14，D-20。
依赖PR #41/affcc6c已测的单人标签/门槛报告；独立工程QA原报告不改。

## 边界

HelpSteer3本次仅获准固定文件、前200条有限探索审查，目录和缓存均在
`D:/PythonProject/jev项目/var/research/helpsteer3-review/`。审批见[准入记录](helpsteer3-review-admission.md)。
这是 `ADMITTED_FOR_REVIEW_ONLY`，不满足固定评测的 `ADMITTED` 批次要求；其他来源仍HOLD。
评价者反馈仅进入独立的关联/审查记录，不进入预测材料，也不代表原用户动机或本人标签。

## 真实投影与预算

固定gzip/JSONL只读取获准行范围；一条源记录选择response1或response2中的一个目标。
预测只序列化context的role/content和所选answer，禁止第二回答、feedback、标签、语言/任务参考值
进入进程。完整上下文保留；original_code仅从最后user消息中的完整代码围栏认定，不凭数据集domain猜测。
conversation组绑定规范化上下文hash；near_duplicate保留本人复核分组。
input_id绑定来源/revision/源文件hash/行号/目标，state_sha256绑定实际UTF-8预测字节。
已审查记录以及卡片示例的上下文摘要进入暴露清单，验证集不能与其精确或规范化上下文组重叠。
近重复分组还需本人复核，程序的规范化指纹不等于语义近重复审查已完成。

用固定Laya tokenizer和其encode_text/render_options测量全部可能问题，包括A、B/C路由、所有理由与回退。
预算为所有问题的最大值，记录tokenizer文件hash和预算明细hash；无静默截断。
原始输入超限仍保留样本和预算报告，不会送进推理；全部有效预算的错误/超时/拒识按既有门槛统计。

## 执行与环境

setup/标签校验在监督进程完成；预测子进程只接收投影、方案与模型路径，不能加载标签文件。
操作人在执行前给出新冻结回执。实际源码、投影、预算、环境和标签绑定不一致即拒绝/停止。
固定60例、六种顺序各10次，恰好180个槽位；只追加started/completed记录，逐槽fsync，run_id、
输入hash、方案和序号可重建，不自动重试。中断不覆盖原目录；本版暂不提供续跑，重新运行需新协议/批次授权。

进程互斥在读预测正文和创建模型前取得。每槽60秒含子进程启动/模型加载/路由/回退；超时或取消先
终止进程树、等待回收，再释放互斥。合法unknown/no_match是拒识，不转成技术错误。
计划内失败记录后继续；源码/输入hash漂移、无记录失败或其他安全事件停止，未尝试槽位保持缺失，不能补成功。

Windows以无网络capability的AppContainer启动，并将子进程归入kill-on-close Job；只向临时SID授予
运行时/源码/固定模型的读权限，退出撤回，不授予源数据或标签目录。缓存目录只读且位于项目内，
不允许运行时下载/编译。环境核验须有真实token属性、TCP/UDP受控接收端对照及实际模型合成调用；
不能只凭socket monkeypatch、错误码或声明环境通过。网络依据见
[Microsoft AppContainer说明](https://learn.microsoft.com/en-us/windows/win32/secauthz/implementing-an-appcontainer)。

合成执行与真实执行分开，合成stub永不接受real模式。真实批次/本人封存标签未就绪时只运行合成验证。
AppContainer验证范围不替代独立安全评审或部署批准，也不修改既有在线宿主的运行边界。

## 本人标注

探索30与验证60必须是不同组；获准范围内已展示的200行只可用于探索。
首标、至少48小时后的固定20例盲复标、最终标签依次保留。现有self_review契约校验三轮记录和48小时差值，
不代填本人判断，不伪造日期，不立即补成48小时，不把本人记录称为独立验收。

## 使用入口与兼容

新入口 `python -m whynote.controlled_environment --help` 仅执行合成环境核验，产出源码/运行时代码与原生库摘要、网络/并发/取消和A/B/C实际模型回执。`VERIFIED_FOR_FREEZE` 表示该环境候选通过开发核验，仍须独立复验与运行人冻结。

`python -m whynote.controlled_replay setup.json exposures.json environment.json NEW_OUTPUT_DIRECTORY` 只接受real模式、ADMITTED批次、稳定且封存的self_reviewed_blind标签和匹配冻结记录。运行前重新核对源码、运行时和协议hash，实际复测网络/进程控制及合成模型，再读取获准投影并复测所有预算；随后更新实际started_at/run_id并执行固定180槽位。准备/预热不计样本，槽位计时含实际启动/加载；每个槽位重新启动同一模型。任何准备失败均不把真实槽位写成已尝试。

exposures按batch_id映射到 `{"schema_version":"m54a-exposed-groups-v1","groups":["hash"]}`；其digest等于准入的exclusions_sha256。环境回执的outbound/concurrency/cancel摘要绑定freeze对应字段；protocol_sha256为协议文件字节SHA256，source_sha256为controlled_replay.source_hash()。既有v1报告/标签/事件契约不变；A的other_or_unknown哨兵映射为拒识，不充当第八种gold理由。

Windows实际运行显式设置TORCHDYNAMO_DISABLE=1，防止Transformers类定义的装饰器隐式启动编译；固定运行时和模型路径只读。取消核验是在真实进程等待处注入KeyboardInterrupt，随后查询Job活跃进程数为0；不称已实际操作控制台取消。外部UDP没有受控接收端，本轮只记录本机IPv4/IPv6 TCP/UDP交付对照与无网络capability，不冒充外网泄漏试验。

源码指纹覆盖whynote下全部.py文件（包括未跟踪文件）；运行时指纹覆盖解释器/依赖树.py/.pyd/.dll/.exe/.json，模型五文件在每次加载时核验。源/环境文件若更新需重新核验并冻结；运行器不自动续跑或覆盖旧结果。

### 失败阶段与不可观测路由

Q-38/Q-39修复约定：uncaught_error通过错误信封、ReplayError或完整prediction到达监督器时，
先追加并fsync该槽的completed失败记录，再追加stopped并抛出固定uncaught_error；不启动下一槽，
不写完整bundle/report。completed_slots表示已持久化结果的槽数，包含失败，不表示成功数。
停止记录仅保留固定分类，不复制异常正文。原有journal字段不变，旧审计记录不迁移。
model_load_failed仅用于tokenizer/SDK/模型加载与模型配置核验；加载完成后的初始化或策略未知异常
使用uncaught_error。SDK预测的既有worker_failed及其他计划内错误保持可记录、可继续。

SDK推理异常固定为worker_failed并保留infer_scheme已完成路由及失败阶段；模型尚未加载才使用model_load_failed。没有完整worker回执的超时、传输或非法响应按route_status=failed表示路由结果不可用，不能声称路由从未尝试，也不纳入条件路由遗漏分母。无法观察到的中间结果不补写为成功。预算/材料未通过及模型加载失败使用not_attempted。此为新执行器到既有v1统计的映射，旧事件和标签契约不变。
