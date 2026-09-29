# M5-3a准备 / M5-3b实时Laya合成集成

2026-09-29。基线main `5abdc580c7c510b247fa2e1db1d0fcb40f96416e`；D-19；读取飞书PRD406/技术401。
需求与兼容见[实时契约](m5-live-laya-contract.md)，三源准备见[来源记录](m53-source-preparation.md)。

## 实现

现有模板Action可显式选择laya_local；先保存动作/Outbox，准入通过后将直接父问题/当前回答交给固定本机模型。
真实任务路由及理由使用现有C方案，保留通用回退与证据要求，不由fixture指定最终reason_ids。
只接受scripted登记和服务端对象版本白名单，所有mock研究开关继续保留；未开放真实聊天研究。

独立进程禁网络、使用固定权重/SDK校验，8192字节/700token、不截断，总超时60秒且终止进程。
结果后再次验证宿主权限/版本，事务内再验动作与登记。超时、离线、非法结果保持动作并回到原菜单，
unknown/no_match分开记录。实时生成来源为model_inferred_unconfirmed，显式yes/correct才是user。
报告按模型来源/推断版本分层，仍为synthetic_development_only；原事件和旧fixture来源不改写。

当前固定C方案每次最多一个候选，原文没有模型定位证据，故显示类别及“不运行代码”的说明。
多候选更正仍由既有fixture回归覆盖，不称本次实际模型已经演示多候选更正。

## 开发验证

- 新增28项：来源与确认、同点击重放、拒识、固定失败码、在途撤销/撤权/换版/停用、未登记目标、配置拒绝、非法模型结果、材料与回退、实际缺失进程/超长、超时/取消回收进程及单候选上限。
- 本地最终完整727项通过（82.56秒，含新28项），重新安装非editable包后执行。远端CI另核对最新head。Ruff lint/format通过，90文件格式一致。
- 当前Laya真实GPU单次接口探针：code_rewrite→code.interface_changed；独立进程没有fixture预测输入。
- Open WebUI固定`8bd8b4f`与4层补丁经临时Git index逐文件对比；完整生产前端用Node22构建两次成功。保留宿主既有Svelte警告。
- 实际登录完整Open WebUI、实际本机模型、独立新SQLite：合成问题与回答由开发脚本明确保存；点踩→模型建议→实际渲染→确认→完成→同按钮撤销→再次点踩→都不是→常规人工原因。随后最终构建复验另一次真实建议/确认。回执、事件与报告保存在[证据目录](../qa/evidence/2026-09-29-m53)。这些是开发者合成操作，不能计作真实用户效用。

首次Node24安装被上游engine约束拒绝，改用Node22。首次宿主启动因旧S1单层反向补丁检查与M5叠加不兼容失败，
改为完整补丁栈的临时index验证并检查末层补丁。首次建例脚本遗漏qualify必需chat_id/messages，修复后在新库完成。
失败日志与初始库保留在工作树var，不降低业务断言或改旧失败证据。

最终浏览器对账：4次生成/4次渲染、3组有效响应（2次yes、1次都不是）、22条事件/7条Outbox。
其中一次开发操作停留超过60秒而未响应，回到常规菜单；该未响应保留在分母。3次动作撤销，当前仅最后一次确认有效。
相同截止时间报告两次完全一致、读取前后数据库hash不变；事件导出不含问答正文。临时8134/8135服务已关闭，数据库与失败日志保留。

## 复现

```powershell
uv sync --extra dev --locked --no-editable --reinstall-package whynote
uv run --no-sync pytest tests/test_live_suggestions.py -q
uv run --no-sync ruff check src tests integrations qa
uv run --no-sync ruff format --check src tests integrations qa
uv run --no-sync pytest tests qa/test_s0_regressions.py qa/test_manual_v1_acceptance.py -q
```

先按锁文件安装固定宿主前端依赖，在PATH中启用Node22及npm，运行`python qa/m53_live_host.py build --source <宿主>`生成完整产物与源码绑定记录；旧npm构建目录需重建。然后用具备该宿主依赖的Python运行`qa/m53_live_host.py serve --source <宿主> --data-dir <新目录> --port 8135`；
然后运行`provision --data-dir <同目录> --base-url http://127.0.0.1:8135 --model-python <已有Laya解释器> --model-dir <固定权重>`。
serve强制新目录；默认建立两个虚构用户，登录alice使用源码中的公开合成测试口令。不得复用真实实例或真实数据库。
Laya运行环境和权重沿用已安装路径，不需云凭证；原DeepSeek/Laya联调实例未修改。

## 剩余事项

M5-3a：三源固定文件元数据已备齐，未下载真实行，逐源HOLD。用途/上游许可链/内容筛查、受控目录与访问、
保留删除期、小批范围及责任人准入仍缺；用户自愿选定案例未提供。真实schema、映射/排除分布与探索/留出分割未完成。

M5-3b：本轮为开发实测，完整链路独立QA、非作者批准和来源/兼容/回滚适用签署仍待完成。不开真实研究。
M5-G既有合并后治理欠项保留；M5-4a样本量、独立标签及数值阈值未代填，M5-4b未启动，生产NO-GO。

## 交付回执

候选[PR #40](https://github.com/262412/-Whynote/pull/40)，业务提交2319885；未合并。
该提交[CI36513229997](https://github.com/262412/-Whynote/actions/runs/36513229997)
quality通过（687核心＋9保留S0＋31manual），native-regression通过（80原生＋58探针）。
追加文档后最终head仍须另核对，不以旧绿色替代。
飞书按原需求/字段/当前任务局部更新并读回PRD406→416、技术401→412；[逐条回执](../qa/evidence/2026-09-29-m53/feishu-sync.json)。

当前GitHub只有机器人COMMENTED（评审额度耗尽），无非作者APPROVED。按开发规约等待独立QA与
来源/兼容/回滚适用签署；未执行合并。分支及工作树保留用于复验，var下模型探针、构建/失败日志和新合成数据库保留。
原主目录未切分支/重置，原代码与历史数据不动；仅同步当前开发计划，其他未提交改动逐文件hash保护。
PR #35～39既有治理欠项仍由M5-G收尾，不冒称本轮已补齐。
