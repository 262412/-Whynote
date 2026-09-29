# Q-36/Q-37开发修复与验证

2026-09-29；QA基线b721374，PR #40。关联FR-06/13/14、TD-04/07；本轮开发执行Codex，独立QA及责任人签署待补。
先更新[worker边界契约](m5-live-laya-contract.md)，事件结构、模型revision、输入预算与生产开关不变。

## 修复

- Q-36：错误码先验证字符串类型，再检查固定集合；数组、对象及其他非法值统一invalid_response。推断适配、worker信封和worker异常输出复用相同分类，原合法码保持不变。
- Q-37：所选可信Python以-I/-B/-X utf8运行固定bootstrap，显式加入调用模块所在的绝对包根，再导入worker。当前目录、PYTHONPATH/PYTHONHOME/用户site不参与worker启动选择；不靠环境PYTHONUTF8设编码。
- 超时仍为60秒，取消/超时继续kill并await wait；不增加OS隔离或跨进程并发承诺。解释器、系统site和显式包根须受信。

## 已执行证据

| 检查 | 结果及边界 |
| --- | --- |
| 原独立worker八例 | 修复前5通过/3失败；原断言未修改，修复后8/8。旧CI36530298116失败保留。 |
| 相关回归 | 36/36，包含原独立八例及live建议边界。 |
| 新增边界 | 17/17：其余固定错误码及嵌套非法码保留动作/Outbox/菜单；普通目录、CWD同名包和环境路径/启动钩子下实际worker返回精确model_load_failed，证实合法入口启动；实际轻量子进程超时/取消后均被回收。 |
| 完整回归 | 841/841（核心＋保留S0/manual），Ruff check/format通过；1条既有Starlette弃用警告。 |
| 实际固定Laya | 两次串行合成调用，分别普通目录、CWD同名包＋启动钩子＋无效PYTHONHOME/PYTHONPATH/USERBASE；均返回code_rewrite/code.interface_changed，展示契约合法，未触发替代包标记。 |

实际Laya固定revision 55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851，SDK laya0.3.21/transformers4.57.6；没有真实上下文或外发试验。
已重新安装锁定非editable包；LF规范化后安装worker与修改源码摘要相同。两次模型调用只验证启动、输出与契约，不作为理由质量评测。
[机器汇总](../qa/evidence/2026-09-29-q3637-fix/summary.json)、[实际worker结果](../qa/evidence/2026-09-29-q3637-fix/runtime.json)。详细日志留在隔离工作树var/q3637-*.log；合成探针和历史数据保留，子进程均已结束，本轮未启动HTTP宿主。

复跑：

```powershell
uv sync --extra dev --locked --no-editable --reinstall-package whynote
uv run --no-sync pytest tests qa/test_s0_regressions.py qa/test_manual_v1_acceptance.py -q
uv run --no-sync ruff check src tests integrations qa
uv run --no-sync ruff format --check src tests integrations qa
```

## 剩余门槛

Q-36/Q-37仍待独立复验；Q-28～Q-35各自指定范围独立通过结论保留。未重跑浏览器、宿主部署清单或前端构建。
-I是Python导入边界，不能抵抗已被修改的可信解释器/系统site/包根，也不等于OS级出站隔离。
出站隔离、跨进程并发、非作者APPROVED及适用签署仍阻断。M5-3a准入HOLD，M5-4a/b及真实本人试用未开启；未合并，完整FR和生产NO-GO。
