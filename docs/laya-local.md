# 本机 Laya Multilingual 测试

## 现在如何使用

模型和专用 Python 环境都已安装在 `D:/PythonProject/jev项目/var/`，不需要 DeepSeek、OpenAI 或 TypeSafe API Key。

1. 打开 **http://127.0.0.1:8766/**。
2. 填入问题、回答和可选反馈，点击「本地分析」。也可以点击「填入虚构示例」。
3. 查看八类原因分数、两个 Noul 信号及实际模型版本。结果始终是模型推测，未确认；不会写入聊天、反馈事件或研究台账。

本机快捷入口：`var/laya/start.cmd` 启动，`var/laya/stop.cmd` 停止。重启服务后刷新页面。服务在后台运行，只监听 127.0.0.1:8766；没有配置开机自启。关闭网页不会停止模型，停止脚本会释放 GPU。进程与启动日志记录在 `var/laya/`，日志不记录测试正文。启动脚本拒绝复用已占用端口。

| 内容 | 本机位置 |
|---|---|
| 多语言权重、配置、tokenizer | `D:/PythonProject/jev项目/var/models/laya/multilingual/` |
| 原始模型说明 | `D:/PythonProject/jev项目/var/models/laya/README.md` |
| 隔离运行环境与已安装 Whynote 包 | `D:/PythonProject/jev项目/var/laya-runtime/` |
| 启停脚本、说明和截图 | `D:/PythonProject/jev项目/var/laya/` |

主目录原有未提交源码保留；本次代码在 `codex/laya-local` 的隔离工作树中，安装的是该切片构建的非 editable 包。运行不依赖工作树保持在该分支。模型文件约 678 MB；GPU PyTorch 下载约 2.6 GiB，解包后的运行环境更大。

## 模型与项目接口

[官方模型](https://huggingface.co/convaiinnovations/laya)中根目录是英文版本，本次选择 `multilingual`，固定修订 `55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851`。SDK `laya==0.3.21`、Transformers `4.57.6`、PyTorch `2.11.0+cu128`。启动前验证五个模型文件的 SHA-256，离线加载本机 safetensors，不执行仓库远程 Python 文件。

Python 入口：`whynote.laya_local.LocalLaya.load(..., enabled=True)` 和 `evaluate_reason(state_factory, enabled=True)`。返回字段与现有 Jev 原因适配器对齐，但 provider 明确为 `laya_local`，版本是实际 checkpoint，绝不冒用 Jev 身份。页面调用 `POST /api/reason`；会话令牌从同源页面获取，请求字段是 `question`、`answer`、`feedback`。当前页面为独立手动模型测试入口，没有接入 Open WebUI 自动归因消费者。

Laya 是原因分类模型，不能替代聊天生成模型。所有预测为 `model_inferred_unconfirmed`，未经本项目校准；700 模型 token / 8192 UTF-8 字节以内输入才被接受。详细边界见 [本地契约](laya-local-contract.md)。

## 在新环境复现安装

在包含本切片代码的 checkout 执行；`$projectRoot` 可指向专门保存模型和环境的项目目录。按顺序完成安装，不同时向同一个 venv 安装依赖。

```powershell
$projectRoot = 'D:/PythonProject/jev项目'
hf download convaiinnovations/laya --revision 55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851 --include 'multilingual/*' --include README.md --local-dir "$projectRoot/var/models/laya"
uv venv --python 3.11 "$projectRoot/var/laya-runtime"
uv pip install --python "$projectRoot/var/laya-runtime/Scripts/python.exe" --extra-index-url https://download.pytorch.org/whl/cu128 --index-strategy unsafe-best-match -r requirements/laya-windows.lock
uv pip install --python "$projectRoot/var/laya-runtime/Scripts/python.exe" --no-deps .
uv pip check --python "$projectRoot/var/laya-runtime/Scripts/python.exe"
./scripts/start-laya-local.ps1 -ProjectRoot $projectRoot
# 停止：./scripts/stop-laya-local.ps1 -ProjectRoot $projectRoot
```

该锁文件固定本轮 Windows / Python 3.11 / CUDA 12.8 环境，普通 CI 不下载模型或 GPU 依赖。`--device cpu` 可通过模块 CLI 显式选择 CPU；本轮只验证 GPU 性能。

## 开发验证与效果限制（2026-09-28）

- 6 个下载文件与远端固定版本校验一致；根目录英文和其他模型文件未下载。加载时再次校验 multilingual 的五个文件。
- RTX 5070 Ti Laptop 12 GB、约 32 GB RAM；实际 CUDA 12.8 推理。离线冒烟禁用 socket connect/connect_ex，3 个虚构中文案例成功返回合法 Choice/Noul；峰值 allocated 显存约 1506 MiB。加载约 6.5 秒，首例约 299 ms，后续两例约 14–15 ms；这些是单次开发测量，不是性能基准。
- **效果有明显不足**：算术错误例返回 factual_error；中文指令例却返回 factual_error；无关回答例返回 other_or_unknown。后两例不符合开发者预期。3 个演示不是 gold 或效果评测，保留原始输出，未筛选失败例。
- 本地适配器与页面契约 24 项；核心及保留 QA 合计 329 项；Ruff lint / format。真实回环 HTTP 10/10，含重复分析、跨源/Host、缺令牌、空输入、未知字段、token 窗口、特殊 token 和大请求拒绝。
- 实际浏览器：填入虚构示例、提交、查看原因/八类分数/未确认来源通过；停止后端口释放、重启与再次分析通过。开发浏览器证据不等于独立 QA。未重新运行既有 Open WebUI 原生套件；本切片没有修改原生补丁。
- 证据：`qa/evidence/2026-09-28-laya-local/{smoke,http}.json`；复现脚本 `qa/laya_local_smoke.py`、`qa/laya_local_http.py`。只有前者显式使用 GPU，均不会被普通 pytest 自动启动。

安装过程中曾混入 CPU/GPU 两版 Torch；已卸载该新环境内四组重复发行版，再单独安装固定 CUDA 版，`uv pip check` 无冲突。HTTP 探针首轮用了未超过 tokenizer 限制的重复文字，错误期待 422；改用真实超过窗口的固定输入后通过，没有修改输入门槛。

本地手动运行已验证；PR 合并需最新 CI、非作者批准及适用签署。模型质量、自动菜单建议、真实上下文消费者、校准、完整 FR-06 和生产准入仍未通过。
