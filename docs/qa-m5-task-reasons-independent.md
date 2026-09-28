# M5-0b 离线理由包独立 QA

2026-09-28；PR #36；FR-05/06/09/11/14、TD-02/07/09、D-16/18。

## 结论与基线

所测离线工程子项通过，未发现新业务缺陷；没有执行模型效果评测。
真实探索审阅、产品采用版本签署、独立留出集、非作者有效 APPROVED 与适用责任人结果签署仍缺，
因此保持待合并/阻塞，不标完整 FR 或 M5-0b 完整验收。

- 业务候选：`ab908bc1031502bed5dd40719295554132b20a40`，分支 `codex/m5-task-reasons`。
- main：`453fca7df7756e38a65b6f829d5204b1ec39b2bb`；PR #35 已合入。
- 输入飞书：PRD **316**、技术文档 **321**；核对原 M5-0b 任务、字段与 TD-02。
- Windows、CPython 3.11.14；隔离 QA 工作树，锁文件安装并强制重装非 editable wheel。
  已核实安装包 `task_reasons.py` 与本次源码字节相同。
- 候选的 [CI 36413441396](https://github.com/262412/-Whynote/actions/runs/36413441396)
  quality/native-regression 均通过；QA 追加提交的检查以 PR 最新 head 为准。
- 已阅读自动评审的重复参数意见；该意见指向旧实现，`27c4e51` 修复及对应三项测试已包含在复跑中。
  当前评审只有 COMMENTED，没有有效 APPROVED。

## 实际执行

| 操作 / 预期 | 实际结果与证据 |
| --- | --- |
| 在新增 QA 前重跑 `pytest -q tests qa` | **450/450**，保留开发原断言；包含旧菜单拒绝新 ID、事件零追加及旧 unknown 重放检查 |
| 新增独立边界测试 | **23/23**，见 `tests/test_task_reasons_independent.py`；不修改业务代码或开发测试 |
| 加入 QA 后全量 `pytest -q tests qa` | **473/473**；1 条既有 Starlette/httpx 弃用警告 |
| Ruff lint / format | 通过；72 个文件格式检查通过 |
| 16 种材料组合 × 5 种任务路由 | 80 组按独立预期集合核对路由、可选与缺失材料；缺 request/answer 不可建议，代码细项需 original_code，事实/过时需 reference；不可选 ID 均拒绝 |
| 11 项中任取 3 项，验证合法上界及多因顺序 | 165 种组合全部接受，保留调用者顺序和 caller_supplied/unconfirmed，不冒充模型预测或用户确认 |
| 保持 general.style 可选，分别改变证据、fallback、task 后重发旧选择 | 三条路径均 candidate_set_mismatch；原候选仍可重放，未将通用理由视为跨候选集通行证 |
| 从仓库外运行已安装 CLI，两次同输入构建 | 路由 11、可选 9、缺 reference 的 2 项不可选；两次 stdout 字节一致，工作目录零新增文件 |
| 选择文件恰为 8191 / 8192 / 8193 字节 | 前两者退出 0、返回 unconfirmed unknown；8193 退出 2、selection_too_large；输入文件字节不变，无额外文件 |
| 4000 字节深嵌套 JSON、非法 UTF-8 | 退出 2、仅 invalid_selection_file，无原输入回显，文件保持原样 |
| 禁止 socket 创建后执行候选构建/描述/校验 | 成功；本次调用无网络创建、无新增宿主/模型模块导入 |

CLI 实测候选身份：`e1a933bdc4941030942187e8640b7a45c523a94a5c739120b706d9a0ea130684`。
输入为 code_rewrite + request/answer/original_code；这只绑定材料种类，不证明正文真实或推断正确。
包摘要及三个版本在旧测试与本轮复跑中均校验通过。

## 契约对应与副作用范围

- FR-05/06、TD-02、D-18：四代码/七通用理由、显式任务与回退、版本/摘要和选择门槛的离线实现通过。
  无上下文正文读取、无任务关键词猜测；新细理由未投影为旧用户选择。
- FR-09/11、TD-07/09：unknown / no_match / 用户 none_matched 分离；合法选择仍是未确认的调用者输入。
  该包不产生模型调用、用户动作或确认事件；旧事件兼容测试实际使用临时虚构数据库。
- FR-14、D-16/18：开发的 7 个合成案例继续作为开发探索，未重新命名为独立标签或留出集。
  新增 QA 检查结构和拒绝行为，不能提供候选命中率、误导率或模型准确率。

本切片没有修改 Open WebUI 补丁、权限或删除逻辑；本轮未独立重跑原生宿主或浏览器。
原生绿色来自远端 CI，不能改写成 QA 当轮实测。离线 CLI 已实际执行，不涉及浏览器路径。
没有读取真实语料、启动模型/供应商服务、使用真实密钥或产生模型费用。
既有本人联调授权不因本轮 QA 扩大或撤销；生产上下文、auto-attach、自由文本 SLM、训练导出保持关闭。

## 可复验材料与退出条件

- 提交材料：独立测试、本报告、`qa/evidence/2026-09-28-m5-task-reasons-independent/summary.json`。
- 原始本地证据：QA 工作树 `var/qa-m5-0b-independent/` 中的 baseline-tests.log、independent.log、
  independent.xml、full.log、full.xml、cli-export.json、runtime-receipt.json、飞书前后快照及写入回执。
- 下一次复验：理由内容/版本或路由变化后重跑材料矩阵与候选身份；M5-1 先冻结样本、协议、阈值与留出集，
  再验证真实预测。M5-2 对引用、宿主白名单、持久化、展示/确认独立验收。
- 合并条件：非作者有效批准覆盖最新提交，以及适用责任人的结果签署；QA 不代签。
  原目录未提交改动、其他运行进程、历史失败与审计数据保留，继续原 PR #36。

飞书回填：按精确 revision-id 局部更新原 M5-0b / TD-02 条目并读回，PRD **317**、技术文档 **323**。
历史归档正文与原链接集合保持不变；前后快照与写入回执留在上述证据目录。
