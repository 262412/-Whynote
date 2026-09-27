# S1-0 数据来源审查与抽样清单

2026-09-27；FR-14/15、TD-02/06/11/14、D-16。范围：核对公开来源卡、固定仓库版本及文件清单，准备逐源审查和行级manifest。没有下载数据文件、抽取语料正文、开展模型调用或批准导入。

## 已核对的材料与当前决定

| 来源 / 固定版本 | 已核对 | 仍需核对 / 决定 |
| --- | --- | --- |
| [WildFB](https://huggingface.co/datasets/THU-KEG/WildFB/blob/0791dbc3101c6be7e0316cba1d8caf10c28917ad/README.md) `0791dbc3101c6be7e0316cba1d8caf10c28917ad` | 文件为train/test JSONL；卡标MIT，来源WildChat-4.8M；label来自自动流程，不能当用户确认原因。卡的schema称history为string，但示例是数组，存在类型不一致 | **HOLD**：确认ODC-BY上游归属/条款及转换责任，检查实际JSONL类型、反馈对应回答；中文可用数未知 |
| [HelpSteer3 feedback](https://huggingface.co/datasets/nvidia/HelpSteer3/blob/f6d145777bcbde96137596340fab89793acd1031/README.md) `f6d145777bcbde96137596340fab89793acd1031` | feedback/train.jsonl.gz与validation.jsonl.gz确实存在；默认子集为preference，须显式feedback；卡标CC-BY-4.0，人工质量反馈 | **HOLD**：限定研究用途、署名/修改说明和受控存储签署后核实际类型、中文子集及敏感内容；不当原用户动机 |
| [WildFeedback](https://huggingface.co/datasets/microsoft/WildFeedback/blob/8b1a3e530b949d6aacfad6ba8912e209a05bc846/README.md) `8b1a3e530b949d6aacfad6ba8912e209a05bc846` | wildfeedback.json、sat_dsat_annotation.json、user_preference.json；卡标ODC-BY；卡说明部分优选回答为GPT-4生成 | **HOLD / 英文备选**：原反馈、自动注释、生成回答必须分开；审查WildChat派生重叠及上游许可 |

以上是数据卡及仓库元数据事实，不是法律准入结论或实际文件schema验证。[WildChat上游](https://huggingface.co/datasets/allenai/WildChat-4.8M)卡标ODC-BY，其字段涉及来源/地域等元数据；不能把公开可获取视作无敏感内容。优先级保持WildFB中文、HelpSteer3 feedback，英文备选不阻塞个人聊天契约。

## 字段映射候选（实际类型待行级审查）

| 来源 | 上下文与目标 | 反馈 / 来源 | 排除或隔离 |
| --- | --- | --- | --- |
| WildFB | history/messages/text须核对哪个表示目标前历史，以及目标回答的索引 | user_feedback为后续话语候选；label作为自动满意度字段单列 | 关联不唯一、缺失目标、反馈夹在推断输入、类型不一致未解释 |
| HelpSteer3 feedback | context与response1/2分别绑定，不交叉配对 | feedback1/2与对应回答绑定；评价者evaluator；同回答多评价不当多用户 | 缺失/混配、误读preference、未来edit答案泄漏 |
| WildFeedback | 原会话及目标回答与派生偏好需分文件核对 | 原话、SAT/DSAT自动标注、生成偏好回答分别标来源 | 无法还原原始目标或来源的行不进入回放/评估 |

## 准入和抽样步骤

1. 用户按数据/隐私责任角色确认**每个来源**的用途（仅探索/回放，暂不训练）、归属/修改说明、受控存储位置/访问者和保留/删除期；未决源保持HOLD。不是批准三个源批量导入。
2. 获准后读取固定revision的指定文件，记录文件hash与schema实际类型；对格式异常计数并隔离，不用宽松转换掩盖差异。中文数量、可用数、PII/敏感风险均须实际扫描后报告，当前为`null`。
3. **建议首批上限每源20个候选目标回答，待批准**：先按会话分组，再在中文显式纠错/不满、其他反馈及语言/类型异常中分层审阅。它是流程检查，不估总体比例；无法完成覆盖时报告缺口，不凑样本。
4. 筛查个人信息、敏感内容、归属不清、空/截断、非法结构、重复及派生源重叠。原文只留受控目录；代码库保留空模板与非敏感规则，行级链接/内容hash也随受控manifest保存。
5. 按会话/近重复组划分split，跨WildChat派生源合并重叠组；固定group/split版本。无稳定会话键时记录降级和排除，不假称去重完成。
6. 推断可见部分只能到目标回答为止；后续反馈、label、修订回答、偏好答案与未来轮次在标注侧隔离。公开反馈标签不能生成Whynote `user_selected`/`confirmed`或gold。
7. 审阅报告列：读取数、有效目标数、中文数、各排除原因数、去重后数、保留数以及未知项；不得从数据卡总量推导实际可用数。公开回放没有操作时间，填写耗时记不适用。

## 受控manifest字段

模板见 `fixtures/s1-source-manifest.example.json`，没有真实语料行。来源URL、repo revision、文件路径/hash、许可链及证据引用、用途、审批引用、row_id、会话/目标索引、正文受控引用/hash、原始反馈类别、评价者身份类别、语言、筛查/分割版本、排除原因、保留/删除期限均须可追溯。缺失值用null，不造默认许可、数量或批准。

已核对README SHA-256：

- WildFB：`3713a447f68b029d7e3696c000c29bcea79fc042c05154abdfef05e9ec920b11`
- HelpSteer3：`d5d46db58e8374937ea284b33b1583a4a465b232bf74b941745d80e3c35fd8ae`
- WildFeedback：`703961ead61ca303bc60626372bf5d8dc2b4a20519a7a11f3411fa99ed7d635e`

数据卡及元数据读取结果保留在本地忽略目录 `var/s1-*-card.md`、`var/s1-*-metadata.json`；不提交卡片内的原文示例。此轮完成来源审查准备，**未完成逐源准入或实际抽样**。
