# M5-0a 合成映射独立 QA（2026-09-28）

业务提交：PR #35 `edda92f3f18d6c13b9b1c2a1435990f58068282c`；目标 main，main `52ac4db21e9f83ae50f17f7a1a4c2c4984540997`。读取飞书 PRD **311**／技术文档 **313**，映射 FR-04/05/14/15、TD-02/06/07/11/14、D-16/18。开工 CI `36408684440` quality/native-regression 成功；无非作者有效批准，Copilot COMMENTED 仅报告额度不足，不算评审通过。

**已执行的合成 CLI 子项独立通过，未发现新业务缺陷。** 真实数据、实际案例原因审阅、WildFeedback 固定原文件兼容、完整 M5-0a 和后续 M5-0b/1/2 未由本次测试完成。

## 执行结果

- 在空闲 QA 工作树固定该提交；保留原目录改动和三个既有运行进程，没有操作它们。锁文件安装 dev/jev extras 并强制重装当前 whynote wheel。
- `pytest -q tests qa`：**393/393**（原 tests 353＋QA 40）；Ruff check/format 通过。1 条既有 Starlette/httpx 弃用警告。
- 新增 `tests/test_source_mapping_independent.py`：**11/11**。使用临时合成文件、实际文件系统和已安装包 CLI；与 393 项分别运行，不假称重跑合并后的全套。
- 已安装 CLI 对提交内合成 fixture 输出 **1/2/1** 个目标、**7** 个 JSON 文件，退出 **0**；两次新目录输出逐字节相同，三个来源均 mapped。多因 missing_category/multiple_issues 分别计 **1/2/1**，合成审阅不代表真实原因正确。

## 操作、预期与实际

| 子项 | 操作/预期 | 独立实际 |
| --- | --- | --- |
| 引用可重放 | 对三源每条 context/target/feedback/label 引用，按 row_id 和 JSON pointer 解析固定文件，重算值哈希 | 全部相同；data_sha256 对应原文件；UTF-8 长度包含完整上下文与目标 |
| 预测与反馈分离 | 核对 prediction_refs 仅为 context＋target，反馈/标签位置不得混入 | 三源全部通过；HelpSteer3 两目标分开；既有未来反馈/重写及正文泄漏检查通过 |
| 门控先于读取 | 对 public/HOLD/excluded 三种拒绝状态将 Path.open 设为失败哨兵 | 三种均 HOLD、rows_read=null、无记录/排除内容，未触发任何文件打开 |
| 审阅变更与身份 | 首次运行后清空 HelpSteer3 审阅表并更新文件哈希，输出到新目录 | record_id 保持；diagnoses=null、ready_for_replay=false；旧输出不变 |
| 重跑保护 | 向已存在的输出目录重跑 CLI | 退出 **1**，旧目录所有文件逐字节不变 |
| 坏源隔离 | 修改 WildFB 文件但不更新固定 hash，再运行完整三源 CLI | 退出 **2**；WildFB 报 file_hash_mismatch，其他两源仍为 **2/1** 个目标；7 个报告文件完整，stdout/stderr 不含损坏正文 |
| 行数上限 | 同一合成源分别输入 1000/1001 行 | 1000 行映射 2000 个 HelpSteer3 目标；1001 行 HOLD/too_many_rows 且零目标 |
| gzip 物理行 | 空行＋合法行＋坏 JSON 行压缩输入 | rows_read=3，合法两目标 row_id=1，排除 row_id=0/2；输入字节及 manifest 对象不变 |
| 多因/缺失/分割 | 独立复跑既有审阅枚举、空分母、未知字段、跨来源/同上下文/人工组冲突测试 | 通过；冲突取消回放资格，缺失单列；近重复审阅不因此完成 |

本轮 [汇总](../qa/evidence/2026-09-28-m5-source-independent/summary.json)包含测试基线、计数与报告哈希；原始日志/JUnit/两套 CLI 输出在 `var/qa-m5-0a-independent/`。源文件仅为仓库内合成 fixture 或临时生成值，无真实语料、私人案例、模型请求或真实密钥。

## 验收范围与剩余项

- 本切片仅增加独立离线模块，没有修改现有 API、数据库、事件或宿主补丁。本轮没有重新运行原生宿主、浏览器或前端构建；远端 native-regression 的绿色结果明确为 CI 证据。
- 原始 HelpSteer3/WildFB/WildFeedback 文件没有下载或打开。尤其 WildFeedback 的审阅指定会话 pointer 只在合成结构上验证，不能推出固定版本真实 schema 已兼容。
- `ready_for_replay` 仅表示合成记录满足当前字段检查；UUID 审阅引用没有验证外部签署，合成输入不能作为真实运行或数据准入授权。
- 按 D-18 保留真实案例审阅、逐源准入/敏感筛查/固定 schema 与目标关联、小批映射及独立原因判断。M5-0b/1/2 未执行。当前真实云/本机联调的既有授权不被本报告扩大或撤销；生产、auto-attach、自由文本 SLM、训练导出保持关闭。
- 继续原 PR #35，仍缺非作者有效 APPROVED 和适用责任人签署。未合并；分支、工作树与历史数据保留。QA 不代签完整 FR 或数据准入。
