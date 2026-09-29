# M5-3b 实时Laya的合成确认契约

需求FR-01/06/09/10/11/13/14、TD-02/04/07/09/10；D-19。基线main5abdc580，读取PRD406/技术401。

## 准入与数据

原mock研究、suggestion、template开关继续有效。新增`suggestion_backend`默认`fixture`；
只有显式`laya_local`进入实时路径，要求固定模型revision、绝对Python/权重目录，
以及服务端`suggestion_synthetic_targets`中的对象ID和版本白名单。登记来源须scripted。
先检查主体、版本、登记及白名单，再读取直接父问题与目标回答。没有私人聊天自动抓取或真实研究开关。

点踩动作/Outbox先提交。实时预测在独立本地进程执行，进程禁网络，原文经stdin短期传递，
不存入日志/队列/事件。复用已固定Laya checkpoint及SDK校验、C方案任务路由、逐理由判断与通用回退；
保留8192 UTF-8字节/700输入token限制，不截断。一次总等待最多60秒，超时终止进程。
生成前重新核对宿主与事务准入，等待中撤权/换版/撤销/停用不能追加建议。

## 输出、来源与兼容

真实输出不能从suggestion_fixture指定reason_ids或citations；候选由实际路由和材料要求决定。
当前模型没有定位坐标输出，因此只显示类别，不从原文任意取句充作支持证据。
UI显示“Laya模型建议，未确认；本次使用合成会话”。旧fixture继续标合成模型建议。

保持m5-suggestion-v1追加事件结构；实时binding新增`inference_version=m5-live-laya-v1`、
`model_version`和`model_source=model_inferred_unconfirmed`，生成事件source同后者。
旧fixture不加新字段且source保持synthetic_model；确认仍仅来自yes/correct，报告保持合成开发scope。
渲染与响应继续Q-27服务端到达时间契约，版本字段参与旧展示失效判断。
模型报错只返回固定失败码，保留动作、无用户确认并提供原manual-v1菜单；unknown/no_match单独生成拒识记录。
新none_matched不清空旧确认，常规菜单none_matched保持原清空语义。

## 回滚与退出

切回fixture只适用于合成演示；停用template拒绝在途写入。移除WHYNOTE_TEMPLATE_SYNTHETIC后回到既有本机链路，
不删除事件或改写历史。真实语料、模型质量、本人效用、完整FR/生产不由本切片通过。
开发验证必须包含实际本机模型及登录Open WebUI的合成链路；独立QA、前端构建及来源/兼容/回滚签署分别记录。
