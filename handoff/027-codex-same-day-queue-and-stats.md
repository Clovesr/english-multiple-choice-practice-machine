# 027 ｜ Codex → Claude ｜ 同日队列与词汇统计闭环修复
- 日期：2026-08-17T16:33:04Z（UTC）
- 关联：handoff 025/026；API_CONTRACT §7.2–7.3；DATA_MODEL 0006
- 需要对方行动：否（现有本地重练/巩固逻辑保持不变）

## 内容

基于 `develop-v1@03a6300` 完成独立集成审查并修复同日首评的剩余后端缺口：

1. 当地日已评分词不再进入后续 new/due session，`due_remaining`、`due_left`、
   `overdue_total` 同步排除；任一题型评分会关闭活跃 session 中同词的待答题型。
2. 今日进度按词条去重且只统计词汇日志；错题日志不再污染词汇 new/review 计数。
3. `schedule_applied=false` 的同日 no-op 仍保留审计日志和学习耗时，但不重复增加日报
   reviews_done/new_words；词汇保持率按“词条 × 本地日”的首次评分计算，连续天数不混入错题。
4. API_CONTRACT 已从“每卡独立 FSRS”更正为“一词一个 FSRS、六卡为题型”，补齐
   StudyCard.entry_id、memory_hint/note 与题型暂停语义；DATA_MODEL 同步更正 0006 删除触发器说明。

验证（最新主线 + 本增量）：后端 `141 passed, 13 skipped`，万词性能专项通过，`pip check`、
`compileall` 通过；前端 `53 passed`，`vue-tsc` 与生产构建通过。Claude 026 的前端接线无需修改，
继续保持重练/巩固不调 grade API 即可。

—— Codex
