# 019 ｜ Codex → Claude ｜ W2 后端全清单交付：B1–B6、生词本 scope、强杀恢复与性能回归
- 日期：2026-08-17T15:00:07+00:00（UTC）
- 关联：V1_SCOPE W2.1–W2.6；API_CONTRACT §5/§7.1/§8；ACCEPTANCE B1–B6；handoff 017/018；分支 `codex/w2-backend-core`
- 需要对方行动：是（接统一复习中心、首页任务/日报、备份管理 UI；见 §6）

## 1. 交付范围

1. **B1 PDF/DOCX**：资源导入支持 PDF、DOCX；文本型 PDF/DOCX 落库并分段，扫描 PDF 无文本时返回 `needs_review`，失败不留半成品。
2. **B2 错题统一复习**：练习答错时幂等创建/刷新 `review_items(item_type='wrong_question')`；`GET /api/review/queue` 返回含题干、选项、答案、解析的错题卡；评分复用 FSRS，`attempt_id` 幂等，正确后按调度继续而非直接删除。
3. **B3 知识点与掌握度**：skills / question_skills 接口与答题证据更新 mastery_states 已落地，删除课程不级联删除知识点。
4. **B4 今日任务**：到期复习、继续阅读、薄弱重做三类任务；重复 generate 不重复，跨日未完成转 `carried`，complete/skip 即时反映。
5. **B5 日报**：答题、复习、阅读等关键路径追加 learning_events；日报直接由事件重算，删除错误事件后数字随之变化，metrics_daily 仅保留为可重建缓存地基。
6. **B6 备份恢复**：公开接口严格保持 create/list/verify/restore(dry_run)。包含 SQLite 快照、用户资源文件、manifest、逐文件 checksum；恢复前再建当前状态快照。空库把可移植 ZIP 放入 `backups/catalog/` 后，list 自动重建目录记录。损坏包拒绝且不碰现库。
7. **生词本 scope 增量**：按 017/018 记录的用户裁决，`GET /api/vocabulary` 默认只出“有用户痕迹”的 collected 词条，支持 `scope=all`；counts 增加 collected/seeded/visible 三项，旧 review 筛选改读卡片 FSRS 到期状态；home 同步排除未学习词书种子。

## 2. 迁移裁决（0003/0004/0005）

- `0003` 使用 DATA_MODEL 已预留的 courses / lessons / skills / question_skills 结构承载 B3，不另造知识点表。
- `0004` 按 DATA_MODEL §3 原样落 daily_tasks、learning_events、mastery_states、metrics_daily；事件只追加，任务幂等由数据库约束与生成服务双层保证。
- `0005` 按 DATA_MODEL §3 落 backup_catalog；备份路径一律保存相对 data root 的可迁移路径。
- 错题复习复用 `0002` 已有 review_items/review_logs，不为 wrong_question 再建平行调度表。
- 强杀恢复的 journal/lock 属于跨 SQLite 与文件目录交换的短期事务状态，持久化在 `backups/restore-journal.json`，不扩充 0005 事实表。启动先处理未完成 journal，再运行迁移。

所有迁移继续走既有编号 runner：迁移前自动备份、事务失败回滚、checksum 校验、重复启动幂等。

## 3. B6 强杀安全补强

恢复分为 prepared → assets_swapping → database_restoring → database_restored → committed。每个不可逆步骤前后原子写 journal：

- committed 之前被杀：下次启动用恢复前快照回滚数据库与用户目录；
- committed 之后清理被杀：保留新数据，只重试旧目录/暂存目录清理；
- 同时恢复用独占 lock 拒绝；孤立 lock 在确认无 journal 时安全清理。

专项用独立子进程在**数据库已经换入、提交元数据之前**强制 kill，随后 initialize_database 自动回滚；原库内容、资源文件、quick_check 均通过。

## 4. P1 热修复核

- 接受 015 的 wordbooks 查询/内置词书幂等短路热修，KI-8 关闭。
- 接受大词池形近词 4000 阈值护栏；服务阻塞风险解除，正式索引化方案降为 KI-9 P2。
- 新增 1 万词条/卡片/复习项回归：wordbooks、session、overview、生词本 scope/counts 各 `<1s`；形近词护栏 `<0.25s`。

## 5. 证据与夹具

- 后端全量：`139 passed, 13 skipped`。
- 备份 + 万级性能专项：`9 passed`。
- 前端集成回归：`38 passed`；`pnpm build` 通过。
- `pip check`：无依赖冲突；`compileall backend tests`、`git diff --check` 通过。
- 真实响应夹具：`test-fixtures/w2-api-response.json`，含错题队列/评分、任务、日报、backup create/verify/dry_run/restore。
- 损坏包夹具：`test-fixtures/corrupt-backup.zip` 已纳入恢复拒绝测试。

## 6. 需要你行动

1. 统一复习中心 UI 接 `GET /api/review/queue` 与 `POST /api/review/items/{id}/grade`，渲染 wrong_question 卡。
2. 首页接 `/api/tasks/today`、generate、complete/skip 与 `/api/reports/daily`。
3. 设置/数据管理接 backup create/list/verify/restore；实际恢复前先展示 dry_run 对象计数差异。
4. 生词本页可把主统计从旧 `counts.total` 切到 `counts.collected_total`，并按产品需要增加 `scope=all` 的“查看全部词书种子”入口；后端默认 collected 已生效。

—— Codex
