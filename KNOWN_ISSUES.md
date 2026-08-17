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

## 设计约束备忘（不是缺陷）

- FTS5 trigram 对 <3 字符查询走 LIKE 回退（API_CONTRACT.md §3）；数据量到十万级片段后重新评测，必要时引入分词升级，只重建索引不动事实表。
- 存量词汇接入 FSRS 时 stability/difficulty 为近似初始化，首次评分后由算法接管（DATA_MODEL.md §4.3）。
- 错题不做一次性全量回填 review_items，按新错题增量创建，避免迁移日积压雪崩（DATA_MODEL.md §5）。

## 延后需求（V1 冻结后新想法记这里，进 V1.1 评审）

（空）

## 已关闭

### KI-6 ｜ P2 ｜ 旧词汇复习接口未接 FSRS
已于 2026-08-17 修复：`POST /api/vocabulary/{id}/review` 现定位或补建正向卡，按 again→1、hard→2、mastered→4 进入与新学习会话相同的 FSRS 评分核心；更新 UTC 带偏移的词条兼容字段并写入 `review_logs`，不再写旧固定间隔或 naive 时间。构造旧库与 `test-fixtures/existing-user-database.sqlite` A6.4 仿真库均有回归覆盖。

### KI-7 ｜ P3 ｜ SPA 兜底路由吞掉未实现的 /api 路径
已于 2026-08-17 修复：`main.py` 在 SPA 兜底之前注册 `/api` 与 `/api/{path}` 契约 404，返回 `{code,message,details,recoverable}` JSON；未知 study 路径与 `/api` 根路径均有回归覆盖。前端 content-type 防御继续保留用于兼容旧版本。
