# Open WebUI 集成与测试

从仓库根目录操作，固定上游 `v0.11.4 / 8bd8b4fac5e059578ac0c74b3c18d11139f88b7d`。所有验证使用独立 loopback 实例、新数据目录和虚构身份；不指向运行中的宿主或用户数据库。

## 补丁与原生回归

补丁顺序是 native S0 → manual timing → S1 trial → M5 templates；CI 的 `native-regression` 验证完整补丁栈。锁定宿主依赖独立于 Whynote 运行依赖；CPU PyTorch 只满足上游导入。

```powershell
git clone --depth 1 --branch v0.11.4 https://github.com/open-webui/open-webui.git var/openwebui-source
git -C var/openwebui-source rev-parse HEAD
# 必须等于上面的固定 SHA；目录必须是新的隔离检出
foreach ($patchName in @('native-v0.11.4-s0.patch','manual-v0.11.4-timing.patch','s1-v0.11.4-trial.patch','m5-v0.11.4-templates.patch')) {
    $hostPatch = (Resolve-Path "integrations/openwebui/patches/$patchName").Path
    git -C var/openwebui-source apply --check $hostPatch
    if ($LASTEXITCODE -ne 0) { throw 'Patch check failed' }
    git -C var/openwebui-source apply $hostPatch
    if ($LASTEXITCODE -ne 0) { throw 'Patch application failed' }
}
New-Item -ItemType Directory -Force var/openwebui-source/src/lib/whynote | Out-Null
Copy-Item -LiteralPath integrations/openwebui/suggestion_dialog.js -Destination var/openwebui-source/src/lib/whynote/suggestion_dialog.js
uv venv --python 3.11 var/native-venv
uv pip sync --python var/native-venv/Scripts/python.exe --torch-backend cpu integrations/openwebui/tests/requirements.txt
$env:WHYNOTE_OPENWEBUI_SOURCE = (Resolve-Path var/openwebui-source).Path
& var/native-venv/Scripts/python.exe -m pytest integrations/openwebui/tests -q
```

Linux 使用 `var/native-venv/bin/python`。套件强制新临时 DATA_DIR、STATIC_DIR、SQLite，执行真实路由、ORM、迁移和删除；不替代登录、WebSocket、浏览器、供应商或模型质量验收。独立 Q-16 探针入口为 `qa/native_regressions.py --source <固定宿主> --output <新目录> --admin-other-status 404`。

## 宿主行为与兼容

| 入口 | 范围与保护 |
| --- | --- |
| S0 `s0_pipe.py` / `s0_action.py` | 固定虚构问答；Action 只绑定专用模型且非全局。服务端复核身份、聊天归属和对象 HMAC；签名票据与 SQLite 最新展示绑定 |
| manual-v1 | 每次明确点击产生新 UUID，重发保留原 ID；已完成点击重试不弹新菜单。预约提交后启 60 秒时钟，回调任务捕获到达时间；旧请求不续期 |
| S1 `s1_pipe.py` / `s1_action.py` | 自然完成并保存后才建立可信回执；编辑/删除/null 替换/撤权使其失效，恢复原文不复活。入口关闭仍完成宿主清理，历史候选不自动确认 |
| 本机 `local_chain_action.py` / `laya_action.py` | 单点踩动作和 Outbox 先保存；聊天生成预算与原因推断分开；本机手动路径沿用既有批准范围，不接生产 Gate |
| M5 `template_action.py` / `suggestion_dialog.js` | 合成对象白名单、模板开关与来源绑定；真实 Laya 建议未确认，fixture 来源另列。撤权/撤销/换版/切后端拒绝旧渲染与响应；常规菜单回退可用 |

S0 关闭原生评分及导出，并限定使用者只能输入 fixture；宿主仍保存输入。禁用后台标题/标签/续问、工具、记忆及旁路模型。只监听 `127.0.0.1`；环境变量不替代网络层隔离。云配置/密钥与数据条款见[供应商边界](../../../docs/s1-2r-provider-review.md)，不得把填写 key 当作出站许可。

评分关联仅由认证作者绑定本人聊天/消息，不可重绑定；只清理服务端验证且归属匹配的评分。旧记录为 `legacy_unverified`，保留并进入审核；账号删除按作者清理，其他作者伪关联不误删。迁移 `whynote_s0_v1` 的三个验证列和审核表不自动升级旧记录；回滚先停写，旧代码含 Q-16 缺陷，不可据此恢复对外服务。审核入口 `integrations/openwebui/review_feedback.py` 只读并拒绝覆盖输出。SQLite 并发证据不扩写成 PostgreSQL 通过。

S1 新增实例/会话/生成/当前回执表及费用列，旧 NULL 记录不开放反馈；宿主和 Whynote 两个 SQLite 库不宣称分布式原子。未知费用保留预留，重启/更换 key 不重置累计上限；回滚先关入口和连接，保留账本/事件，不删表重写历史。

## 合成复现入口

- S1：`qa.s1_mock_provider` + `qa/s1_browser_host.py serve/provision` + `qa.s1_http_probe`；mock 配置不读取云 key，仅固定虚构输入。
- 模板：`qa/m5_template_browser.py --data-dir <新目录> --port 8132`；须同时满足 `WHYNOTE_TEMPLATE_SYNTHETIC=1`、`WHYNOTE_LOCAL_CHAIN=1`、mock 研究配置和模板开关。
- 实时 Laya：Node 22 按上游锁文件构建后，`qa/m53_live_host.py build/serve/provision`；固定解释器/模型参数在任何写入前校验，构建须绑定源码及全部产物；不得用已有联调库。
- 研究：`python -m qa.s1_research_fixture --output <新目录>`、`python -m whynote.research_report --db <该目录/events.db> --instance synthetic-s1-study --user synthetic-alice --since <UTC> --as-of <UTC>`；固定来源/分母，报告只读。

生命周期测试证明活动 SQLite 的指定删除行为，同时保留备份/导出残留边界；不等于设备级擦除、完整日志或全部出站审计。完整签署规则、迁移步骤和旧失败可在[固定集成契约历史](https://github.com/262412/-Whynote/tree/9246dd5f143a02148a9328f79f5e188049f01563/docs/)查阅；验收/责任人状态见[质量基线](../../../docs/quality-baseline.md)。
