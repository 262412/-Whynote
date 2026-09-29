# M5-3b 独立 QA：实时本机 Laya 合成链路

2026-09-29；PR #40；实际业务基线 `e1c4410c77ec4e4adcde89fc7e84347806d5d612`，
远端 main `5abdc580c7c510b247fa2e1db1d0fcb40f96416e`。输入飞书 PRD 416、技术 412。
映射 FR-01/06/09/10/11/13/14，TD-02/04/07/09/10，D-13/18/19，Q-27 回归。

**结论：所测实时模型与确认子项通过；Q-28、Q-29 两个宿主验证工具缺陷开放，切片验收未完成。**
本轮新增测试、报告和证据，不修改业务实现。非作者 APPROVED、来源/兼容/回滚与结果签署仍缺，未合并。
M5-3a 真实行未下载，准入 HOLD；M5-4a/4b 未执行，生产 NO-GO。

## 实测基线与结果

| 范围 | 结果与证据边界 |
|---|---|
| 原样核心和保留 QA | 727/727；锁文件安装后重新安装当前非 editable 包 |
| 新增独立验收 | 13 通过、2 失败；失败对应 Q-28 两个必需参数；无 skip/xfail |
| 加入验收后的完整回归 | 740 通过、2 失败，86.87 秒；不是全绿 |
| Ruff | lint 与 format 通过，91 文件 |
| 实际 Open WebUI ORM/路由 | 80/80；固定 `8bd8b4fac5e059578ac0c74b3c18d11139f88b7d`，四层补丁逐层检查/应用，新虚构 SQLite |
| 原生契约探针 | 58/58；管理员本人/他人、评分关联与删除路径实际执行，历史 Q-12～Q-16 未重现 |
| 真实进程边界 | 缺原代码返回 unknown/空理由；超过 700 token 返回 input_tokens_exceeded；超过 8192 字节在启动前拒绝，3/3 |
| 真实浏览器 | 新登录实例＋实际固定本机 Laya＋新 SQLite；确认、撤销、再次点踩、都不是→常规菜单、模型不可用回退通过 |
| 远端基线 CI | `36513647082` 的 quality/native-regression 成功；未覆盖本轮新增 Q-28 失败 |

测试计数存在范围重叠，不相加作为样本量。首次新增用例中的事件名简写、Path 类型和导入顺序错误已修正；
原日志留在隔离 var，未把这些测试作者错误登记为业务缺陷。Q-28 最终失败发生在有副作用的 provision 调用边界。

## 通过子项与具体证据

- **来源拒绝：** 将虚构 self_natural/public_replay 对象也加入目标白名单，仍在模型调用前拒绝；
  各保留 1 条动作事件，0 建议/渲染/确认，模型调用次数 0。未读取真实用户数据。
- **迟到状态变化：** 渲染或响应回调返回时，分别改白名单、协议、运行解释器或撤销动作；
  8 项均返回 superseded，0 非法响应，已生成历史保留。渲染后发生变化时仅保留已有效渲染。
- **回滚与重试：** 已确认 live 点击切回 fixture 后，同键重试不再调用模型、不追加；
  撤销后新点击使用 fixture。旧事件仍 model_inferred_unconfirmed，新 fixture 为 synthetic_model；
  固定 as_of 报告重复一致，读取前后数据库 SHA256 不变。
- **进程取消：** 使用真实 OS 子进程、管道、kill/wait；仅将 worker 命令替换为睡眠程序。
  测试取消和加速超时均回收子进程；同时断言实现传入的生产期限仍为 60 秒。
  此项不冒充真实模型跑满 60 秒的容量测试。
- **实际模型材料边界：** 原模型/SDK 与权重校验保留。缺原代码的改写请求返回 code_rewrite/unknown、空理由；
  超 token 约 6.9 秒返回固定错误，未截断后推断；超字节在父进程立即拒绝。
- **Q-27：** 原有到达时间、锁等待和非时间准入用例包含在本轮原样 727 项中，未发现回归。

## 本轮独立浏览器与数据库

使用固定上游的新隔离源码检出，四层补丁和当前 suggestion_dialog.js 核对一致。
前端是**开发构建副本**，未在本轮重新运行完整 npm build；index SHA256 为
`7b4239f9e3bf33f728ebc2bcd11b3ad4985eab0cdedcf6d7d42b03055da3a00d`，
与开发留存构建记录一致。浏览器操作、登录、模型进程和数据库对账均是本轮独立执行。

1. 虚构 Alice 登录 8136 新实例，打开脚本登记的“保留函数名与参数”代码案例。
2. 点击点踩，观察“点踩已保存”；实际本机 Laya 返回 `code.interface_changed`。
   界面显示“Laya 模型建议，未确认”“未运行代码”和“未定位可靠原文，仅显示类别”，无默认确认。
