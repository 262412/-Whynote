# M5-5 三源本机探索使用记录

2026-09-29；D-21、FR-05/06/14/15、TD-07/11/14。用途为本机无标签自动诊断，不是训练、再分发或正式质量验收。

固定来源、版本、文件大小和SHA256以本页范围和受控来源清单为准；下载前重新核对远端元数据，变更版本须另行核验。

| 来源 | 固定文件 | 使用边界 |
| --- | --- | --- |
| HelpSteer3 | f6d145777bcbde96137596340fab89793acd1031 / feedback/validation.jsonl.gz | 复用已下载文件；D-21增加本机探索推理及100→1000目标工程验证，原下载到期日不延长 |
| WildFB | 0791dbc3101c6be7e0316cba1d8caf10c28917ad / test.jsonl | 首次完整固定文件下载；100源记录冒烟，随后1000目标或实际可用量 |
| WildFeedback | 8b1a3e530b949d6aacfad6ba8912e209a05bc846 / sat_dsat_annotation.json | 首次完整固定文件下载，有界解析；100源记录冒烟，随后1000目标或实际可用量 |

两份新文件及所有下载缓存、临时文件、投影、案例和结果位于 `D:/PythonProject/jev项目/var/research/m55/`。仅本人和本人账户下的本机Codex访问；不新增共享、云同步或另行备份。自各自实际下载起保留30天；到期删除源文件、缓存和含正文衍生文件，仅保留无正文审计。本人已回复“采用上述安排”；本轮记录时间2026-09-29T13:24:54.802168+00:00。新文件可按本页范围下载；时间为当前记录时间，不倒填历史批准。

HelpSteer3按发布卡CC-BY-4.0保留署名；WildFB发布卡MIT及上游WildChat限制、WildFeedback发布卡ODC-By及上游来源一并记录。无法确定关联或明确敏感字段的记录机器排除并计数；原反馈、自动标签、另一回答及未来轮次不进入模型。所有已推理/查看的组记探索暴露，正式留出后置。

worker禁止网络，只接当前允许投影；正文不进入Git、普通日志、业务事件或云模型。未知失败立即停止。模型输出始终为model_inferred_unconfirmed，正确率等无标签指标为NA。

## 许可链与原审查范围

固定数据卡：[HelpSteer3](https://huggingface.co/datasets/nvidia/HelpSteer3/blob/f6d145777bcbde96137596340fab89793acd1031/README.md)（NVIDIA，CC-BY-4.0，保留署名/修改说明）、[WildFB](https://huggingface.co/datasets/THU-KEG/WildFB/blob/0791dbc3101c6be7e0316cba1d8caf10c28917ad/README.md)（MIT；WildChat-4.8M上游ODC-BY限制另列）、[WildFeedback](https://huggingface.co/datasets/microsoft/WildFeedback/blob/8b1a3e530b949d6aacfad6ba8912e209a05bc846/README.md)（ODC-By；原话、自动SAT/DSAT与生成偏好分层）。公开可获取和数据卡声明不等于本项目准入，也不批准训练或再分发。

HelpSteer3原有限审查仅前200源记录，批准记录为2026-09-29T09:00:14.607557+00:00；文件5,275,463字节，SHA256 `b86f71581a83f610ec5416029422f84ff1fc005e77b56c7634486ef9f6f12ceb`。原目录 `var/research/helpsteer3-review/`，到期2026-10-29T09:00:14.607557Z；D-21扩大本机探索用途不延长到期。HelpSteer3上游ShareGPT52K声明CC0-1.0、WildChat-1M声明ODC-By，采集版本未固定这一限制保持。

评价员反馈、原用户后续话语、自动标签、生成回答、模型推测、本人盲审及独立gold分别标来源；不能互转。预测仅到目标回答为止，不读反馈/未来轮次；派生源会话/近重复重叠须分组，全部推理/查看组属于探索暴露。正式留出、中文配额、敏感内容和独立标签准入仍需实际证据。

原许可和确认详情保存在[来源审查](https://github.com/262412/-Whynote/blob/9246dd5f143a02148a9328f79f5e188049f01563/docs/s1-source-review.md)、[HelpSteer3有限准入](https://github.com/262412/-Whynote/blob/9246dd5f143a02148a9328f79f5e188049f01563/docs/helpsteer3-review-admission.md)；采用本页范围后不把旧HOLD快照当作撤销现有授权。没有修改到期规则、建立删除任务或清理数据。
