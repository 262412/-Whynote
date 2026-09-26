# 固定 Open WebUI 补丁回归

从知因仓库根目录执行。此套件默认不混入核心 pytest；CI 的 `native-regression` 单独应用补丁并执行，失败不跳过。只替换身份依赖和事件通知，执行真实 evaluations 路由、ORM、迁移和新建 SQLite；不验证登录、真实 WebSocket 或浏览器。浏览器证据见 `docs/s0-review-fixes.md`。

```powershell
git clone --depth 1 --branch v0.11.4 https://github.com/open-webui/open-webui.git var/openwebui-source
git -C var/openwebui-source rev-parse HEAD
# 必须为 8bd8b4fac5e059578ac0c74b3c18d11139f88b7d
$patch = (Resolve-Path integrations/openwebui/patches/native-v0.11.4-s0.patch).Path
git -C var/openwebui-source apply --check $patch
git -C var/openwebui-source apply $patch
uv venv --python 3.11 var/native-venv
uv pip sync --python var/native-venv/Scripts/python.exe --torch-backend cpu integrations/openwebui/tests/requirements.txt
$env:WHYNOTE_OPENWEBUI_SOURCE = (Resolve-Path var/openwebui-source).Path
& var/native-venv/Scripts/python.exe -m pytest integrations/openwebui/tests -q -s
```

Linux 使用 `var/native-venv/bin/python`。测试强制设置全新临时 DATA_DIR、数据库和 STATIC_DIR，固定上游 SHA 并验证补丁可逆向应用、实际导入模块均来自指定源码。不要指向正在使用的宿主检出；上游 config 初始化会复制静态文件。锁文件仅用于独立测试环境，包含上游依赖，不加入 Whynote 运行时依赖；CPU PyTorch 用于满足上游导入，不运行模型。更新依赖需重新执行 `requirements.txt` 头部的固定命令并复验。

生命周期测试创建本轮合成评分的一致性备份与导出，断言活动库删除后两者仍保留副本。临时测试目录按 pytest 正常保留策略处理，既有审计实例完全不参与此套件。字节扫描与 caplog 只覆盖本次活动 SQLite 和当前测试进程，不证明设备级擦除、全宿主日志或所有出站行为。
