# M5-3a 逐源小批准备

2026-09-29，FR-04/05/14/15；TD-02/06/07/11/14；D-19。本轮核对固定版本的公开卡片、文件列表与LFS SHA-256；
未下载真实行、未运行真实评测。逐源[元数据清单](../qa/evidence/2026-09-29-m53/source-preparation.json)保留固定文件身份。

| 来源 | 候选文件 | 当前准备结果与缺口 |
| --- | --- | --- |
| [WildFB](https://huggingface.co/datasets/THU-KEG/WildFB/tree/0791dbc3101c6be7e0316cba1d8caf10c28917ad) | test.jsonl，86,593,611字节 | 卡片标MIT且要求另看WildChat上游条款；Viewer splits失败，不能从合成history假定文件schema。HOLD。 |
| [HelpSteer3](https://huggingface.co/datasets/nvidia/HelpSteer3/tree/f6d145777bcbde96137596340fab89793acd1031/feedback) | feedback/validation.jsonl.gz，5,275,463字节 | 卡片标CC-BY-4.0；首选较小文件核对context、response1/2与feedback1/2关联。反馈为评价员意见，不能冒充原用户点踩。HOLD。 |
| [WildFeedback](https://huggingface.co/datasets/microsoft/WildFeedback/tree/8b1a3e530b949d6aacfad6ba8912e209a05bc846) | sat_dsat_annotation.json，1,615,402,501字节 | 卡片标ODC-By；原话/目标关联需从annotation核验，不能用较小的生成偏好对冒充原反馈。先确认有界读取方案。HOLD。 |

卡片许可标注不是本项目的准入签署。上游许可链、内容筛查、用途、访问者、受控目录、保留/删除期和小批范围仍待记录。
文件哈希来自Hub元数据，尚无下载后本地文件摘要，不能写“实际文件校验通过”。Viewer不是固定revision文件证据。

## 可领取的准入记录

每源单独填入以下记录，某源批准不改变其他源状态：

- 来源、revision、config/split、固定文件及预期SHA-256：沿用清单，变更须记录原因。
- 用途：仅本地探索、schema/目标关联审阅；是否允许进入独立质量评测另签。
- 许可链与敏感内容筛查负责人/依据：[待填写]。
- 受控目录/允许访问者/保留期限/删除与备份处理：[待填写]。
- 小批行范围、数量、排除规则、探索/留出分割版本：[待填写]。
- 批准人、时间及引用：[待填写]；未批准继续HOLD。

批准后先核对字节hash与schema，再用现有适配器逐例审阅目标与反馈关联；记录拒绝/排除计数及上下文缺失，
不能只把现有synthetic-only runner的mode改名作为真实入口。公开材料标public_review，用户自选标user_selected，
开发虚构标synthetic；原用户、评价员、自动标签继续分开。真实入口、近重复分组与分割仍待实现/核验。

## 用户自选案例

“无法归类案例”指你实际点踩时，现有理由都不能准确表达问题的那次问答。可能是缺少类别、模型选错、
上下文不足或一条回答同时有多个问题；在看到案例之前不认定原因。
只需你自愿指定受控案例位置及允许审阅的范围；不自动读取私人聊天。可补一句“原本希望指出什么”，
它作为用户原意单独保存，不作为模型输入或独立gold。当前未收到选定案例，数量与归因保持缺失。
