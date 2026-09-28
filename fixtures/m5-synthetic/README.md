# M5-0a 合成示例

本目录所有正文、标签、审阅引用和 UUID 均为开发时编写的虚构输入。
没有下载真实语料，也没有用户案例或真实签署；不能把结果用于推断模型质量。
`source/revision/config` 只标识拟验证的来源结构，不证明这些行来自该仓库。
WildFeedback 示例是待实测的会话绑定协议，不是官方原文件 schema 样本。

在仓库根目录运行（output 须为新目录）：

```powershell
uv sync --extra dev --locked --no-editable
uv run --no-sync python -m whynote.source_review --manifest fixtures/m5-synthetic/batch.json --output var/m5-synthetic-run
```

预期三个来源为 mapped、总计四个目标，输出每源 records/rejected 及 report.json。
`ready_for_replay` 仅指合成记录具备审阅/分组字段，不代表公开数据准入。
退出码：0=三源全部映射且无分割冲突；2=存在 HOLD/排除/部分失败/分割冲突；
1=输入配置或输出 IO 失败。HOLD 仍保存报告，某源失败不阻塞其余源。

审阅规范和字段见 [M5-0a 契约](../../docs/m5-source-contract.md)。
