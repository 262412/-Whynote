# S1-3 记录与报告契约 v1

2026-09-28；FR-11/14/15，TD-03/06/09/11/14，D-16/17。
依据 PRD/技术文档 271 和 PM-20260928-S1，本版仅实现获准的虚构开发和对账。
字段及兼容规则先于实现固定于本文件；独立契约评审、真实研究参数和结果签署仍待完成。

## 1. 准入与来源登记

- 新能力须服务端配置 `research_enabled: true` 且 `mode: mock`；其他组合不产生研究记录。
  cloud 配置显式请求研究记录时拒绝，不凭配置中的审批字符串开启真实采集。
- 登记仅提供受控本地 Python/CLI 入口，无浏览器写入口。操作者必须具有服务端配置和数据库访问权。
  身份取自配置，传入 Principal 须匹配实例和唯一主体。会话须先经宿主验证归属并 enroll；登记本身不授予会话权限。
- 来源为 `self_natural`、`scripted`、`public_replay`；前两者角色 `self_report`，后者 `evaluator`。
  这是虚构场景的来源标记，不表示已经采集真实自然使用或导入公开材料。
- 严格清单字段：`registration_id`、`study_ref`、`protocol_ref`、`context_ref`、`feedback_ref`
  使用不透明 UUID（feedback_ref 可空）；`source_kind` 使用上述枚举；
  `public_label_origin` 为 `none`、`original_user`、`evaluator` 或 `automated`，仅公开回放允许非 none。
  不接收正文、原始标签值、自由文本笔记或模型建议。
- `study_ref`/`protocol_ref` 须等于服务端配置中的 `research_study_ref`/`research_protocol_ref`；
  这两项在启用虚构研究记录时必须提供。`context_ref` 必须为已准入的当前 chat_id。
  `feedback_ref` 若非空，必须解析为同实例、主体、会话的已有 Whynote 动作。
  此处不提供公开原始反馈内容导入；`public_label_origin` 仅记录模拟场景中的来源枚举。
- 每次登记绑定实例/主体/会话，服务器记录时间；变更追加，传 `expected_registration_id` 校验前序。
  相同 registration_id 和内容重试返回原登记；改内容冲突；旧请求重试不让最新登记回退。
- 每个 attempt 在 reserve 同事务冻结当时登记。后续登记仅影响未来 attempt，不回填旧回答。
  无登记的虚构 attempt 仍可记录，但报告归 `missing`，不自动归自然组。
  浏览器提供 source_kind、registration_id 或 study_ref 等分组字段一律拒绝。

## 2. 合格回答与生命周期

- 追加研究 attempt、状态记录和合格回答表，均在现有 TrialStore 数据库；无正文/密钥副本。
  不修改既有动作、原因、Outbox 的 schema 或语义，不把公开标签转换成用户选因事件。
- reserve 记录 attempt 及来源/版本快照；finish 记录 awaiting_save 或 incomplete。
  只有原 `confirm_saved` 的可信保存检查通过，才在同一事务记录 eligible，attempt 唯一。
  保存回调重试不增分母；stop 以外完成/length/中止沿用 S1-1 不合格规则。
- 回答主键为 attempt，携带已有目标版本。重生成产生新 attempt/版本，旧 attempt 追加 superseded。
  invalidate 追加 invalidated，revoke 追加 revoked；宿主现有删除路径使用这两类失效，
  v1 不伪称能从旧钩子进一步区分删除与编辑。失效后恢复原文不能恢复反馈资格。
- 合格历史不因撤权、删除或重生成而缩小；报告单列截至观察时点已失效的合格项。
  这是虚构记录的工程对账口径，不代替真实研究的删除/匿名化/保留方案。
  真实采集前须审查这些元数据的保留和全部副本处置；继承 S1-1 已签本地留存上限。
- 旧数据库仅新增表，无历史回填；旧回答的研究资格/来源未知，报告给出覆盖缺口数量。
  回滚关闭 research_enabled 或退回旧代码；已有表保留供审计，事件重放不变。
  关闭标记停止新增研究 attempt；已追踪 attempt 的保存/失败/失效历史继续对账，不丢在途记录。

## 3. 固定观察范围与只读报告

- 报告必须指定实例、主体、`since`、`as_of`，带时区；区间为闭区间。
  以首次合格时刻选择回答，生命周期与动作只观察截至 as_of 的内容。
  报告通过只读连接、单个 SQLite 读事务完成，不构造会迁移表的 EventStore。
- 自然点踩率：至少有一次合格成功点踩的唯一回答 / 自然组合格回答。
  同回答撤销再点踩的多个动作不重复增加回答分子；动作数及填写率仍单独保留各个意图。
  脚本与公开回放分开；参与者按主体去重，本人多会话仍 N=1。
- 仅匹配实例、主体、完整目标类型/id/version、S1 channel、manual-v1 的动作纳入。
  来源来自冻结登记；动作事件中的 user_selected 仍只表示操作者亲选，报告另标 self_report/evaluator。
- 复用 manual-v1 的 24 小时工程响应窗、主动/总耗时、跳过/拒填/都不是/关闭、更正、
  迟到、未响应及展示未知语义；窗后动作撤销另报。工程响应窗不等于研究观察期。
  填写率分母保留撤销动作。无计时为 missing/legacy，不补 0；公开历史材料时间不适用，
  本轮重新操作菜单产生的计时才是 evaluator 操作时间。
- attempt 固定 mode、provider_model、pricing_version、host_sha、patch_sha256、config_ref、
  model_revision、UI 和协议引用；不可取得的版本为 null 并计缺失，不伪造不可变模型版本。
  不同版本组合列出独立 strata，来源汇总只作描述性对账。
- 输出仅元数据和聚合数，不输出问题/回答、配置密钥或完整事件 payload；不是训练导出。
  没有推断时 attribution_invalidated 和模型来源混淆指标为不适用。

## 4. 固定验收与运行边界

自然 2 答/1 踩，脚本 3 答/3 踩，公开模拟 2 答/evaluator 分列；中止与 length 排除。
覆盖登记越权/重试/竞争/变更，旧库、缺来源、全部保存回调、生成重试、失效/撤权/重生成，
时间截止、来源隔离、点击重试、更正/撤销/迟到/未响应及缺计时；核对数据库只读和无正文。
测试数量不是研究样本量。研究观察窗、时长/样本量、阈值、价值量表和停止规则仍待采集前签署。
公开真实回放仍受 S1-0 三源 HOLD 阻断。Jev/Laya、真实云调用及 S1-4 不在本切片内。
