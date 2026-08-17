# API_CONTRACT — 接口契约（唯一事实来源）

> 五份契约文档之一。规则：**先改本文件，双方确认，再写代码**。后端（Codex）按此实现 `backend/app/routers/`，前端（Claude）按此实现 `frontend/src/api.ts`。已有接口（docs/AUDIT.md §3）保持不变，本文件只记录新增与变更。

## 1. 通用约定（规划书 C.2）

- 前缀 `/api`；JSON UTF-8；时间一律 UTC ISO-8601 带偏移。
- 错误响应统一：`{ "code": "string", "message": "人类可读", "details": {}, "recoverable": true|false }`，HTTP 状态码语义正确（400 参数、404 不存在、409 冲突、422 校验、500 内部）。
- 写接口返回完整对象（含 `id`、`uuid`、`updated_at`）。
- 幂等：导入按 checksum 判重；进度保存可重复调用；复习评分用 `review_log_id` 返回值去重（前端重试时先查后写）。
- 分页：`?limit=&offset=`，响应 `{ "items": [...], "total": n, "limit": l, "offset": o }`。

## 2. 资源库（W1）

### POST /api/resources/import
multipart：`file`（.txt/.md，UTF-8/GBK 自动检测）；或 JSON：`{ "title": "...", "content": "...", "type": "note", "format": "md" }`（粘贴文本）。
可选字段：`title`、`type`、`source`、`source_url`、`author`、`language`。

- 201 → `{ "resource": Resource }`（含 `segment_count`）
- 409 重复（checksum 已存在）→ `{ "code": "duplicate_resource", "details": { "existing_id": n }, ... }`
- 解析失败（如编码无法识别）→ 仍保存原文件，创建 `status='needs_review'` 的资源并 201 返回，`parse_error` 说明原因。**导入永不丢文件。**

`Resource` 对象字段 = DATA_MODEL.md resources 表列（不含内部路径细节，`stored_path` 不返回给前端）。

### GET /api/resources
`?status=&type=&q=&limit=&offset=`。`q` 只匹配标题（全文搜索走 /api/search）。按 `updated_at DESC`。每项附 `progress`: `{ "scroll_ratio": 0.42, "last_opened_at": "..." } | null`。

### GET /api/resources/{id} → 资源详情 + progress。
### PUT /api/resources/{id} body: `{ "title"?, "status"?, "source"?, "author"?, "language"? }`。
### DELETE /api/resources/{id} → 软删除入回收站（trash_entries，resource_type='resource'），204。
### GET /api/resources/{id}/segments `?offset=&limit=`（默认 limit=200，按 sequence 升序）。

### PUT /api/resources/{id}/progress
`{ "last_segment_id"?: n, "scroll_ratio": 0.42, "reading_ms_delta"?: 30000 }` → 200 `{ "progress": {...} }`。幂等；`reading_ms_delta` 累加进 total_reading_ms。

## 3. 搜索（W1）

### GET /api/search
`?q=...&scope=resources&limit=&offset=`（V1 只有 resources scope）。
→ `{ "items": [ { "resource_id", "resource_title", "segment_id", "sequence", "snippet" } ], "total": n }`
`snippet` 为命中片段截取，命中词用 `<mark>` 包裹（后端转义其余 HTML）。≥3 字符走 FTS5 MATCH，<3 字符 LIKE 回退——对调用方透明。

### POST /api/search/rebuild → 重建 FTS 索引，返回 `{ "job": "done", "segments": n }`（W1 同步执行即可）。

## 4. 选词收藏（W1）

### POST /api/vocabulary/from-selection
```json
{
  "term": "resilience",
  "context_sentence": "Her resilience surprised everyone.",
  "context_before": "", "context_after": "",
  "resource_id": 12, "segment_id": 345
}
```
行为：按现有 normalized_term 合并逻辑复用 `services/vocabulary.py`（同词多来源 → 同一词条 + 新 occurrence，encounter_count+1）；occurrence 写入 `resource_id`/`segment_id`；**同时确保存在对应 review_item**（无则建，state='new'）。
→ 201 `{ "entry": VocabularyEntry, "occurrence_id": n, "review_item_id": n, "merged": true|false }`

## 5. 统一复习（W1）

### GET /api/review/queue
`?limit=20&types=vocabulary,wrong_question`
→ `{ "items": [ ReviewCard ], "counts": { "due": n, "overdue": n, "new": n, "done_today": n } }`
`ReviewCard`: `{ "review_item_id", "item_type", "state", "due_at", "payload": {...} }`；
`payload`（vocabulary）= 词条 + 最近 3 条语境；（wrong_question，W2）= 题干 + 选项 + 原错误答案。
排序：逾期最久优先，new 卡按每日新卡上限（默认 20，设置项 `review.new_per_day`）混入。

### POST /api/review/items/{id}/grade
`{ "rating": 1|2|3|4, "duration_ms": 5300 }`
→ 200 `{ "review_item": {...}, "review_log_id": n, "next_due_at": "..." }`
- 404 不存在；409 `already_graded_just_now`（同一 item 60 秒内重复评分 → 返回上次结果，幂等保护）。

### POST /api/review/items/{id}/suspend / unsuspend → 200。
### GET /api/review/stats → `{ "due": n, "overdue": n, "new_available": n, "reviewed_today": n, "retention_30d": 0.87|null }`。

## 6. 旧词汇复习接口的兼容（W1 起）

`POST /api/vocabulary/{entry_id}/review`（旧，评分 again|hard|mastered）：内部改走 FSRS——查该词条的 review_item，映射 again→1、hard→2、mastered→4 后评分；响应结构保持旧格式（entry 序列化，含 next_review_at=FSRS due_at）。前端 VocabularyView 改用新接口后，此接口标记 deprecated，保留到稳定版。

## 7. W2 接口（先定形，细节实现前可修订）

- `GET /api/tasks/today` → `{ "date": "2026-08-24", "items": [ DailyTask ] }`；`POST /api/tasks/generate` 幂等重发不重复；`PUT /api/tasks/{id}/complete|skip`。
- `GET /api/reports/daily?date=` → `{ "study_ms", "reviews_done", "review_accuracy", "new_words", "questions_answered", "question_accuracy" }`（由 learning_events 计算）。
- `POST /api/backup/create` `{ "kind": "manual" }` → BackupEntry；`GET /api/backup/list`；`POST /api/backup/{id}/verify`；`POST /api/backup/{id}/restore` `{ "dry_run": true|false }`，dry_run 返回恢复预览（对象计数对比）。恢复前自动为当前库建快照。
- `POST /api/questions/{id}/skills` `{ "skill_ids": [..] }`；`GET /api/skills?type=&stage=`；`POST /api/skills`。
- 错题评分后自动建/更新 wrong_question review_item（无独立接口，practice 提交流程内部触发）。

## 8. 前端接入约定

- `frontend/src/api.ts` 是唯一请求出口，新增函数按本文件命名：`importResource`、`listResources`、`getResourceSegments`、`saveProgress`、`searchAll`、`collectWordFromSelection`、`getReviewQueue`、`gradeReviewItem` 等。
- 新页面路由：`/resources`（资源库列表+导入）、`/resources/:id/read`（阅读器）、`/review`（复习中心）。VocabularyView 复习入口改指向 /review?types=vocabulary。
- 所有新页面必须实现规划书 12.4 的六种状态：加载、空数据（说明原因+下一步）、错误（保留输入+重试）、离线降级、未保存提示、危险操作确认。
