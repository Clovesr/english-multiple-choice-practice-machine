# KNOWN_ISSUES — 已知问题与延后需求（唯一事实来源）

> 五份契约文档之一。等级定义见规划书 16.3：P0 数据丢失/无法启动（必须为 0）、P1 核心闭环中断（必须为 0）、P2 有绕行方式、P3 体验问题。
> 每条格式：等级 ｜ 现象 ｜ 影响与绕行 ｜ 计划。

## 未修问题

### KI-1 ｜ P2 ｜ 词汇复习时间字段时区不一致（存量缺陷）
`vocabulary_entries.next_review_at / last_reviewed_at` 由 `datetime.now()` 写入（本地 naive），而 created_at 等为 UTC。
影响：到期判断在时区边界可能偏差数小时；绕行：无需操作。
计划：迁移 0002 将存量值按本地时区换算为 UTC 接入 FSRS（DATA_MODEL.md §4）；新代码禁止 naive 时间。迁移后本条关闭。

### KI-2 ｜ P2 ｜ 无版本化迁移（存量）
schema 靠启动补丁（`_ensure_column`），无迁移历史、无迁移前备份。
计划：迁移 0001 引入 schema_migrations + 迁移前自动快照；旧补丁逻辑冻结保留。W1 完成。

### KI-3 ｜ P3 ｜ requirements.txt 原 pin lxml==6.0.0 在 Python 3.14 无 Windows wheel
已在 develop-v1 更新为 lxml==6.1.1（85 测试通过）。若 Codex 环境为 Python ≤3.13，6.1.1 同样兼容。

### KI-4 ｜ P3 ｜ 前端无测试框架
计划：W1 由 Claude 引入 vitest + @vue/test-utils，先覆盖 api.ts 封装与阅读器进度逻辑，不追求覆盖率指标。

### KI-5 ｜ P3 ｜ Python 3.14 弃用警告（asyncio.iscoroutinefunction，来自 FastAPI）
影响：仅日志噪音。计划：等 FastAPI 升级；不自行处理。

### KI-6 ｜ P2 ｜ 旧词汇复习接口未接 FSRS（契约 §6 未实现）
`POST /api/vocabulary/{id}/review` 仍执行旧固定间隔调度（again/hard/mastered → +1/3/7 天），写 naive 本地时间到 `vocabulary_entries.next_review_at`；不更新 forward 卡的 review_items，不写 review_logs。
复现：A6.4——对迁移后的词条调用旧接口评 hard → entry.next_review_at=+3天本地时间，卡片 due_at 不变，review_logs 0 行。
影响：旧单词本 UI 评分与 FSRS 状态分叉；继续污染 naive 时间戳。绕行：改用新学习会话接口（前端切换后旧入口弃用）。
主责：Codex（Issue #2 当前批次实现 API_CONTRACT §6 映射）。发现于 2026-08-17 A6 验收。

## 设计约束备忘（不是缺陷）

- FTS5 trigram 对 <3 字符查询走 LIKE 回退（API_CONTRACT.md §3）；数据量到十万级片段后重新评测，必要时引入分词升级，只重建索引不动事实表。
- 存量词汇接入 FSRS 时 stability/difficulty 为近似初始化，首次评分后由算法接管（DATA_MODEL.md §4.3）。
- 错题不做一次性全量回填 review_items，按新错题增量创建，避免迁移日积压雪崩（DATA_MODEL.md §5）。

## 延后需求（V1 冻结后新想法记这里，进 V1.1 评审）

（空）

## 已关闭

（空）
