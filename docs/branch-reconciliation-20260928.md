# 2026-09-28 积压分支核对与处置

## 结论与基线

用户授权检查、合并必要且满足门槛的内容，并删除可安全清理的分支。
远端 main 为 `84cd940ae797196952da2ffddd6f133e6c73d507`，已包含 PR #27。
PR #28 合并提交 `253392ec1e920685afb4e32d631dc1d1c930ccc1` 的目标是
`codex/qa-s1-dynamic`，Jev 接口和 Q-26 修复确实尚未进入 main。
飞书读取修订为 PRD 267、技术文档 266；Q-26 指定子项已有独立通过证据。

## 逐分支判断

| 分支 | 核对结果 | 处置 |
| --- | --- | --- |
| codex/jev-native-interface | 远端 `32fffc2` 的全部提交在 `253392e` 中；接口、Q-26 修复和独立证据需要进入 main | 保留；随集成 PR 交付后再清理 |
| codex/qa-s1-dynamic | `253392e` 相对 main 仅新增 Jev 相关 8 个文件的差异；与接口分支的两份停用 QA 文件差异已在 main | 复用分支，补交正确目标 main 的集成 PR |
| codex/qa-manual-v1 | 本地 `f66bfb6` 与远端 `adae0e4` 分叉；相关内容已被 main 保留或后续实现覆盖 | 保存双方 Git 历史后，已删除远端和本地引用 |
| codex/s0-menu-readiness | 本地 `678fcae`、远端 `f55a8e4`；#20 之后的内容已被 main 保留或后续实现覆盖 | 保存 Git 历史后，已删除远端和本地引用 |

旧菜单分支逐项核对：

- `measurement.py`、`qa/manual_http_probe.py` 和原 manual 独立证据 JSON 与 main 的 blob 完全相同。
- 旧分支及本地分叉里的每个 manual 验收测试函数，与 main 对应函数 AST 完全相同；main 另有 Q-21/Q-22 扩展用例。
- 本地 Q-17～Q-20 证据 JSON 的原字段和值均在 main 保留，main 增加交付更正信息。报告保留历史失败并更正旧 PR 复用计划。
- `s0_action.py` 的旧随机点击键和通用通知已由 Q-21 幂等生命周期、Q-22 时间修复及更明确的操作提示覆盖。重新带回旧代码会损害当前行为，不应再合并该版本。
- CI 的 manual 验收命令仍在 main；后续仅调整步骤名称并增加 S1 原生补丁检查。

## 本轮验证与恢复材料

在复用的干净 QA 工作树快进至 `253392e` 后，以锁文件安装：
`uv sync --extra dev --locked --no-editable`；Ruff check、format 检查通过；
`pytest tests qa -q` **258/258**。没有改业务代码或验收断言。
本轮没有重做浏览器、真实供应商调用或独立 QA；既有证据的范围保持原样。

已验证完整 Git bundle，位置为 QA 工作树的
`var/branch-reconciliation-20260928/retired-manual-branches.bundle`；
`refs-before.json` 保存四个本地/远端引用及 SHA。
远端删除使用精确旧 SHA 的 lease 和原子 push，本地删除使用预期旧 SHA 校验。
原目录未提交改动、所有工作树和忽略的数据库/审计数据均保留。

## 合并门槛与后续

PR #27/#28 仅有 Copilot 额度不足的 COMMENTED，没有有效非作者 APPROVED。
本轮集成 PR 需当前 head 的 CI 与非作者批准；责任人还须签署相关结果。
依据开发规约 3.2，不能把用户的条件式合并指令或开发侧自审当成非作者批准。
因此 Jev 集成为待合并，生产仍 NO-GO。

批准后由交付执行人合并集成 PR，核对远端 main 和合并后 CI；重新读取两条保留分支的
SHA 与依赖，释放本地工作树分支占用，再删除远端和本地引用。工作树和审计数据继续保留。
