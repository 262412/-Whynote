# M5-3b 实时Laya的合成确认契约

需求FR-01/06/09/10/11/13/14、TD-02/04/07/09/10；D-19。基线main5abdc580，读取PRD406/技术401。

## 准入与数据

原mock研究、suggestion、template开关继续有效。新增`suggestion_backend`默认`fixture`；
只有显式`laya_local`进入实时路径，要求固定模型revision、绝对Python/权重目录，
以及服务端`suggestion_synthetic_targets`中的对象ID和版本白名单。登记来源须scripted。
先检查主体、版本、登记及白名单，再读取直接父问题与目标回答。没有私人聊天自动抓取或真实研究开关。

点踩动作/Outbox先提交。实时预测在独立本地进程执行，拦截Python TCP connect/connect_ex；环境级出站隔离尚未验收。原文经stdin短期传递，
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
Q-30修复：除建议/展示ID、候选集合、理由、结果及presentation这些生成快照字段外，
生成时与当前准入绑定的全部字段须对称相等，包含字段的存在性；当前后端与生成时后端不一致时（live与fixture任一方向），
旧建议的渲染、响应和直接库回执重试均拒绝且零追加。旧fixture无live版本字段仍按原契约合法，
无需迁移或回填；已记录确认及生成历史保持原样，回滚后可用新ID生成fixture建议。
既有Action同点击重放只返回此前结果、不追加回执，继续保留。Q-27到达时间不绕过最新版本复核。
模型报错只返回固定失败码，保留动作、无用户确认并提供原manual-v1菜单；unknown/no_match单独生成拒识记录。
新none_matched不清空旧确认，常规菜单none_matched保持原清空语义。

## 回滚与退出

切回fixture只适用于合成演示；停用template拒绝在途写入。移除WHYNOTE_TEMPLATE_SYNTHETIC后回到既有本机链路，
不删除事件或改写历史。真实语料、模型质量、本人效用、完整FR/生产不由本切片通过。
开发验证必须包含实际本机模型及登录Open WebUI的合成链路；独立QA、前端构建及来源/兼容/回滚签署分别记录。

## 宿主验证工具的预检与构建记录（Q-28/Q-29修复）

provision 的 model-python/model-dir 在任何宿主API或SQLite写入前检查必填及路径类型；
CLI缺项返回参数错误，直接函数调用也拒绝。配置错误修正后可在同一未修改的新实例重试。
不自动清理或重置此前已产生副作用的失败实例。

M5-3宿主新增build命令：先核对完整补丁栈和客户端，要求Node22，执行npm run build；
仅构建成功且前后源码指纹相同才写build/.whynote-m53-build.json。
记录Git跟踪及非忽略新增文件的内容指纹，以及build下全部产物的相对路径和SHA-256；
启动时重验源码与每个产物，缺失/旧/篡改/新增bundle均拒绝，且不创建data-dir或启动宿主。
失败构建撤销旧构建记录；无给旧目录直接补签记录的命令。旧构建需重新运行build。
记录面向可信本地操作者的复现一致性，不能抵抗拥有本机写权限者同时伪造源码、产物与记录；
不将它称为独立安全签署。业务事件、Q-27时间契约和生产开关均不改变。
