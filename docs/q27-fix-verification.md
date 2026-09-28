# Q-27 渲染回执到达时间修复

2026-09-29；PR #39，输入 PRD371 / 技术370，修复基线 `5227e61863daf0ed17adac0d2599cc4e9c6c2a71`。
涉及 FR-06/11/13/15、TD-04/10/11；后端/客户端负责实现，独立 QA 负责复验，产品/客户端/数据责任人负责适用签署。

## 复现与修复

在重新安装非 editable 包后，原样运行独立用例
`tests/test_templates_independent.py::test_arrived_render_receipt_survives_slow_host_recheck`：
1 失败，耗时0.54秒。回执时间1788220859.5、截止1788220860.0、复查完成1788220860.5；
结果superseded，事件只有动作和建议生成，Outbox为1。独立失败报告与QA提交保持原样。

`template_action.run` 现在将 `receive` 捕获的渲染回调到达时间传入 `suggestions.render`。
`render` 在进入SQLite事务前验证内部时间参数；未提供时在入口捕获，然后按到达时间核验期限。
事务内继续核对权限、动作、研究开关、协议、对象版本、建议版本和展示绑定。
客户端回执仍只允许binding；事件结构、旧数据和60秒有效期不变。

同一独立用例修复后记录了 `m52_render_reported`。复查完成时已过截止点，后续响应超时，
流程进入常规菜单回退；用户动作保持active，Outbox仍为1，没有生成研究确认。
按时渲染因此计入渲染分母，后续迟到响应不会取得新的有效期。

## 本轮开发验证

- 原独立23项加新开发23项：46通过，7.83秒；未修改独立QA断言，未skip/xfail。
- 完整回归：699通过，78.40秒。保留既有Starlette/httpx弃用警告1条。
- Ruff lint/format通过，87个Python文件格式一致；首次格式检查发现新测试长装饰器，格式化后复查通过。
- 新23项：实际SQLite锁等待跨截止点2项；排队期间停用/撤权/删除/动作撤销/新建议/建议撤销/协议及模型版本变化8项；宿主复查跨截止点期间停用/撤销/换版/撤权4项；原有效期外到达3项；非法服务器时间5项；客户端附加伪造时间1项。
- 排队测试还核对报告1渲染/0有效响应、unresponded；动作未撤销时继续有效。

```powershell
uv sync --extra dev --locked --no-editable --reinstall-package whynote
uv run --no-sync pytest tests/test_render_receipt_timing.py tests/test_templates_independent.py -q -s
uv run --no-sync ruff check src tests integrations qa
uv run --no-sync ruff format --check src tests integrations qa
uv run --no-sync pytest tests qa/test_s0_regressions.py qa/test_manual_v1_acceptance.py -q
```

本轮未重新运行浏览器或本地原生整套；前次独立QA的浏览器、原生80/80、探针58/58属于
`5474496`的历史证据。修复提交`c00408b079e5bbe651f9324ad7c45e8889ef949c`的
[CI 36455026963](https://github.com/262412/-Whynote/actions/runs/36455026963)已逐项核验：
quality通过（659+9+31），native-regression通过（原生80、Q16探针58）。

## 交付回执

飞书原11个受影响条目局部更新并逐项读回，PRD371→377、技术370→375；原失败记录和链接保留，
见[回执](../qa/evidence/2026-09-29-q27-fix/feishu-readback.json)。原独立断言、报告和证据目录相对5227e61无差异。
主目录只更新当前计划的日期、输入与M5-2b剩余项，另外15个已有改动文件摘要保持一致，
见[保护记录](../qa/evidence/2026-09-29-q27-fix/primary-plan-update.json)。代码在原PR工作树，真实服务和数据未改动。
本轮最后一次提交只追加文档/回执，最新head的CI另在PR检查中核验；不以c00408b检查代替后续提交检查。

## 待完成

修复后的独立QA复验、最新head非作者有效批准，以及适用产品/客户端/数据契约与结果签署。
PR保持未合并，工作树用于后续复验。真实本人试用参数另行确认，真实试用未开启，生产NO-GO。
开发者运行独立用例不等于独立QA签署；旧失败证据见[独立报告](qa-m5-templates-independent.md)。
