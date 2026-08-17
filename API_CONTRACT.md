# API_CONTRACT — 接口契约（唯一事实来源）

> 五份契约文档之一。规则：**先改本文件，双方确认，再写代码**。后端（Codex）按此实现 `backend/app/routers/`，前端（Claude）按此实现 `frontend/src/api.ts`。已有接口（docs/AUDIT.md §3）保持不变，本文件只记录新增与变更。

## 1. 通用约定（规划书 C.2）

- 前缀 `/api`；JSON UTF-8；时间一律 UTC ISO-8601 带偏移。
- 错误响应统一：`{ "code": "string", "message": "人类可读", "details": {}, "recoverable": true|false }`，HTTP 状态码语义正确（400 参数、404 不存在、409 冲突、422 校验、500 内部）。
- 写接口返回完整对象（含 `id`、`uuid`、`updated_at`）。
- 幂等：导入按 checksum 判重；进度保存可重复调用；**一切作答/评分类写接口以客户端生成的 `attempt_id`（UUID）为幂等键**——同键重试必须返回首次结果，不产生第二条日志（handoff 004 §3.5 定案，取代早期"60 秒窗口判重"方案）。
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
行为：按现有 normalized_term 合并逻辑复用 `services/vocabulary.py`（同词多来源 → 同一词条 + 新 occurrence，encounter_count+1）；occurrence 写入 `resource_id`/`segment_id`；词条数据**优先从离线词典补全**（音标、词义、词形；模型翻译队列降级为可选补充）；随后按数据充分性生成卡片（至少正向卡，语境齐备时含挖空卡），卡片进入学习队列。
→ 201 `{ "entry": VocabularyEntry, "occurrence_id": n, "cards": [{ "card_id", "card_type", "review_item_id" }], "enriched_from_dictionary": true|false, "merged": true|false }`

## 5. 统一复习中心（W1 基础 / W2 错题接入）

**队列分工（004 对齐后）**：词汇卡的日常学习与复习走 §7 的 `/api/study/session`；复习中心队列服务**非卡片类复习项**（V1 即 `wrong_question`，W2 接入）。复习中心的计数聚合所有类型（含词汇卡到期数），词汇部分以入口跳转到学习会话，不在复习中心重复出卡。

### GET /api/review/queue
`?limit=20&types=wrong_question`
→ `{ "items": [ ReviewCard ], "counts": { "due": n, "overdue": n, "new": n, "done_today": n, "vocab_due": n } }`
`ReviewCard`: `{ "review_item_id", "item_type", "state", "due_at", "payload": {...} }`；
`payload`（wrong_question，W2）= 题干 + 选项 + 原错误答案。
排序：逾期最久优先。

### POST /api/review/items/{id}/grade
`{ "attempt_id": "uuid", "rating": 1|2|3|4, "duration_ms": 5300 }`
→ 200 `{ "review_item": {...}, "review_log_id": n, "next_due_at": "...", "attempt_id": "uuid" }`
- 仅用于非卡片类 review_item；词汇卡评分必须走 §7 的 card 端点（禁止按词条/复习项直评六类卡）。
- 404 不存在；同 `attempt_id` 重试 → 返回首次结果（幂等）。

### POST /api/review/items/{id}/suspend / unsuspend → 200。
### GET /api/review/stats → `{ "due": n, "overdue": n, "new_available": n, "reviewed_today": n, "retention_30d": 0.87|null }`（聚合全部类型）。

## 6. 旧词汇复习接口的兼容（W1 起）

`POST /api/vocabulary/{entry_id}/review`（旧，评分 again|hard|mastered）：内部改走 FSRS——定位该词条的**正向卡**（无则创建），映射 again→1、hard→2、mastered→4 后按 §7 评分核心处理；响应结构保持旧格式（entry 序列化，含 next_review_at=正向卡 FSRS due_at）。前端改用新接口后，此接口标记 deprecated，保留到稳定版。

`GET /api/vocabulary?scope=collected|all&status=&search=`：默认 `collected`，只返回有用户痕迹的生词（遇见/收藏、用户编辑、重点标记或任一卡已复习）；`all` 才包含尚未学习的词书灌入词。`counts` 保留 `total` 并新增 `collected_total`、`seeded_total`、`visible_total`；`status=review` 以 `review_items` 的 FSRS 到期状态为准，不再读取旧固定间隔字段。

