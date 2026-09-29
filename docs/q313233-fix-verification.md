# Q-31/Q-32/Q-33开发修复与验证

日期：2026-09-29。基线：QA提交f453ff8，PR #40。责任：本轮开发执行Codex；独立QA及责任人签署待补。
关联FR-06/13、TD-04/07；先补[预检及兼容契约](m5-live-laya-contract.md)，再实现。

## 变更

- Q-31：源码差异、对话框、固定上游HEAD及宿主入口构建检查显式抛异常，优化解释器不能跳过。
- Q-32：任务理由契约的语义错误统一转成invalid_response，继续回退菜单，动作/Outbox保留且不生成非法建议。
- Q-33：宿主写入前在所选Python中进行有60秒上限的离线预检：固定SDK版本及导入、五个manifest文件可读及SHA-256。
  不加载模型/分配GPU/推理/下载，禁止子进程pyc写入；错误不携带子进程正文。缺参/路径类型仍先检查。
  这是预检范围扩展，不撤销Q-28已独立通过的缺参契约；运行期加载继续原有校验。

事件结构、来源标记、Q-27到达时间与Q-30后端失效契约不变，无历史迁移。

## 已执行证据

| 检查 | 结果与范围 |
| --- | --- |
| 原QA四条失败 | 修复前4失败；原用例未修改，修复后通过。旧CI36521685406失败保留。 |
| 初始相关回归 | 70/70，覆盖宿主预检、实时建议及本地适配器。 |
| 新增边界 | 23/23；真实子进程普通/-O/环境优化三模式下的源码/对话框/HEAD/产物，SDK/文件校验、启动失败/超时拒绝。使用微型Git fixture，不冒充完整构建。 |
| 完整回归 | 802/802（核心及保留S0/manual），Ruff check及format通过；1条既有Starlette弃用警告。 |
| 实际固定宿主源码 | 正确源码三模式通过，合成README改动后三模式均拒绝；finally恢复原字节。既有完整构建记录验证通过。 |
| 实际新宿主8141 | 普通文本解释器、空模型目录均CLI退出2；每次前后21个宿主文件hash相同。合法固定Laya环境同实例provision退出0并登记合成案例。 |

实际源为8bd8b4f及四层补丁；沿用已有Node22构建，无本轮重新构建。模型revision为55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851，SDK laya0.3.21/transformers4.57.6。
[机器证据](../qa/evidence/2026-09-29-q313233-fix/summary.json)、[源码探针](../qa/evidence/2026-09-29-q313233-fix/source.json)、[宿主预检](../qa/evidence/2026-09-29-q313233-fix/runtime.json)。
本地完整/失败日志在隔离工作树var/q313233-*.log；临时8141服务已关闭，新合成数据及历史审计保留。

复跑：

```powershell
uv sync --extra dev --locked --no-editable --reinstall-package whynote
uv run --no-sync pytest tests qa/test_s0_regressions.py qa/test_manual_v1_acceptance.py -q
uv run --no-sync ruff check src tests integrations qa
uv run --no-sync ruff format --check src tests integrations qa
```

## 剩余门槛

上述为开发验证，Q-31/Q-32/Q-33尚待非作者独立复验。Q-28/Q-29/Q-30指定范围原独立通过结论保留。
本轮未重新运行浏览器、模型推理或前端构建，预检不承诺CUDA/显存或预检后文件不变。
出站隔离与跨进程单并发仍待处理；未执行外网泄漏/GPU耗尽试验。非作者APPROVED和适用签署仍缺。
M5-3a仍HOLD，M5-4a/4b未开启；未合并，完整FR及生产NO-GO。
