# HelpSteer3 有限下载审查准入记录

日期：2026-09-29。状态：**ADMITTED_FOR_REVIEW_ONLY：本人已确认，有限下载与前200条机器结构审查完成**。
需求：M54A-E1/E4、FR-05/14/15、TD-07/11/14。此记录只批准有限探索审查，不批准真实模型评测。

| 项目 | 已确认安排 |
| --- | --- |
| 来源 | NVIDIA HelpSteer3；仓库 nvidia/HelpSteer3 |
| 固定 revision | f6d145777bcbde96137596340fab89793acd1031 |
| 文件 / 大小 | feedback/validation.jsonl.gz；5,275,463字节 |
| 预期 SHA256 | b86f71581a83f610ec5416029422f84ff1fc005e77b56c7634486ef9f6f12ceb |
| 用途 | 本机schema、目标回答/评价者反馈关联及敏感内容审查；不训练、不上传正文、不调用外部模型 |
| 范围 | 最多前200条源记录，0-based行号0..199；一条含两回答仍算一条源记录。完整压缩文件落盘，解压/展示最多200条 |
| 目录 | D:/PythonProject/jev项目/var/research/helpsteer3-review/；下载缓存、临时解压和审查产物均在该目录 |
| 访问 | 限定本人及本机Codex在本人账户下进行审查；不新增共享/云同步，不将正文/模型输入提交Git |
| 留存 | 从实际下载起保留30天；到期删除本批源文件、缓存、解压与含正文衍生文件；不另作备份，仅保留无正文的摘要/审计记录 |
| 内容筛查 | 本机研发先核查字段/关联并标出敏感材料，项目本人决定可用范围；无法判断的记录排除。公开许可不替代内容筛查 |
| 暴露与用途 | 所有已展示/人工审阅记录及其会话/近重复组进入探索暴露清单，不能进入盲评留出；卡片示例若匹配同组也排除 |
| 反馈来源 | HelpSteer3反馈标为evaluator，不是原用户点踩动机，也不是本人盲评标签 |
| 批准 | 本人回复“采用上述准入安排”；记录时间 2026-09-29T09:00:14.607557+00:00；会话引用 user-reply-call_8HIKo9xFSuTvStcTCMnLIEZZ-adopt-arrangement |

## 来源和许可链核对

固定版本[数据卡](https://huggingface.co/datasets/nvidia/HelpSteer3/blob/f6d145777bcbde96137596340fab89793acd1031/README.md)
标注CC-BY-4.0；保留作者、版本、数据卡及[许可链接](https://creativecommons.org/licenses/by/4.0/)。
数据卡说明反馈来自评价员；其上游来源与限制按卡片逐项留存，不将公开可下载等同于本项目准入。
已下载5,275,463字节，本地SHA256与预期一致。前200条均为context/response1/response2/feedback1/feedback2/domain/language结构；原始正文留在受控目录，未提交Git。此为结构审查，不声称人工内容筛查或盲标已完成。

## 后续独立前置

本记录不批准留出批次或模型运行。实际探索30/留出60、中文/任务配额、近重复隔离、
本人首标及至少48小时后20例盲复标、封存与环境执行冻结分别完成。其他两源继续HOLD。

上游数据卡：ShareGPT52K当前数据卡声明CC0-1.0；WildChat-1M当前数据卡声明ODC-By。两者为发布者声明；HelpSteer3未固定其上游采集版本，保留这一来源链限制，不据此批准再分发/训练。此轮仅获准本机有限审查，保留来源和许可引用。

## 本机审查结果（2026-09-29）

前200条的400个目标投影全部关联成功；评价者反馈与另一回答未进入预测材料。固定tokenizer实测112个目标满足全部预算，288个超限，最大7371输入token；没有截断，也未对真实材料推理。全部200条标为探索暴露；6个数据卡示例纳入上下文排除，排除清单共345个精确/规范化hash。domain均为code，但不据此标成代码改写；language字段是编程语言，不能代替原生中文认定。

受控记录：`admission.json`、`schema-review.json`、`exposure.json`、`projection-budget-review.json`、`projection-budget-summary.json`、`exposed-groups.json`。正文、缓存和上述记录均在本页约定目录。到期时间2026-10-29T09:00:14.607557Z；保留/删除范围沿用已批准记录。内容筛查、探索30条选择和留出60条准入尚待完成。
