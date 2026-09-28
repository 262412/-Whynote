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
`5474496`的历史证据。新提交的远端CI另行核验后记录，不用旧绿色结果替代。

## 待完成

修复后的独立QA复验、最新head非作者有效批准，以及适用产品/客户端/数据契约与结果签署。
PR保持未合并，工作树用于后续复验。真实本人试用参数另行确认，真实试用未开启，生产NO-GO。
开发者运行独立用例不等于独立QA签署；旧失败证据见[独立报告](qa-m5-templates-independent.md)。