3. 显式“是这个问题”后显示“已记录你的确认”；点击完成。
4. 再点按钮撤销，随后新点踩再次显示实际建议；选择“都不是”，进入常规菜单并记录人工原因。
5. 再次撤销，将本测试实例模型目录改为不存在路径；新点踩仍成功，实际进程失败后回退常规菜单，取消菜单。

最终数据库：3 动作、2 撤销、2 模型生成、2 渲染、2 建议响应、2 常规展示、1 人工选因、1 关闭；
共 15 事件、5 Outbox。建议响应为 yes/none_matched；yes 属用户确认，模型生成仍未确认来源。
离线这次增加动作和常规展示/关闭，0 新模型建议。报告 generated=2、rendered=2、valid_response=2、
confirmed=1；撤销另报，历史不删。固定 as_of 读两次一致、数据库哈希不变，事件没有合成问答正文。
本轮实际单候选未演示多候选更正；该能力只引用既有 fixture 回归范围。

截图保存在 `var/qa-m53-independent/browser-confirmed.png` 与 `browser-offline-fallback.png`，
原始虚构数据库、服务日志、失败日志和初始库留在同目录，未提交认证文件或口令。

## 开放缺陷

### Q-28 / P2：模型参数缺失时先修改宿主，再失败

- 位置：`qa/m53_live_host.py:41–146`，CLI 参数在 `main` 中也未按 provision 子命令设为必需。
- 操作：新建空实例 8137；运行 `provision --data-dir <新实例> --base-url http://127.0.0.1:8137`，遗漏模型选项。
- 预期：在调用宿主 API 或写数据库前清楚拒绝，0 用户/Function/模型/聊天/研究记录增加。
- 实际：在 `args.model_python.resolve()` 抛 AttributeError；此时已有 3 用户、3 Function、1 模型、1 聊天，
  研究 sources=1、answers=1。补齐两个选项重试在 `/api/v1/auths/signup` 收到 403，无法按原流程恢复。
- 原生库真实观察支持自动评审意见；新增两条可移植回归用例保留失败，分别缺 model_python/model_dir。
- 修复/复验：宿主脚本维护者在任何写入前验证命令参数；两种缺项均零副作用，正确参数仍能在全新库完成。
  保留已生成失败实例作为证据，不通过清理旧库掩盖错误。

### Q-29 / P2：正确源码搭配任意旧构建也能启动验证宿主

- 位置：`qa/m53_live_host.py:20–37,181–184` 与 `qa/s1_browser_host.py:36`。
- 操作：另建固定上游并应用全部当前补丁/JS；仅把 build/index.html 写为带独立标记的无客户端 HTML，
  在新目录启动 8138。未改本轮正常浏览器实例或开发构建。
- 预期：验证宿主需将构建产物绑定到当前源码/补丁/客户端；不匹配时启动前拒绝。
- 实际：源码检查通过、宿主初始化成功，GET `/` 为 200，返回标记 `Unverified stale QA artifact`。
  index 文件存在和记录单文件哈希不能证明编译的 JS 包来自当前版本。
- 本轮该项为独立实际启动/HTTP 复现；没有伪称它已加入默认 pytest。结果见 stale-build-result.json。
- 修复/复验：验证工具维护者绑定完整产物与源码的可信构建记录，或执行对应构建；
  新鲜构建可启动，旧/缺失/篡改 bundle 被拒绝，然后重新核对实际浏览器客户端。
  不据此否定本轮已单独核对的正常浏览器副本，但自动化复现保障尚不完整。

## 复跑与交付范围

```powershell
uv sync --locked --extra dev --extra jev --no-editable --reinstall-package whynote
uv run --no-sync pytest tests/test_m53_independent.py -q
uv run --no-sync pytest tests qa/test_s0_regressions.py qa/test_manual_v1_acceptance.py -q
uv run --no-sync ruff check src tests integrations qa
uv run --no-sync ruff format --check src tests integrations qa
```

原生使用隔离宿主依赖解释器，设置 WHYNOTE_OPENWEBUI_SOURCE 指向四层补丁检出，运行
`python -m pytest integrations/openwebui/tests -q` 及
`python qa/native_regressions.py --source <source> --output <新目录> --admin-other-status 404`。
保留的机器可读证据见 [本轮证据目录](../qa/evidence/2026-09-29-m53-independent)。

按规约 §3.1 继续推送 PR #40 的同一分支；失败不能 skip、xfail 或降断言。
源码运行身份、模型权限和产物绑定缺口修复后再复验；GitHub 非作者有效批准与适用责任人签署另行完成。
环境级出站控制、真实材料质量与用户收益未验证；未使用真实云密钥、未开展真实本人试用。
原目录改动、开发库及旧审计材料保留。

飞书在原需求/字段/当前任务条目完成15次局部更新，各次使用刚读取的revision-id；
读回 PRD **424**、技术 **419**，历史归档与原链接集合保持。回执存于本轮证据目录。
8136/8137/8138 临时服务和测试标签页均已关闭，原目录脏文件清单与开始时一致。