## 7. 词汇学习（V1 冻结线，handoff 003 §4 提案 + 004 §5 修订对齐后的定稿）

数据模型见 DATA_MODEL.md 0002 修订（Codex 维护）。核心原则：`vocabulary_cards` 是学习单元，每卡独立 FSRS；后端是判分最终权威；服务端展开容错规则，前端只做即时反馈。

### 7.1 词书与词典

- `GET /api/wordbooks` → `{ "items": [{ "id", "uuid", "name", "kind": "builtin|imported", "source_tag", "total", "state_counts": { "new", "learning", "known", "ignored", "paused" }, "active_plan": {...}|null }] }`。内置词书（CET4/CET6/考研）随发布包离线可用。
- `POST /api/wordbooks/import`：multipart（txt/csv，一行一词或 词[Tab]释义）或 JSON `{ "name", "terms": [...] }` → `{ "wordbook": {...}, "matched": n, "unmatched": [{ "term", "status": "needs_enrichment" }] }`。未匹配词入库为最小词条并标记 `needs_enrichment`，**只生成数据充分的卡片**，不生成空白 reverse/spelling/cloze/collocation 卡。
- `POST /api/wordbooks/{id}/plan` `{ "daily_new": 20, "new_order": "frequency|sequence|random" }` → 激活/更新学习计划；`DELETE /api/wordbooks/{id}/plan` 停用。同一时刻仅一个 active 主计划；切换计划不删除已有卡片与进度。
- `GET /api/wordbooks/{id}/entries?offset=&limit=&state=`
- `GET /api/dictionary/lookup?term=` → `{ "found", "entry": { "lemma", "phonetic_uk", "phonetic_us", "pos_senses": [{ "pos", "gloss_zh", "gloss_en" }], "forms": [{ "kind", "text" }], "relations": [...], "tags": [...], "frequency_rank" } }`。阅读器选词浮层同源调用；离线可用。
- `PUT /api/vocabulary/entries/{id}/state` `{ "study_status": "known|learning|ignored|paused|focus" }`。词条级状态控制其全部卡片是否入队；恢复词条不得清除用户单独暂停的卡片。
- `GET /api/vocabulary?status=&search=&scope=collected|all&limit=&offset=`：默认 `scope=collected`、`limit=120`、`offset=0`，只返回“有用户痕迹”的生词本词条；`scope=all` 用于包含尚未学习的词书种子词。`collected` 判定为 `encounter_count>0 OR source_kind='user' OR user_edited=1 OR manually_frequent=1 OR EXISTS(该词条任一卡片 reps>0)`。响应遵循 §1 分页外壳 `{ items, total, limit, offset }`；`counts` 不受分页影响，保留全库 `total`，并增加 `collected_total`、`seeded_total`、`visible_total`。`status=review` 以 vocabulary_card 对应 review_item 的 FSRS `due_at` 为准，不再读取旧 `next_review_at`。`GET /api/vocabulary/home` 同样排除未学习种子词。（用户裁决见 handoff 017/018；分页升级见 handoff 019 Claude。）

### 7.2 学习会话

- `GET /api/study/session?limit=` → `{ "session_id": "uuid", "counts": { "new_remaining", "due_remaining", "done_today" }, "cards": [ StudyCard ] }`。同一 `session_id` 内刷新/重试保持同一批卡与每日上限口径。到期优先，新卡按 `daily_new` 混入。
- `StudyCard` 统一载荷：
```json
{
  "card_id": 1, "review_item_id": 2, "entry_id": 3, "card_type": "forward|reverse|listening|spelling|cloze|collocation",
  "state": "new|learning|review|relearning",
  "entry": { "lemma": "...", "phonetic_uk": "...", "phonetic_us": "...", "memory_hint": "...", "note": "...", "senses": [...] },
  "prompt": { "text": "...", "tts_text": "...", "cloze_sentence": "...", "pairs": [...] },
  "answer": { "text": "...", "accept": ["..."], "distractors": [...] },
  "contexts": [{ "sentence": "...", "source": "..." }]
}
```
  `answer.accept` 由**后端按统一容错规则展开**（大小写、Unicode 规范化、首尾/重复空格、美英拼写变体）；前端仅做规范化比对给即时反馈。
