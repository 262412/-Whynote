# S1-1 契约与虚构回包开发交付

2026-09-27；基线 `cdc0673fe933ddb9c0686aa09cdeb7eef7b76eb2`；分支 `codex/s1-cloud-contract`。

## 交付

- [S1-1契约v1](s1-cloud-contract.md)：DeepSeek及100 CNY上限已由用户确认；具体模型、历史范围、留存/删除和生成资格为待签建议，列出预算/日志/完成回执的实际实现缺口。
- [S1-0来源审查](s1-source-review.md)：固定三源版本、文件列表及卡片hash；记录WildFB的history类型矛盾。空manifest可审阅，未下载数据文件或导入语料。
- [S1-3记录协议](s1-trial-protocol.md)：来源分层、分母与固定对账案例草案，未采集。
- `qa/s1_mock_provider.py`：只接受固定虚构消息的本地Chat Completions回包工具，供下一片测试完成/截断/断流/取消及429/503；没有上游客户端、不保存请求、不生成伪造usage。

## 本轮实测

| 检查 | 结果 / 范围 |
| --- | --- |
| 锁文件安装 | `uv sync --extra dev --locked --no-editable --reinstall-package whynote`；复用环境曾残留旧whynote包并导致5个收集错误，重建本项目安装后修复；无依赖版本变更 |
| Ruff lint / format | `src tests integrations qa` 通过，32个Python文件格式符合 |
| 新增回包测试 | 13/13，已计入核心 |
| 全套本地 | 166/166：核心126、保留票据9、独立用例开发复跑31；1条既有Starlette依赖弃用警告 |
| 实际回环TCP回包 | 10/10：发现模型、非流式、流式stop/length/EOF、429/503、读超时、非fixture拒绝、错误认证 |

日志：`var/s1-mock-tests.log`、`var/s1-all-tests.log`、`var/s1-mock-http-summary.json`；摘要在 `qa/evidence/2026-09-27-s1-contract/summary.json`。初次环境失败日志保留为 `var/s1-core-stale-install.log`。未修改历史QA用例或删除历史失败。

回环TCP仅针对新虚构回包工具，没有启动Open WebUI，也没有执行真实登录、浏览器、S1动态菜单或云调用。临时工具进程已关闭。固定宿主源码的终态、聊天更新、后台调用与错误日志检查属于代码审查，不能称宿主实测通过。最新提交CI在对应PR核对。

## 剩余与交付边界

S1-1契约待用户按已有责任角色确认；实际出站还缺provider适用留存/训练设置证据、服务端凭据配置和S1-2/3运行验收。100 CNY是已确认的预算要求，当前没有声称预算控制已执行。

S1-2依赖冻结的契约，下一片实现服务端动态目标回执/版本、单人会话准入、受控云生成及预算门控，再接现有菜单和真实浏览器对账。不能只删除S0的fixture校验。S1-0每源准入后才抽样；S1-4实际试用须按协议采集。

本切片沿用非作者APPROVED及结果签署门槛。待评审时保留分支/工作树；旧`codex/manual-acceptance-fixes`已合并且工作树已切换用于新片，旧审计数据保留，本轮不批量处置历史分支。原目录其他改动不动。完整FR、真实数据扩大及生产继续NO-GO。
