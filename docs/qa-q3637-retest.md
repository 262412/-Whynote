# Q-36／Q-37 独立复验

日期：2026-09-29（Asia/Shanghai）。结论：**两项指定技术范围通过；PR #40 待合并／阻塞，生产 NO-GO。**

## 基线与证据范围

- 候选：`56ec110de3804b13f575c97df00e85782da2b65f`，PR #40 / `codex/m5-live-laya-synthetic`；main 为 `5abdc580c7c510b247fa2e1db1d0fcb40f96416e`。
- 输入文档：PRD 496／技术 482。对应 FR-06/09/11/13/14、TD-02/04/07/10、D-19 与 M5-3b；本轮没有扩大真实数据准入或产品决定。
- 使用空闲 QA 工作树的 detached checkout，锁文件安装并重装非 editable `whynote`。实际 worker 安装源码与候选源码按 UTF-8/LF 规范化后相同，SHA-256 为 `1d77af4228bb48d7bd34677c1aa18ffc9cb6b3ec94e073626c55f2823ab505b0`。
- 原八条独立用例文件在 QA `b721374` 与候选中的 Git blob 均为 `1ba95e2aebb274290babb3d821949db9cf338282`，未修改断言。其原始 5 通过／3 失败见[历史报告](qa-q3435-retest.md)及保留证据。
- 候选远端 [CI 36531346743](https://github.com/262412/-Whynote/actions/runs/36531346743) 的 quality、native-regression 均成功；无非作者有效 APPROVED。本轮提交仅更新 QA 文档和证据，不修改业务、测试或依赖。

## 执行结果

| 执行 | 实际结果 | 范围 |
|---|---|---|
| `uv run --no-sync pytest tests qa/test_s0_regressions.py qa/test_manual_v1_acceptance.py -q` | 841/841 | 含原八例和开发新增 17 例；保留历次边界回归 |
| 固定上游实际 ORM／FastAPI 原生回归 | 80/80 | 全新虚构 SQLite；未替换查询、权限或删除分支 |
| `qa/native_regressions.py` | 58/58 | 管理员本人／他人、关联权限、删除及数据副作用；他人读取契约为 404 |
| `uv run --no-sync ruff check .`、`ruff format --check .` | 通过；184 文件已格式化 | 全仓含保留 QA 文件 |
| 实际本机 Laya worker | 2/2 | 普通目录及目录／环境干扰；串行合成调用 |

完整回归有一条 Starlette/httpx 弃用提示，原生有上游弃用提示；无失败。全套计数已包含原八例和新增 17 例，不重复相加。

### √ Q-36：错误码类型分类

操作：原样执行 `test_worker_boundary_independent.py` 的七种错误信封；实际启动轻量子进程输出 `[]`、`{}`、`null`、数字、布尔、未知字符串或合法 `timeout`。只替换命令，保留真实管道、解码、Action 和 SQLite。

预期：非法类型统一 `invalid_response`；合法固定错误码不改变。反馈动作及常规菜单保留，不生成建议事件。

实际：七例通过，原数组／对象两条失败转绿。事件投影 `action_status=active`，客户端收到 `input`，没有 `m52_suggestion_generated`；返回固定失败码。开发新增用例另覆盖其余全部合法码与嵌套数组／对象，并查询 SQLite 确认每个事件的 Outbox 恰为 1。以上均在本轮 841 项中独立执行通过。

证据限制：错误信封由轻量子进程按测试输入生成，用于验证实际进程返回后的分类与 Action 副作用，不代表真实模型产生过这些错误。

### √ Q-37：可信 worker 启动

操作与实际结果：

1. 原样运行旧失败用例：当前目录放置能够读取合成请求／回答并写标记的同名 `whynote` 包。修复后标记不存在，原失败转绿。
2. 新增边界用例实际启动可信 worker：普通、CWD 同名包、`sitecustomize.py`、`json.py` 及 `PYTHONPATH/PYTHONHOME/PYTHONUSERBASE` 干扰均返回预设缺失模型路径对应的准确 `model_load_failed`；标记不存在，排除“worker 根本没有启动”的伪通过。
3. 独立实机探针使用固定 Laya 权重及真实解释器，普通目录和组合干扰目录各调用一次。两次均返回 `m5-live-laya-v1`、固定模型 revision、`code_rewrite`／`code.interface_changed`，且 presentation 校验成功、1 张卡片、无回退、无诱饵标记。耗时分别 7.468 秒、7.500 秒。
4. 超时／取消用例实际启动轻量休眠子进程，检查 `kill/wait` 后 `returncode` 非空。超时测试把等待缩为 0.1 秒；没有据此宣称执行过完整 60 秒计时实验。实机调用后核查无本轮 worker 残留，父进程 CWD／环境恢复。

实际模型：`convaiinnovations/laya/multilingual@55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851`。解释器使用既有 `var/laya-runtime`，模型使用既有固定本地权重；未修改解释器安装或模型权重，未使用真实云密钥或真实用户上下文。

此处通过证明所测模块选择、合法启动与返回契约；没有独立 gold，也没有模型效果或用户收益结论。解释器、系统 site 与显式包根仍须可信，`-I` 不构成环境级出站隔离。

## Open WebUI 原生与旧缺陷回归

使用固定 Open WebUI v0.11.4 / `8bd8b4fac5e059578ac0c74b3c18d11139f88b7d` 的隔离检出，已有四补丁和显式 dialog。实际执行原生路由与 ORM，并为本轮探针新建 SQLite；管理员跨用户读取按最新 404 契约检查。80 项和 58 项通过，逐条实际／预期见 `native-probe.json`。

测试 wrapper 对 dialog 暂时使用 Git blob 的 LF 文本，结束恢复原字节，避免 CRLF 与既有固定源码证明不一致。没有修改构建回执或前端产物。Q-11～Q-35 保留用例包含在对应套件内；本轮没有重新执行各轮全部浏览器／部署实验。

## 文档、可复现材料与保护

- 飞书在原状态、FR／TD、M5／M5-3b 条目局部写入：每次使用最新 revision-id，目标变化即停止；逐次全文读回，并检查历史归档后缀及旧资源链接保留。
- 读回结果：**PRD 504／技术 489**，15 条写入回执见 [feishu-writes.json](../qa/evidence/2026-09-29-q3637-independent/feishu-writes.json)。完整 FR、真实试用及生产未打通过勾。
- 提交证据：`qa/evidence/2026-09-29-q3637-independent/` 的汇总、worker 实机结果、原生逐项结果和文档回执。
- 本地保留：`var/qa-q3637-retest/` 中完整日志、JUnit XML、实机 probe 脚本、写入脚本、全文快照与新的虚构数据库。认证材料不进入提交。
- 原工作区既有修改／未跟踪文件、模型权重、旧审计库和失败证据保留。本轮没有启动 HTTP 宿主或浏览器，没有重新构建前端；所有测试子进程已结束。

## 剩余阻断与下一次复验

| 阻断 | 责任角色与下一步 |
|---|---|
| 环境级出站隔离 | 运行环境维护者提供覆盖实际进程与出站通道的控制和受控拒绝证据；此前机制局限保留，QA 按最终契约独立复验 |
| 跨进程并发控制 | 模型进程维护者补跨请求／跨进程的可验证限制及取消、超时、释放测试；当前串行两次成功不关闭此项 |
| 评审与签署 | 取得覆盖最新 head 的非作者有效 APPROVED；模型进程、来源／兼容／回滚及结果按适用责任人签署；QA 结果不代签 |
| 真实准入和产品验证 | M5-3a 仍 HOLD；M5-4a/b、限定本人试用、真实采集未开启，不从合成工程通过推出质量或生产批准 |

Q-36／Q-37 可退出缺陷修复待办，其适用签署和整体切片验收仍未完成。本轮未发现新的失败；未合并，保留 PR、分支、工作树与审计材料供评审。
