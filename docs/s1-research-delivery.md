# S1-3 来源登记、合格回答台账与只读报告

2026-09-28；从 main `5ee95c5` 开发，分支 `codex/s1-source-reporting`。
需求 FR-11/14/15、TD-03/06/09/11/14、D-16/17；飞书输入 PRD/技术 271/271。
本页只记录开发验证。独立 QA、契约评审、非作者批准和责任人结果签署尚未完成。

## 本轮实现

- `research.py`：受控本地来源登记，验证实例/主体/会话和协议/研究/反馈引用；追加版本、幂等和并发前序检查。
- `s1.py`：reserve 冻结来源及版本，可信保存同事务追加合格回答；失败、未保存、失效、撤权及重生成有历史。
  未点踩回答同样进入台账，旧库不回填，旧来源不根据当前清单猜测。
- `research_report.py`：只读连接和单个快照，按来源对账全部合格回答与动作，复用 manual-v1 操作/时间语义。
  不同来源、操作者角色、版本分层和缺失均明确列出，报告不含正文或密钥。
- 浏览器和 Pipe/Action 拒绝来源登记字段；新增功能默认关闭，仅允许 mock。
  没有真实公开导入、云研究记录或模型推断调用。

字段、删除/失效口径及旧数据兼容以[工程契约](s1-research-contract.md)为准。
新增四张追加表，既有事件/Outbox schema 与意义不变。研究配置非法或停用时，清理钩子仍能失效已有记录，避免阻断宿主删除。关闭新记录或回退代码可停用；表与审计证据保留。

## 开发验证

- 核心及保留 QA **297/297**（新增 S1-3 39 项），Ruff check/format 和 diff 检查通过。
- 固定案例通过真实 TrialStore、Action 和事件存储执行；宿主归属查询被虚构对象替代。
  自然组 2 个合格回答/1 个被点踩；脚本 3/3；公开模拟 2/2，角色 evaluator；缺来源 1/0 单列。
- 中止与 length 对应的两项不完整生成、一个未保存候选不进入合格分母。
  来源变更不回填，保存重试不重复，完成 click 重连不弹菜单，撤销后重踩不重复增加回答分子。
- 明确更正、跳过、拒填、都不是、关闭/未响应、展示未知、迟到、窗后撤销和计时缺失；
  先前截止时点在后续撤权后仍可重放出完全相同报告，数据库读前后字节一致。
- 历史 258 项保持通过；本轮没有新增浏览器结论。既有 S1-2 独立浏览器证据不扩展为 S1-3 验收。
- 新增原生用例使用固定 Open WebUI 的真实 ORM、保存钩子、Action 和聊天删除，模拟 provider 结果。
  实际原生回归 **75/75**（既有72项＋新增三来源3项）；原生环境只发往临时回环mock，无真实供应商调用。

最初固定用例把 Outbox 总数预期写成 6，实际为 6 条 gate 加 1 条撤销。
已按两个 topic 分别断言；业务代码没有为通过该断言而改变。初次局部运行还发现环境中安装包缓存未刷新，
强制重装本项目后重跑；最终结果来自当前安装代码。

## 可复跑入口

使用锁文件环境，每次输出到新目录（已存在即拒绝）：

```powershell
uv sync --extra dev --locked --no-editable --reinstall-package whynote
uv run --no-sync python -m qa.s1_research_fixture --output var/s1-research-new-cases
uv run --no-sync python -m whynote.research_report --db var/s1-research-new-cases/events.db --instance synthetic-s1-study --user synthetic-alice --since 2026-09-01T00:00:00Z --as-of 2026-09-03T00:00:00Z
```

fixture 不启动服务、不发送网络请求、不读取真实密钥。研究数据库留在被忽略的 var 下；
完整元数据报告保存在输出目录的 report.json，仓库仅保存可审阅的虚构证据。
报告日期是工程测试输入，不是冻结了真实研究观察期。

本地登记入口：

```powershell
uv run --no-sync python -m whynote.research --config <受控虚构配置路径> --chat <已准入chat UUID> --registration <严格登记JSON路径>
```

首次正式研究 attempt 之前，服务端须验证宿主归属并 enroll 会话，再登记；接口不自动授予会话权限。
现有 mock 实例也可先完成非研究的准备轮，登记后再开启后续研究记录，准备轮不补入分母。
配置须有 research_enabled=true、mode=mock、research_study_ref、research_protocol_ref；
可选 research_versions 保存已核对的 host_sha/patch_sha256/config_ref/model_revision，未知保持 null。
登记示例由 fixture 的 registration() 生成，context_ref 使用 chat_id；公开原始标签值不允许写入。
修改登记必须传 --expected-registration-id，重试使用原 registration_id。

## 剩余门槛

S1-0 三源 HOLD；真实供应商条款、运行/出站/副本处置、研究观察期/样本量/阈值/价值量表和限定签署仍缺。
本轮仅开发描述性对账，不开展 S1-4、不宣称 S1-0～S1-2 全部完成，不开启 Jev/Laya 或训练导出。
待独立 QA 与所需审批后再判定开发验收；合并须满足开发规约 3.2。生产继续 NO-GO。
