# M5-2a 开发交付：展示与确认记录

2026-09-28；基线 main `c2ee7a3463f2b058d4f7560ec8e1a440b3c0564e`（PR #37 已合并，
合并 CI `36431508655` 成功），用户验收前一阶段。输入 PRD 334 / 技术文档 332。
映射 FR-10/11/13/14/15，TD-04/06/09/10/11，D-13/18，Q-09/10/21/22、S1-3R。

## 交付范围

- [契约](m5-suggestion-contract.md)先固定新事件、展示预约、幂等、更正、撤销、报告及回滚。
- `suggestions.py` 的受控本地入口复用 TrialStore、研究回答台账和 events；四类事件分别记录
  合成建议、客户端报告渲染、显式响应、推测撤销。只有 yes/correct 创建本契约的用户确认。
- 新 none_matched 拒绝整组，保留已有原因；旧 manual-v1 的清空行为不变。
  新旧投影分别报告，未把新细理由传入旧菜单/生产 API。没有默认确认或历史回填。
- 主体、回答、event/suggestion/display、候选顺序及模型/目录/模板/UI 版本全部绑定。
  display 在生成时预约，新预约立即替代旧的；60 秒期限按响应入口收到时间判断，
  等待数据库锁不误判迟到。事务内再次检查撤销、当前版本、权限和开关。
- `research_report` 同一只读快照新增 `suggestions`；按来源、版本、建议组分别统计生成、
  可展示、渲染报告、有效响应、确认及缺失。close 单列，不充当有效原因响应。
  旧人工填写率维持原口径；公开来源操作者仍为 evaluator；不生成 gold 或训练导出。

这次实现仅供 mock 合成验证。服务端新开关默认关闭；没有新增宿主 UI/HTTP 路由，
没有调用真实模型、读取正文或启用真实/cloud 研究记录。M5-2b 模板、引用和本人试用仍待开发。

## 可复现验证

```powershell
uv sync --extra dev --locked --no-editable --reinstall-package whynote
uv run --no-sync ruff check src tests integrations qa
uv run --no-sync ruff format --check src tests integrations qa
uv run --no-sync pytest tests qa/test_s0_regressions.py qa/test_manual_v1_acceptance.py -q
uv run --no-sync python qa/m52_suggestion_fixture.py --output var/m52-new-fixture
```

输出目录必须未存在，避免覆盖审计数据。具体结果见提交的
[评审修复后合成报告](../qa/evidence/2026-09-28-m5-suggestions/report-reviewed.json)、
[初版合成报告](../qa/evidence/2026-09-28-m5-suggestions/report.json)和
[验证回执](../qa/evidence/2026-09-28-m5-suggestions/verification.json)。
初版本地测试585项通过（新增54项），Ruff lint/format与diff检查通过。仅一条既有Starlette/httpx弃用警告。
开发测试包括旧人工原因保留、并发幂等、失效后拒绝、研究停用、无登记、原子回滚、
append-only、只读时点重放、无正文/密钥、59.999/60 秒边界与锁等待、迟到旧回执。
初版 300 秒期限与渲染到达排序在核对 Q-22/H-04 后调整为现契约；早期本地输出保留在 var，
不作为最终版本证据。

合成脚本包含 10 个回答/动作、9 组生成，其中 7 组有候选、6 组报告渲染、5 组有效响应、
1 组曾确认。该确认随后更正、撤销推测、撤销动作，历史分母不变，当前确认清空。
另外包括 unknown、no_match、未渲染及未生成各 1；close 到期派生未响应。
这些数字是固定虚构对账案例，不是产品效果或实际采样结果。

收尾自查补修：已记录展示换request_id重报返回冲突，防止返回成功却未绑定新键。
原request_id的合法重试仍返回原记录；换键不新增事件或统计。原585项全套结果对应4fe5c0d，
补修提交`26642c4`的受影响101项与Ruff通过；
[CI 36434922047](https://github.com/262412/-Whynote/actions/runs/36434922047)的quality/native-regression均成功。
原验证回执保留初版全套身份，并单列补修源码摘要与测试，不覆盖历史证据。

### 自动评审修复

三条P2意见先新增复现测试，在`32e9858`上7项全部失败；随后修复：

- 确认来源：response_id保留建立原因的yes/correct事件，last_response_id单列后续响应。
- 宿主清理：临时配置同时关闭两个研究开关，停用/错配时删除、编辑仍能追加失效。
- 报告终态：建议撤销、替代、动作撤销及回答失效不再计pending，按as_of重放历史。

修复后完整592项（累计新增61项）、Ruff通过。见[红/绿验证及源码摘要](../qa/evidence/2026-09-28-m5-suggestions/review-fixes.json)。
原7项失败输出保留在工作树var/m52a-review-red.txt；原585项证据保留，不能代替新用例。
本地仍未运行原生宿主/浏览器，适用宿主回归由本PR最新CI核验。

## 剩余验收与交付状态

开发执行：本轮 Codex；独立 QA 待指定非作者执行；产品/客户端/数据责任人待签署契约和结果。
[PR #38](https://github.com/262412/-Whynote/pull/38)，业务提交 `4fe5c0d`；
[CI 36434184529](https://github.com/262412/-Whynote/actions/runs/36434184529)的quality与native-regression通过。
飞书15处原需求/字段/任务条目及两处幂等补修条款逐项局部更新并精确读回，最终PRD342 / 技术文档341，历史链接保留；
见[同步回执](../qa/evidence/2026-09-28-m5-suggestions/feishu-readback.json)。
Copilot评审仅返回配额耗尽的COMMENTED，无有效APPROVED。未取得非作者有效批准与适用签署前不合并。
依据[开发规约 §3.2](development-governance.md)，绿色测试不能代替独立批准。
当前分支及工作树保留供评审；原工作区已有代码、运行库和模型文件均保留。

后续退出条件：独立复现合成对账、确认各响应语义与版本/时效规则、签署责任记录；
真实数据用途/保留/删除契约和 M5-1 效果门槛由对应责任人补齐后，才进入 M5-2b 的获准范围。
