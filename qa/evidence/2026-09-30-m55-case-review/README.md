# 首轮三源案例审阅：元数据证据

2026-09-30，历史run 7a6af87b202340cdb6e96a14280476af；审阅checkout6004ee6。没有新推理、参数修改或质量验收。

- selection.json：每源每桶4目标，固定哈希3例＋定向边界/理由1例，48目标/48组。
- case-notes.json：逐例归纳与引用；仅ID/结构/说明，无案例正文。助手探索审阅不充当本人签署或gold。
- aggregate.json：3000槽、956预测及各候选阶段计数；不是模型质量分数。
- length-summary.json：1960超限＋84跳过本地token计量；数值可容纳不等于语义完整或实际覆盖提升。
- review-receipt.json：追加暴露48 case_view＋2044 review_metadata；后者不计入逐例语义审阅。
- 三个py.txt：抽样、计量与摘要脚本。原文输出仅落在项目research受控目录，原保留期不变。重复运行extract会因目录已存在而拒绝；计量重跑会新增暴露记录。
- feishu-prior-blocks.json / feishu-writes.json：只更新两份文档M5-5d状态块，589→590，读回/历史/链接核验通过。

原文与脱敏工作材料仅保存在D:/PythonProject/jev项目/var/research/m55/review-20260930-first，沿用来源到期；不复制到Git、飞书或另行备份。
