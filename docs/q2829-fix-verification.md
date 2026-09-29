# Q-28 / Q-29 开发修复与复验记录

2026-09-29；PR #40，同一分支；修复起点QA提交ea9bc26，业务基线e1c4410。
输入飞书PRD424/技术419；FR-06/09/11/13/14、TD-02/04/07/10，M5-3b/D-19。
**开发修复验证通过，尚待独立复验及签署；未合并，生产NO-GO。**

## 修复

- Q-28：CLI和provision函数都在任何宿主API/数据库写入前检查两个模型参数及路径类型。
  缺参返回明确参数错误；不清理此前失败实例，不扩展为可覆盖既有实例的重复provision。
- Q-29：增加build命令，校验补丁/客户端后用Node22实际执行npm run build；成功且前后源码指纹相同才出具记录。
  Git跟踪及非忽略新增源码、build中全部产物（路径＋SHA-256）绑定，启动前重验。
  旧/缺失/篡改/新增产物均拒绝，失败构建撤销旧记录；不能直接给旧产物补一份记录。
  可信本地操作者可迁移完整相同源码与产物；记录不是对抗本机写权限攻击的签名。
- 业务事件、Laya策略、TTL、来源、Outbox和生产开关不变。

## 执行证据

| 验证 | 本轮结果 |
| --- | --- |
| QA原用例复现 | ea9bc26的15项原样运行：13通过/2失败，Q-28两条失败与CI36515635840一致；日志保留 |
| 修复后原独立用例 | 原文件无修改，15/15通过 |
| 新增预检与产物边界 | 14/14，含CLI缺项、无效路径、合法构建、旧HTML/JS、缺JS、额外JS、无记录、源码变化、错误版本、构建失败、构建中源码变化 |
| 完整回归 | 756/756，87.65秒；Ruff lint/format通过（92文件）；既有Starlette/httpx警告保留 |
| 真实完整构建 | Node22.23.3，固定Open WebUI8bd8b4f、四层补丁与当前JS；完整npm build成功，5,604个产物 |
| 实际启动拒绝 | 分别改写本次构建的index与一个JS bundle，serve均非零退出且不创建data-dir；随后按原字节恢复并重验通过 |
| 真实宿主缺参与重试 | 8139新实例，两种缺参均返回2，user/function/model/chat仍0，研究库未创建；补参数在同一实例完成3用户/3Function/1模型/1聊天及登记 |
| 新构建实际浏览器 | 合成Alice登录，实际Laya返回code.interface_changed；显式确认成功。4事件/1Outbox，生成/渲染/确认=1/1/1；固定as_of报告重复一致且数据库字节不变 |

本轮新增测试最初错误地拦截了Git子进程，10个测试作者错误修正为只替换构建调用；未登记为业务缺陷，未降低业务断言。
[机器可读证据](../qa/evidence/2026-09-29-q2829-fix)包含本次构建身份、拒绝结果、缺参零副作用和浏览器对账。
原独立QA报告、740通过/2失败和旧构建200响应证据原样保留。

## 复现

```powershell
uv run --no-sync pytest tests/test_m53_independent.py tests/test_m53_host_preflight.py -q
uv run --no-sync pytest tests qa/test_s0_regressions.py qa/test_manual_v1_acceptance.py -q
uv run --no-sync ruff check src tests integrations qa
uv run --no-sync ruff format --check src tests integrations qa
# 先按上游锁文件安装依赖，并把Node22及npm放在PATH
python qa/m53_live_host.py build --source <固定四层补丁宿主>
python qa/m53_live_host.py serve --source <同宿主> --data-dir <全新目录> --port 8139
python qa/m53_live_host.py provision --data-dir <同目录> --base-url http://127.0.0.1:8139 --model-python <已有Laya解释器> --model-dir <固定权重目录>
```

serve/provision需使用具有原生宿主依赖的解释器。构建记录属于本机受控构建过程，不手工补写。
临时8139服务与测试标签页已关闭，构建记录、新合成库、失败日志及原QA失败实例保留。
未重跑独立QA的全部浏览器序列；原生80/80和探针58/58由本候选CI另核对，不能算本轮本地独立执行。
Q-28/Q-29仍待非作者复验；APPROVED与来源/兼容/回滚签署未取得。M5-3a准入HOLD，M5-4a/4b及生产未开放。

## CI、同步及评审边界

修复提交c89d240的[CI36516877872](https://github.com/262412/-Whynote/actions/runs/36516877872)
quality通过（716核心＋9保留S0＋31manual），native通过（80原生＋58探针）；后续文档提交仍核对最新head。
飞书原条目已局部更新并读回PRD424→432、技术419→426；两项标为开发修复通过、待独立复验，旧失败保留。

复查时还发现原QA提交ea9bc26上的三条自动评审意见，未纳入本轮Q-28/Q-29修复结论：

- [出站隔离](https://github.com/262412/-Whynote/pull/40#discussion_r4129253091)：Python connect拦截不等同OS级网络隔离；环境级边界尚未验收。
- [回滚后在途回执](https://github.com/262412/-Whynote/pull/40#discussion_r4129253095)：待复现并处理live切fixture时可选绑定字段比较。
- [并发进程](https://github.com/262412/-Whynote/pull/40#discussion_r4129253098)：待核对跨进程单并发约束与GPU占用。

三项交后端/运行环境维护者复现处理，随后QA验证；不在无实测时另报新QA编号或标为已解决。
本轮没有修改这些业务模块，未申请合并、未产生APPROVED；原分支和工作树保留。
