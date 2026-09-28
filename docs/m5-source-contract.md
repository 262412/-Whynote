# M5-0a 三源映射契约 v1

2026-09-28；FR-04/05/14/15、TD-02/06/07/11/14、D-16/18。
输入基线：PRD 310、技术文档 312、main `52ac4db`。本文件定义独立离线工具，
不改变服务端事件、manual-v1 或研究台账。实现和合成验证不等于数据准入、独立审阅或完整验收。

## 本切片的范围

`python -m whynote.source_review --manifest <JSON> --output <新目录>`
读取三个来源各自的本地文件与人工关联/审阅表，输出统一的引用记录、排除记录和汇总。
不下载语料、不加载模型、不写动作/确认/gold/训练事件。当前 CLI **仅接受 synthetic**；
public 数据保持 HOLD，且在打开数据或审阅文件之前拒绝。真实语料读取须在逐源用途、
许可链、访问者、受控存储、保留/删除与小批范围签署后另交付入口。

## 文件与身份

- manifest 的 `schema_version` 为 `m5-source-batch-v1`；`sources` 每源至多一项。
- 每项固定 `source`、`revision`、`config`、`split`、`format`、`data_path`、
  `data_sha256`、`reviews_path`、`reviews_sha256`；路径必须相对 manifest 目录且不能逃出该目录。
- `mode=synthetic`、`status=admitted` 才读取；HOLD/excluded/未知状态均不读正文。
- 版本固定到开发计划三项 SHA；变更版本需显式更新契约与验证，不自动跟随 latest。
- 输入文件支持 JSON 数组、JSONL、gzip JSONL。文件大小和解压后大小均限制为 8 MiB，
  单批每源最多 1000 行。这是合成工具工程上限，不是获批真实样本数。
- row_id 为 JSON 数组的零基索引或 JSONL 的零基物理行号；空行/坏 JSON 保留排除计数。
  原始 id 只保存为字段引用，不回显任意字符串。文件 hash 固定时该索引可重放。
- `record_id` 由来源/revision/config/split/文件 hash/row_id/目标定位哈希得到；
  `mapping_version` 单列。同一输入重跑身份相同；输出目录必须不存在，不覆盖旧报告。
- 统一记录保留：来源定位、目标轮次/回答序号、context/target/feedback/label 引用、
  内容 hash、目标版本、反馈角色/标签来源、审阅与筛选/分割版本、任务/语言及 UTF-8 长度。
  manifest 和汇总不保存原文。生成的行级引用清单仍属研究数据，真实环境需受控保存。

## 三个适配器

1. **HelpSteer3 feedback**：只读 `context` 的 user/assistant/system 消息，末项必须为 user；
   分别将 response1 与 feedback1 数组、response2 与 feedback2 数组绑定。反馈为 evaluator，
   每个回答只有一条目标记录，评价人数不当作用户数；同题共享 context hash，不能跨集合。
   评价内容、edit、preference 等字段不进入预测引用。一个回答坏格式不丢弃另一个回答。
2. **WildFB**：只接受逐行关联表指定的 messages 目标 assistant 索引，前一项须为 user，
   后一项须为 user 且与 user_feedback 精确相同。关联表必须有 schema/关联审阅引用。
   history/text 不作隐式回退；label 1–4 作为 automated 标签引用，不当用户原因。
   无法证明直接后续话语对应目标时排除，不搜索“看起来相似”的文本。
3. **WildFeedback**：原文件 schema 尚未实测。适配器要求审阅表给出原会话数组的 JSON pointer、
   目标 assistant 索引及关联/结构审阅引用，验证相邻原用户反馈；仅接受
   `sat_dsat_annotation` 配置。`wildfeedback` 偏好对和 `user_preference` 在此切片显式 HOLD，
   不把 chosen、自动解释或生成优选回答转成 original_user。实际文件不满足这些条件则继续 HOLD，
   不能把合成示例说成已兼容真实 schema。

消息引用截止目标回答。未来轮次只在 feedback 引用中出现；原文/否定词不改写、不截断。
`prediction_refs` 与 `feedback_refs` 分离，后续 runner 只能解析前者，并须重新核对文件 hash。

## 人工审阅表

独立 JSON 对象以 row_id 为键。`linkage` 包含 `schema_review_ref`、`linkage_ref`（UUID）、
`target_index`，WildFeedback 另需 `conversation_pointer`。HelpSteer3 结构不依赖该项。
`targets` 以 `response1`/`response2` 或 `conversation` 为键，每项目可包含：

- `review_ref`、`reviewer_ref`：UUID；`review_version`：本切片固定 `m5-case-review-v1`。
- `diagnoses`：缺类别 `missing_category`、分类错误 `classification_error`、缺材料/历史
  `missing_context`、证据不足 `insufficient_evidence`、多问题只展示一个 `multiple_issues`；
  允许多项或空列表，未审阅保持 null。这里是问题诊断，不是原因 taxonomy 或 gold。
- `task`：code_rewrite/general/other/mixed/unknown；`context_language`、`feedback_language`：
  zh/en/mixed/other/unknown。由审阅给出，缺失为 unknown；不将编程语言当自然语言。
- `group_ref`（UUID）、`partition`（exploration/holdout/unassigned）、`split_version`
  （UUID）：人工确认的会话/近重复分组。未提供保持 unassigned，不自动声称去重完成。
- `screening_ref`（UUID）：本次筛查记录引用。缺失时映射可用于审阅，但不具备回放资格。

精确相同 context hash、目标 hash 或 group_ref 横跨 exploration/holdout 时，相关记录全部
标记 split_conflict 并取消回放资格；相同 group_ref 必须使用同一 split_version。
精确 hash 检查不能证明近重复/派生源重叠已全部排除，仍需人工审阅。
没有审阅表项时输出未审阅目标，不造任务/语言/失败原因。表中多余 row/target 或非法字段拒绝，
避免误拼键导致审阅静默丢失。此表由本地操作人提供，UUID 只是审计引用，不验证外部签署真实性。

## 报告与回滚

逐源报告 status、读取行数（未读为 null）、目标数、排除数/原因、未审阅数、各字段缺失率、
多因诊断计数/组合矩阵、任务和双语言覆盖、输入长度分桶。所有比率包含明确分子/分母；
无分母输出 null。无准确率、用户耗时、真实失败率；合成统计标为 synthetic。
源级损坏不会阻塞其他源；不把空输入、坏格式或超限写成成功。记录与汇总分开保存。
停用工具即可回滚；没有数据库迁移，也不删除旧事件/报告。保留审阅前后的独立输出目录。

## 准入和后续退出条件

产品/数据仍需实际案例、多因审阅和分歧记录；三源分别完成固定文件 schema 实测与准入。
算法据这些证据选择 M5-0b 理由包，随后冻结 M5-1 样本/标签/留出集/阈值。
QA 及非作者审查、适用责任人签署独立进行；本工具的合成通过不关闭这些事项。