- `POST /api/study/cards/{card_id}/grade` `{ "attempt_id": "uuid", "rating": 1|2|3|4, "answer_given"?: "...", "duration_ms" }` → `{ "card_id", "review_item": {...}, "review_log_id", "next_due_at", "auto_correct": true|false|null, "final_rating", "attempt_id" }`。服务端对 `answer_given` 复判并记录 `auto_correct`；客观卡默认 错→1 / 对→3，用户可改评（自动判定与最终评分同时入日志）。同 `attempt_id` 重试返回首次结果。
- `POST /api/study/cards/{card_id}/suspend|unsuspend`（人工暂停位独立于词条状态）。

### 7.3 设置、统计、积压与冲刺

- `GET/PUT /api/study/settings` → `{ "daily_new", "daily_review_max", "enabled_card_types": [...], "new_card_order", "leech_threshold", "backlog_mode" }`（V1 仅全局设置）。
- `GET /api/study/overview` → `{ "today": { "new_done", "new_target", "reviews_done", "due_left" }, "overdue_total", "streak_days", "retention_7d", "retention_30d", "forecast_7d": [{ "date", "due" }], "leeches": n }`。首页与词汇页共用。
- `POST /api/study/backlog/plan` `{ "mode": "spread|suspend_new|focus_overdue", "days"?: n }` → 处理方案预览 + 应用。
- `POST /api/study/sprint` `{ "exam_date", "wordbook_id" }` → 预览（每日需新学/复习量、可行性提示）+ 激活；`GET /api/study/sprint`；`DELETE /api/study/sprint` 退出冲刺回常规计划。冲刺是 study_plans 的 mode=sprint 变体。

### 7.4 发音

前端优先使用浏览器 SpeechSynthesis 本地语音（**检测可用的 en-US/en-GB 本地 voice**，不假设必然存在）；无本地 voice 时切换后端 Windows 本地语音 Provider（Codex 评估 SAPI/System.Speech 最小实现），设置页展示当前发音能力状态。听音卡验收必须在真实断网环境播放通过。

## 8. W2 接口（先定形，细节实现前可修订）

- `GET /api/tasks/today` → `{ "date": "2026-08-24", "items": [ DailyTask ] }`；`POST /api/tasks/generate` 幂等重发不重复；`PUT /api/tasks/{id}/complete|skip`。
- `GET /api/reports/daily?date=` → `{ "study_ms", "reviews_done", "review_accuracy", "new_words", "questions_answered", "question_accuracy" }`（由 learning_events 计算）。
- `POST /api/backup/create` `{ "kind": "manual" }` → BackupEntry；`GET /api/backup/list`；`POST /api/backup/{id}/verify`；`POST /api/backup/{id}/restore` `{ "dry_run": true|false }`，dry_run 返回恢复预览（对象计数对比）。恢复前自动为当前库建快照。
- `POST /api/questions/{id}/skills` `{ "skill_ids": [..] }`；`GET /api/skills?type=&stage=`；`POST /api/skills`。
- 错题评分后自动建/更新 wrong_question review_item（无独立接口，practice 提交流程内部触发）。

## 9. 前端接入约定

- `frontend/src/api.ts` 是唯一请求出口，新增函数按本文件命名：`importResource`、`listResources`、`getResourceSegments`、`saveProgress`、`searchAll`、`collectWordFromSelection`、`getStudySession`、`gradeStudyCard`、`listWordbooks`、`lookupDictionary`、`getReviewQueue`、`gradeReviewItem` 等。
- 新页面路由：`/resources`（资源库列表+导入）、`/resources/:id/read`（阅读器）、`/study`（词汇学习会话）、`/wordbooks`（词书与计划）、`/review`（复习中心，W2 错题接入）。VocabularyView 的复习入口改指向 /study。
- 所有新页面必须实现规划书 12.4 的六种状态：加载、空数据（说明原因+下一步）、错误（保留输入+重试）、离线降级、未保存提示、危险操作确认。
