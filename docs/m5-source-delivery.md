# M5-0a 合成映射开发交付

2026-09-28；FR-04/05/14/15、TD-02/06/07/11/14、D-16/18。
需求输入为已读取的 PRD 310 / 技术文档 312，开发基线为 main
`52ac4db21e9f83ae50f17f7a1a4c2c4984540997`。状态：**部分实现，待非作者评审/合并；
M5-0a 尚未完整验收，M5-0b/1/2 尚未实施。**

交付 PR：[#35](https://github.com/262412/-Whynote/pull/35)，实现提交 `d5ef220`。
飞书已在原 M5-0a 任务单元格局部回填并读回：PRD 310 → 311，技术文档 312 → 313；
保留原退出条件与其他任务/历史内容，没有新增完整验收勾。

## 本切片改变

- 新增 `source_mapping.py`：统一引用记录、HelpSteer3 成对反馈绑定、WildFB 和 WildFeedback
  的显式会话目标绑定。缺失/非法关联输出排除码，输出不含正文。
- 新增 `source_review.py`：本地文件校验、逐源 HOLD/映射/排除、行与目标计数、多因诊断矩阵、
  缺失率及任务/双语言/长度覆盖。JSONL 坏行不从读取分母中消失，单源失败不阻塞其他源。
- 提供[合成 CLI 示例](../fixtures/m5-synthetic/README.md)：三源共四个目标。
  源名与固定 revision 用于验证拟议映射，样本和审阅 UUID 均为虚构，不代表来源已准入。
- 显式区分 prediction 与 feedback 引用、评价员与原用户/自动标签。检查成对回答、精确相同
  上下文/目标、人工会话组跨 exploration/holdout 的冲突；不声称完成近重复审阅。
- 输出目录不可覆盖；重跑记录 ID 稳定，改变目标定位得到不同 ID。未知任务/语言/审阅保留缺失，
  不根据代码语言猜自然语言，不产生准确率、填写耗时或用户确认。

契约及兼容规则见 [m5-source-contract.md](m5-source-contract.md)。不改变现有 API、
数据库、事件、旧八类、manual-v1、模型门控或运行实例。工具停用即停止新增离线输出；
旧报告和用户数据保留。

## 已执行验证

环境：Windows、CPython 3.11.14，锁文件 `uv sync --extra dev --locked --no-editable`。
代码变更后重新安装 whynote，避免旧 wheel 造成错误测试证据。

| 检查 | 本轮结果 |
| --- | --- |
| Ruff check `src tests integrations qa` | 通过 |
| Ruff format --check 同范围 | 68 文件通过 |
| `pytest tests/test_source_mapping.py -q` | 37 passed |
| `pytest -q` | 353 passed，37 个新增用例已包含在内 |
| S0/人工菜单 QA：`pytest qa/test_s0_regressions.py qa/test_manual_v1_acceptance.py -q` | 40 passed |
| 已安装包 CLI：示例 manifest → 全新 `var/m5-synthetic-final` | 返回 0；三源各 1 行，目标数 1/2/1；7 个 JSON 输出 |
| `git diff --check` | 通过 |

pytest 有一条既有 Starlette/httpx 弃用警告，无本轮失败。原生 Open WebUI 集成留给 PR CI
在固定上游环境运行；本地核心/QA 通过不能替代它或独立验收。远端 CI、批准和合并状态以
本切片 PR 最新 head 为准，不在此预写为通过。

## 来源核对与限制

三源固定 revision 的 README 已重新读取：
[WildFB](https://huggingface.co/datasets/THU-KEG/WildFB/blob/0791dbc3101c6be7e0316cba1d8caf10c28917ad/README.md)、
[HelpSteer3](https://huggingface.co/datasets/nvidia/HelpSteer3/blob/f6d145777bcbde96137596340fab89793acd1031/README.md)、
[WildFeedback](https://huggingface.co/datasets/microsoft/WildFeedback/blob/8b1a3e530b949d6aacfad6ba8912e209a05bc846/README.md)。

- HelpSteer3 示例确认 feedback1/2 是对应回答的字符串数组。实际数据文件类型尚未抽样。
- WildFB 卡片对 history 类型的描述与示例不一致，工具不猜类型、不回退 history/text。
- 2026-09-28 的 **当前 Viewer 元数据（未固定 revision）** 对 WildFeedback
  `sat_dsat_annotation` 列出 TurnId、UtterranceId、Role、Content、Preceeding、
  Satisfaction 等扁平字段；不能据此推断固定原文件具有会话数组。本切片验证的是显式绑定协议，
  **未证明真实 WildFeedback 原文件兼容**；原会话/目标关联仍需固定文件审查。
  WildFB 当前 Viewer info 返回 Not found，不等于数据不存在。

未读取实际语料文件、未采集私人聊天、未启动 Laya/DeepSeek、未开展模型或用户实验。
CLI 暂仅 synthetic；来源准入和受控真实文件入口尚待单独交付，不因数据公开而补造签署。

## 剩余工作与责任

| 剩余事项 | 责任/解除条件 |
| --- | --- |
| 自愿选定的无法归类案例、问题归因及分歧记录 | 用户提供选定的问题/回答与不满意点，产品/数据审阅；不自动抓取全部聊天 |
| 各源准入、固定文件 schema、真实目标关联、敏感筛查及小批映射 | 产品/数据明确用途、许可链、受控位置/访问者、保留删除及小批范围；后端再开放真实入口并验证 |
| 任务/语言/长度覆盖、近重复与派生源分割 | 数据/算法在真实样本上核对；合成统计不能代替 |
| 非作者代码批准、独立 QA、适用契约签署 | 按开发规约第 3.2 节；本切片作者不能自批 |
| M5-0b、M5-1、M5-2 | 按当前计划依赖推进；不把模拟案例当作理由库或质量阈值的产品依据 |

工作树与分支保留供评审修复；未合并时不清理。原 `D:/PythonProject/jev项目` 的代码、
运行数据和未提交文件均未覆盖。
