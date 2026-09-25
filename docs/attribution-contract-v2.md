# 归因与展示契约 v2

状态：Q-01 至 Q-03 的服务端安全收敛；对应 PRD FR-01、FR-10、FR-11、FR-13。产品/数据仍需签署手动选择、拒填、跳过与未响应的最终语义。飞书核对版本：PRD 修订 66、技术文档修订 6（2026-09-25）。

## 展示与用户动作

`reason_displayed` 是客户端实际渲染后的可信确认事件，不表示仅向客户端下发了选项。它记录 `display_id`、所属 `event_id`、`mode`、`ui_version`、实际展示的有序 `shown_reason_codes`；没有用户原文。当前由受信任的宿主集成调用 `EventStore.record_display`，本切片不开放公共展示写入接口。

| mode | 合法用户动作 | 附加约束 |
|-|-|-|
| `manual_menu` | `reason_selected`、`reason_declined`、`reason_skipped` | 选择值必须在本次展示的原因码中；新提交的手动原因来源为 `user_manual` |
| `model_suggestion` | `reason_confirmed`、`attribution_invalidated`、`reason_declined`、`reason_skipped` | 仅当前未确认推测可展示和确认；展示码与推测一致 |
| `edit_menu` | `reason_edited`；未确认推测可跳过 | 须已有用户原因或未确认推测；编辑结果必须实际展示 |

所有归因写入都须携带所属动作的 `display_id`。服务端检查显示记录属于同一租户/用户/动作、是最新展示，且模式与原因码匹配；一个展示最多消费一次，重试只通过相同 `Idempotency-Key` 返回既有结果。`reason_declined` / `reason_skipped` 不能清除已提交的用户原因；此类更改须走新 `edit_menu` 和 `reason_edited`。未响应由后续独立超时流程定义，本切片不从沉默推断确认或拒填。「都不是」及模型候选展示仍待产品定义。

## 当前投影与历史

`action_retracted` 是动作终止事件。其当前投影固定为 `action_status=retracted`、`attribution_status=none`、`attribution_source=none`、`reason_code=null`；此前的 `confirmed`、`selected` 等事件仍留在追加历史中，供审计重放。推测撤销仍只产生 `attribution_invalidated`，不改变动作状态。已提交用户原因优先于之后到达的模型结果。

本次投影版本为 `2`。旧事件不删除、不改写；同一有序事件流在新 reducer 下确定性重放。旧版没有 `reason_displayed` 的历史归因事件仍可读取，其 `reason_selected` 来源保持原有的 `user_selected`；新写入必须提供展示记录。新 `reason_displayed` 的 `event_version=1`，含展示绑定字段的归因事件为 `event_version=2`；SQLite 表结构不变。旧版 reducer 会忽略新事件类型。若回滚代码，应先停止新归因写入，再切回旧版读取；不能把回滚当作取消已记录的用户动作。

## 时间、幂等与失败

客户端时间可省略；提供时必须为带 `Z` 或明确偏移量的 RFC 3339 字符串，服务端规范化为 UTC `Z`。事件 `occurred_at` / `recorded_at` 一直由服务端生成 UTC 时间，读取 envelope 必须返回 `event_id` 与 `record_id`。无时区、非字符串或格式错误输入返回 422。

幂等键仍按 `tenant_ref + actor_ref + Idempotency-Key` 保存哈希，请求指纹包含动作类型、`event_id`、`display_id` 与原因码。缺少展示、候选不匹配、展示已消费或非法状态转换返回 409；跨用户动作读取返回 404。任何失败都不追加归因事件；上下文、预算、供应商调用和自动附加保持关闭。

## 验收与未完成项

本切片验证正常选择/确认、无展示或伪造展示、候选不匹配、重复请求、拒填冲突、撤销后投影、旧事件重放和 UTC 时间。FR-11/FR-13 仍是部分实现；展示上报的客户端证明、`reason_unresponded` 与「都不是」语义、真实门控/快照/预算/E-F-T 路由需后续单独评审和开发。
