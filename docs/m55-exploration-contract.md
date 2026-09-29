# M5-5 无标签探索契约 v1

2026-09-29；D-21；FR-05/06/09/14/15、TD-02/07/08/09/11/14。
工程实现与开发验证不等于独立验收、理由正确性或生产批准。

## 入口与兼容

新增 `python -m whynote.explore prepare/run/resume/report/view`。默认模式explore、方案C，数量为每源源记录上限，另支持目标上限；all必须显式指定。未知模式拒绝。
prepare无需标签、reviewer或freeze。既有controlled_replay/self_review正式evaluate入口及其校验保持不变。

来源manifest必须固定repo/revision/file/size/SHA256，以及当前使用授权、用途、受控目录、访问者、保留到期、不备份和允许记录范围。单源HOLD单列；不阻塞其他获准源。
max_source_records/max_targets必须为正整数。显式目标数超过授权即拒绝；默认小批也逐源受max_targets限制。all扫描仍受max_source_records约束；全量目标须另有allow_all_targets授权，不用小批例外绕过较小的来源上限。
下载只取三个指定文件；推理前完整hash核对，禁止从源选择另一版本。下载、缓存、临时文件和运行材料仅在配置的项目research根目录内；空间预检预留源文件、索引与结果，拒绝不足空间。

## 统一输入与隔离

`m55-input-v1`按来源、revision、文件摘要、0-based row_id、target_id唯一标识；另有会话规范化group、跨源上下文指纹、state摘要、映射版本、上下文/答案引用、材料标记和自动语言检测版本。
输入仅包含目标之前的system/user/assistant上下文和目标答案。原反馈、自动满意度、另一答案、未来轮次、人工标签在独立受控参考列；不进入worker载荷。关联不唯一则计mapping_failed，不猜测目标。
JSONL逐行有界读取；JSON数组逐对象有界解析。每个对象超过1MiB拒绝并计数；投影不截断。不以编程语言或数据集domain当人工自然语言/任务标签。
prepare的SQLite仅存受控投影与参考；默认JSONL/JSON/HTML仅元数据和引用。worker没有源文件/参考库读权限。查看只绑定loopback，按随机token获取单例，HTML转义、不加载外部资源；暴露登记后才返回正文。

## 常驻执行与状态

固定本机Laya、CUDA/BF16、worker1、batch1，继续700 state token、1024总token、8192 UTF-8字节，tokenizer真实测量。加载和单次请求各自默认60秒，分别计时。
一条请求一个返回；有界IPC，不预读全部数据。进程持全局GPU互斥；AppContainer零网络capability、Job控制整棵进程树；取消、超时后确认回收才释放互斥。worker失效可在下一未开始目标重建，失败目标不自动重试。
unknown/uncaught_error、来源/源码/配置漂移、隔离或持久化故障立即停止；加载失败不伪装策略故障，延续Q-38/Q-39。
每槽有run_id/input_id/scheme/attempt_id；started后恰好一个completed/failed/skipped/interrupted终态，日志逐条fsync。恢复将无终态started记interrupted，结果未知，绝不自动重做；只接未开始槽。
恢复核对输入库/源文件/映射、源码、模型、理由包、预算、运行时和配置指纹；不同指纹拒绝复用run，必须新建。各运行分段单独记录加载/耗时及资源指标。

## 报告与暴露

源记录：扫描=解析失败+映射失败+无目标+保留（并列目标级排除数）；目标/唯一会话/共同来源重复分别计数，近重复未知明确标记。
槽位：计划=建议+合法拒识+技术失败+输入不适用+明确跳过+中断未知+未开始，互斥对账。
按来源、自动自然语言、长度报告分子分母，拒识/材料不足/超限独立列；总时间与所有终态延迟均计入，加载不混入常驻单条p95。记录GPU/RSS起终点及分段峰值；吞吐外推说明范围。
状态仅COMPLETED/PARTIAL/STOPPED，quality=NOT_EVALUATED，正确率、真实理由覆盖、路由遗漏真值和误导率为null/NA，绝不生成质量PASS。
推理前登记组暴露；查看时再登记review曝光。已探索组不能改名为盲评。可按固定seed在推理前保留组；默认不虚构留出。

## 验证与停止点

合成手算分母、反馈隔离、关联异常、长行/有界解析、未知模式、默认C/可选A/B、无标签非60数量、恢复不重复、漂移拒绝、Q-38/Q-39及旧正式入口拒绝缺标签。
真实每源100源记录冒烟；随后每源1000目标或实际可用量，另30分钟合成耐久。新常驻隔离与状态污染/取消回收实测。没有执行的项目继续列待办，独立QA及非作者批准仍须补。
