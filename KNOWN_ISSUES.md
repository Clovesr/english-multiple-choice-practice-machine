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

### KI-8 ｜ 已关闭 ｜ 词书接口在真实数据量下不可用
两处成因叠加使 GET /api/wordbooks 达 100 秒级：①`install_bundled_wordbooks` 在幂等短路检查**之前**无条件调用 `bundled_entries()` 全量物化 5.7 万词条包；②`_state_case()` 的相关 EXISTS 子查询连接方向反转，查询计划从 review_items 的 item_type 前缀全扫（1.9 万行×每个外层词条），实测单本词书计数 20.73s。
热修（Claude 紧急代改 wordbooks.py，handoff/015）：短路检查前置；子查询改为 vc.entry_id 索引驱动的嵌套 EXISTS。实测 20.73s→25.8ms，接口 100s+→0.075s，122 测试全绿。
Codex 已复核收编，并新增 `tests/test_performance_regression.py`：1 万词条/卡片/复习项下 wordbooks、session、overview 均设 `<1s` 回归门槛。关闭于 2026-08-17。

### KI-9 ｜ P2 ｜ 旧形近词计算在大词池下暂由性能护栏降级
旧单词本 list_entries 的 `local_similar_matches` 对全部 vocabulary_entries（词书安装后 6 千+）做纯 Python 编辑距离扫描，多请求并发时占满 GIL，全服务所有接口挂起（py-spy 证据见 handoff/015）。
热修（Claude 紧急代改 vocabulary.py）：词条池超 4000 时暂停本地形近词建议（返回空，列表本身不受影响）。
Codex 已复核收编，并为护栏增加 `<0.25s` 的万级回归门槛；服务阻塞风险已解除。索引化/预计算或与词典词形整合的正式恢复方案延期，按降级功能记 P2。更新于 2026-08-17。

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
