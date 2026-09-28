# M5-2a 展示与确认记录契约 v1

2026-09-28；输入 PRD 334 / 技术文档 332；基线 main c2ee7a3。
FR-10/11/13/14/15，TD-04/06/09/10/11，D-13/18，Q-09/10/21/22、S1-3R。
本文件先于代码固定开发契约；产品、客户端、数据责任人的签署及独立 QA 仍待完成。

## 范围与数据

在现有 TrialStore、研究回答台账、events 和只读 research_report 中实现。
仅受控本地 Python 入口；没有新增浏览器路由，不调用模型、不读取正文。
服务端同时启用 `research_enabled` 和 `suggestion_research_enabled`，且 `mode=mock`、
`enabled=true` 才可写。既有 cloud 限制继续生效。建议来源固定为 `synthetic_model`，
显式响应来源为 `user`；来源组及 self_report/evaluator 沿用回答登记，不生成 gold。
模型身份使用服务端 `suggestion_model_revision`（40 位十六进制不可变版本）；
UI 固定 `m5-suggestion-mock-v1`；理由、criteria、模板和目录摘要使用 M5-0b 常量。

每个事件继承 tenant、event_id、record_id、时间和 trace；payload 只保存版本、UUID、
枚举及原因 ID。绑定原动作主体、完整 target_ref、已登记且合格的 attempt、候选集合摘要、
有序 1–3 候选、model/package/criteria/template/UI 版本。不得包含模板正文、引用正文或密钥。
无登记、旧回答及旧事件不补写。真实采集的用途、保留/删除/副本处置仍需单独契约。

## 事件与状态

| 事件 | 条件与含义 |
|---|---|
| `m52_suggestion_generated` | 可信本地调用提供经 M5-0b 校验的合成选择；suggestion_id 使用 UUID。suggested 带候选；unknown/no_match 无候选，均不成为用户原因 |
| `m52_render_reported` | 完整展示绑定须与生成快照相同；display_id 在生成时预约为 UUID，候选顺序不能改变。仅证明客户端报告渲染，不证明看见；客户端不能自行换 display |
| `m52_response_recorded` | 引用当前 suggestion/display；yes 确认指定候选，no 否定指定候选，none_matched 拒绝整组；skip/close/decline 分别为跳过/关闭/不愿说明。0 点击没有事件 |
| `m52_suggestion_invalidated` | 撤销当前模型建议，后续展示/响应拒绝；不清除既有用户确认 |

yes 才能新建研究确认原因；correct 明确改为当前展示内另一个候选，必须已有本契约确认。
每次响应携带 `previous_response_id`，与当前动作最后一次本契约响应作 CAS；首条为 null。
同展示已有响应后只能 correct；其他新意图须生成新建议并预约新展示。correct 保留完整前序；拒绝无效更正。
no/none_matched/skip/close/decline 都不清空既有用户原因，不撤销点踩。
与 manual-v1 的清空语义不同，使用新的事件命名空间和研究投影，不映射旧 reason_code。
旧投影、API、菜单白名单保持原样；旧原因与本契约确认分别报告，不能拿研究投影冒充正式界面。
既有 `action_retracted` 使本契约当前确认清空并关闭后续写入，保留历史确认计数。
模型撤销与动作撤销分别计数。无确认=`none`；unknown 是生成结果，不是用户原因。

## 原子性、失效与幂等

所有写入在同一个 `BEGIN IMMEDIATE` 中复核服务端开关、主体、动作 active、当前回答的
generation receipt 与版本、冻结研究登记；复用 TrialStore guard 和撤权/删除失效钩子。
宿主删除/编辑必须先调用现有 invalidate；本切片不新增真实宿主入口。
只接受当前建议及其预约的 display；期限沿用既有 60 秒工程时限，从生成预约时刻起算，等于截止时间即过期。
响应入口在等待数据库锁之前捕获服务器 received_at，按此时刻验期限；提交前仍复核全部非时间准入。
按时到达后等待锁不会被误判迟到，撤销先提交仍阻止响应。收到时间早于生成时刻的墙钟回退拒绝。
新建议预约即取代旧建议；即使新建议未渲染、关闭或到期，旧回执也不能恢复可提交状态。
模型/目录/UI 版本不符、换回答、撤权、删除、动作撤销、研究停用、建议撤销均拒绝新增写入。
同一事务内记录业务事件；不创建推断 Outbox，不影响既有动作/Outbox 原子性。
request_id 是 UUID，在单一动作的 M5-2a 命令内唯一；相同命令重试返回同一 record_id，
换内容冲突。合法重复展示不增加计数，已失效后的重复请求也须先过当前准入检查。
请求处理与撤销按数据库事务顺序生效；撤销先提交后，排队的写入不能提交。

## 报告与兼容

沿用只读连接和固定回答来源分组；统计截至 as_of 的事件，单位为建议组。
报告生成数、可展示数、已报告渲染数、有效响应组数、确认组数、各显式操作次数、撤销数；
生成→渲染以可展示组为分母，渲染→响应以已报告渲染组为分母，零分母为 null。
单列 unknown/no_match、未渲染、待响应、到期未响应、渲染未知、无生成的动作数。
关闭是客户端生命周期事件，单列且不算有效响应；更正和重试不重复增加响应组分母。
不写入 reason_unresponded，按截止时刻派生未响应；撤销/失效不删除历史分母。
新事件追加到已有表，无 schema 改写；旧数据库无 M5-2a 事件时返回零和缺失数。
回滚关闭新开关，旧代码忽略新事件；研究确认仍保留审计，不能假定旧 UI 能显示新确认。

## 验证与退出

合成验证正常流、未知/无匹配、重复/冲突/CAS、顺序/版本绑定、过期、撤权/删除/换版、
在途撤销、停用、来源隔离、只读时点重放、无正文/密钥及旧投影兼容。
开发证据不替代独立 QA、责任人签署、M5-1 真实质量门槛或 M5-2b 本人试用。
