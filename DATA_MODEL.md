# DATA_MODEL — 数据模型与迁移（唯一事实来源）

> 五份契约文档之一。任何表结构变更：先改本文件 → 集成确认 → Codex 写迁移。Claude 不直接改表。

## 1. 总原则

- **旧表一律保留不改语义**（papers、units、questions、options、practice_*、wrong_stats、vocabulary_*、import_jobs、trash_entries、ai_* 等 24 张，见 docs/AUDIT.md §2）。新能力用新表 + 关联表接入。允许对旧表做**纯增列**（nullable 或带默认值）。
- **版本化迁移**：新建 `schema_migrations` 表；迁移脚本按序号只前进；每次启动检查未应用迁移，**应用前自动备份数据库文件**到 `backend/data/backups/pre-migration/`；失败回滚事务并保留备份。现有 `_ensure_column` 补丁逻辑冻结（保留以兼容极旧库，不再往里加东西）。
- **时间戳规范（新表强制）**：UTC，ISO-8601 带显式偏移，如 `2026-08-17T09:30:00+00:00`。禁止 naive `datetime.now()`。旧表的本地时间字段在迁移时显式换算（见 §4）。
- **新核心对象双 ID**：`id INTEGER PRIMARY KEY AUTOINCREMENT`（内部关联）+ `uuid TEXT NOT NULL UNIQUE`（导出/迁移/合并用稳定身份）。
- 软删除沿用现有约定：`deleted_at TEXT` + trash_entries 登记。
- 打开连接即 `PRAGMA foreign_keys=ON`；迁移 0001 起启用 `PRAGMA journal_mode=WAL`。

## 2. 迁移编号

| 迁移 | 内容 | 里程碑 |
|---|---|---|
| 0001 | schema_migrations、WAL、resources、resource_segments、resource_progress、resource_segments_fts（FTS5）、vocabulary_occurrences 增列 | W1 |
| 0002 | 结构化词汇、词书/计划、六类卡片、学习会话、review_items/review_logs、词汇存量接入 FSRS（§4） | W1 |
| 0003 | courses、lessons、skills、skill_dependencies、lesson_skills、question_skills | W1 定稿 / W2 使用 |
| 0004 | daily_tasks、learning_events、mastery_states、metrics_daily | W2 |
| 0005 | backup_catalog | W2 |
| 0006 | 词级 FSRS 调度、题型暂停映射、旧卡级状态无损归档 | V1 用户裁决修正 |

## 3. 新表定义

### 0001 资源库

```sql
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,          -- 0001, 0002, ...
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    checksum TEXT NOT NULL DEFAULT ''     -- 迁移脚本内容哈希
);

CREATE TABLE resources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    type TEXT NOT NULL,                   -- article | book | note | paper_text | other
    language TEXT NOT NULL DEFAULT 'en',
    format TEXT NOT NULL,                 -- txt | md | pdf | docx（W1 只实现 txt/md）
    status TEXT NOT NULL DEFAULT 'inbox', -- inbox | active | archived | needs_review
    source TEXT NOT NULL DEFAULT '',      -- 人类可读来源描述
    source_url TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL DEFAULT '',
    license TEXT NOT NULL DEFAULT '',
    private_only INTEGER NOT NULL DEFAULT 1,
    checksum TEXT NOT NULL,               -- 原始文件/文本 sha256，用于去重
    original_filename TEXT NOT NULL DEFAULT '',
    stored_path TEXT NOT NULL DEFAULT '', -- data/resources/{uuid}/original.<ext>；纯文本粘贴也落盘
    media_type TEXT NOT NULL DEFAULT '',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    segment_count INTEGER NOT NULL DEFAULT 0,
    parser_name TEXT NOT NULL DEFAULT '',
    parser_version INTEGER NOT NULL DEFAULT 1,
    parse_error TEXT NOT NULL DEFAULT '', -- 解析失败原因；失败时 status=needs_review 且原文件已保存
    imported_at TEXT NOT NULL,
    deleted_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_resources_checksum ON resources(checksum) WHERE deleted_at IS NULL;
CREATE INDEX idx_resources_list ON resources(status, deleted_at, updated_at DESC);

CREATE TABLE resource_segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resource_id INTEGER NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,            -- 从 1 递增
    kind TEXT NOT NULL DEFAULT 'paragraph', -- paragraph | heading
    heading_level INTEGER NOT NULL DEFAULT 0,
    content TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    UNIQUE (resource_id, sequence)
);

CREATE TABLE resource_progress (
    resource_id INTEGER PRIMARY KEY REFERENCES resources(id) ON DELETE CASCADE,
    last_segment_id INTEGER,
    scroll_ratio REAL NOT NULL DEFAULT 0, -- 0~1
    total_reading_ms INTEGER NOT NULL DEFAULT 0,
    opened_count INTEGER NOT NULL DEFAULT 0,
    last_opened_at TEXT,
    updated_at TEXT NOT NULL
);

-- 全文搜索：external-content FTS5，trigram 分词（中英文混排均可子串匹配）
CREATE VIRTUAL TABLE resource_segments_fts USING fts5(
    content,
    content='resource_segments', content_rowid='id',
    tokenize='trigram'
);
-- 由触发器同步 INSERT/UPDATE/DELETE；索引可随时重建（rebuild 命令）。
-- 约定：查询词 >= 3 字符走 MATCH；< 3 字符（如二字中文词）回退 LIKE 扫描，W1 数据量可接受。
-- 后续里程碑若引入分词升级（jieba/ICU），只重建 FTS，不动事实表。

-- 旧表纯增列：词汇语境可指向资源
ALTER TABLE vocabulary_occurrences ADD COLUMN resource_id INTEGER REFERENCES resources(id) ON DELETE SET NULL;
ALTER TABLE vocabulary_occurrences ADD COLUMN segment_id INTEGER REFERENCES resource_segments(id) ON DELETE SET NULL;
```

### 0002 词汇知识、卡片与统一复习（FSRS）

旧 `vocabulary_entries` 保留原字段与语义，只纯增列：

```sql
ALTER TABLE vocabulary_entries ADD COLUMN uuid TEXT;
ALTER TABLE vocabulary_entries ADD COLUMN dictionary_key TEXT NOT NULL DEFAULT '';
ALTER TABLE vocabulary_entries ADD COLUMN phonetic_uk TEXT NOT NULL DEFAULT '';
ALTER TABLE vocabulary_entries ADD COLUMN phonetic_us TEXT NOT NULL DEFAULT '';
ALTER TABLE vocabulary_entries ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'user';
ALTER TABLE vocabulary_entries ADD COLUMN enrichment_status TEXT NOT NULL DEFAULT 'needs_enrichment';
ALTER TABLE vocabulary_entries ADD COLUMN deleted_at TEXT;
CREATE UNIQUE INDEX idx_vocabulary_entries_uuid
    ON vocabulary_entries(uuid) WHERE uuid IS NOT NULL;
```

SQLite 不能用 `ALTER TABLE` 给存量表新增 `NOT NULL UNIQUE` 列，因此迁移先增 nullable
`uuid`、同事务回填全部旧行并建 partial unique index；之后所有写入口必须写 UUID。物理约束与
“旧表不重建”的兼容原则以此折中。`enrichment_status` 为 `ready | needs_enrichment | failed`，
不得复用旧 `translation_status`（后者仍只描述可选模型翻译队列）。

结构化词义、词形与关系：

```sql
CREATE TABLE vocabulary_senses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    entry_id INTEGER NOT NULL REFERENCES vocabulary_entries(id) ON DELETE CASCADE,
    pos TEXT NOT NULL DEFAULT '',
    gloss_zh TEXT NOT NULL DEFAULT '',
    gloss_en TEXT NOT NULL DEFAULT '',
    sequence INTEGER NOT NULL DEFAULT 1,
    source TEXT NOT NULL DEFAULT 'user',
    source_ref TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE (entry_id, source, source_ref, sequence)
);
CREATE TABLE vocabulary_forms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL REFERENCES vocabulary_entries(id) ON DELETE CASCADE,
    form_type TEXT NOT NULL, form_text TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'user',
    UNIQUE (entry_id, form_type, form_text)
);
CREATE TABLE vocabulary_relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL REFERENCES vocabulary_entries(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL,           -- family/synonym/antonym/similar/phrasal_verb/collocation
    related_term TEXT NOT NULL,
    related_entry_id INTEGER REFERENCES vocabulary_entries(id) ON DELETE SET NULL,
    note TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'user',
    UNIQUE (entry_id, relation_type, related_term, source)
);
```

自动导入不得覆盖 `source='user'` 的人工事实。旧摘要字段继续供兼容 UI 使用；新接口以子表为
结构化事实来源。

词书、计划和全局设置：

```sql
CREATE TABLE wordbooks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, uuid TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL, kind TEXT NOT NULL, -- builtin | imported
    source_tag TEXT NOT NULL DEFAULT '', source_name TEXT NOT NULL DEFAULT '',
    source_version TEXT NOT NULL DEFAULT '', license TEXT NOT NULL DEFAULT '',
    checksum TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE wordbook_entries (
    wordbook_id INTEGER NOT NULL REFERENCES wordbooks(id) ON DELETE CASCADE,
    entry_id INTEGER NOT NULL REFERENCES vocabulary_entries(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL DEFAULT 0, frequency_rank INTEGER,
    added_at TEXT NOT NULL, PRIMARY KEY (wordbook_id, entry_id)
);
CREATE TABLE study_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT, uuid TEXT NOT NULL UNIQUE,
    wordbook_id INTEGER NOT NULL REFERENCES wordbooks(id) ON DELETE CASCADE,
    mode TEXT NOT NULL DEFAULT 'normal',  -- normal | sprint
    daily_new INTEGER NOT NULL DEFAULT 20,
    new_order TEXT NOT NULL DEFAULT 'frequency', -- frequency | sequence | random
    exam_date TEXT, active INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_study_plans_one_active ON study_plans(active) WHERE active = 1;
CREATE TABLE study_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    daily_new INTEGER NOT NULL DEFAULT 20,
    daily_review_max INTEGER NOT NULL DEFAULT 200,
    enabled_card_types TEXT NOT NULL,     -- JSON：六类卡开关
    new_card_order TEXT NOT NULL DEFAULT 'frequency',
    leech_threshold INTEGER NOT NULL DEFAULT 8,
    backlog_mode TEXT NOT NULL DEFAULT 'spread',
    updated_at TEXT NOT NULL
);
```

`vocabulary_cards` 保存六类题型及语境/搭配变体。0002 初始版本曾把每张卡作为独立调度单元；
该语义已被 0006 的用户裁决修正取代：**卡片只标识呈现题型与内容快照，FSRS 调度单位是词条**。

```sql
CREATE TABLE vocabulary_cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT, uuid TEXT NOT NULL UNIQUE,
    entry_id INTEGER NOT NULL REFERENCES vocabulary_entries(id) ON DELETE CASCADE,
    card_type TEXT NOT NULL,              -- forward/reverse/listening/spelling/cloze/collocation
    variant_key TEXT NOT NULL,
    generation_source TEXT NOT NULL DEFAULT '',
    prompt_data TEXT NOT NULL DEFAULT '{}', answer_data TEXT NOT NULL DEFAULT '{}',
    content_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE (entry_id, card_type, variant_key)
);
```

`prompt_data/answer_data` 是版本化快照，原始事实仍在词汇子表。仅在数据充分时生成卡片；
词典外词可入库为 `needs_enrichment`，但不得产生空白 reverse/spelling/cloze/collocation 卡。
0002 建表时人工暂停位位于对应 `review_items.manually_suspended`；0006 将旧值迁到
`vocabulary_card_type_settings`，最终语义为“该词条的该题型不参与轮换”。

统一复习状态与只追加日志：

```sql
CREATE TABLE review_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    item_type TEXT NOT NULL,              -- vocabulary | vocabulary_card(仅归档历史) | wrong_question
    ref_id INTEGER NOT NULL,              -- vocabulary → vocabulary_entries.id
    state TEXT NOT NULL DEFAULT 'new',    -- new | learning | review | relearning
    step INTEGER NOT NULL DEFAULT 0,
    due_at TEXT NOT NULL,
    last_review_at TEXT,
    stability REAL NOT NULL DEFAULT 0,
    difficulty REAL NOT NULL DEFAULT 0,
    scheduled_days REAL NOT NULL DEFAULT 0,
    elapsed_days REAL NOT NULL DEFAULT 0,
    reps INTEGER NOT NULL DEFAULT 0,
    lapses INTEGER NOT NULL DEFAULT 0,
    manually_suspended INTEGER NOT NULL DEFAULT 0,
    scheduler TEXT NOT NULL DEFAULT 'fsrs',
    scheduler_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (item_type, ref_id)
);
CREATE INDEX idx_review_due ON review_items(manually_suspended, due_at);

CREATE TABLE review_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    attempt_id TEXT NOT NULL UNIQUE,      -- 客户端 UUID；真正的写幂等键
    review_item_id INTEGER NOT NULL REFERENCES review_items(id) ON DELETE CASCADE,
    card_id INTEGER REFERENCES vocabulary_cards(id) ON DELETE SET NULL,
    answer_given TEXT,
    auto_correct INTEGER,                 -- NULL=主观卡；0/1=后端客观复判
    auto_rating INTEGER,                  -- 后端建议 1 Again / 3 Good
    final_rating INTEGER NOT NULL,        -- 用户最终 1 Again | 2 Hard | 3 Good | 4 Easy
    state_before TEXT NOT NULL,
    state_after TEXT NOT NULL,
    step_before INTEGER NOT NULL,
    step_after INTEGER NOT NULL,
    due_before TEXT NOT NULL,
    due_after TEXT NOT NULL,
    stability_before REAL NOT NULL,
    stability_after REAL NOT NULL,
    difficulty_before REAL NOT NULL,
    difficulty_after REAL NOT NULL,
    elapsed_days REAL NOT NULL DEFAULT 0,
    scheduled_days REAL NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    reviewed_at TEXT NOT NULL
);
CREATE INDEX idx_review_logs_item ON review_logs(review_item_id, reviewed_at);
```

`review_items.ref_id` 是多态关联，SQLite 无法直接声明到两张目标表的外键；迁移创建
`vocabulary_cards_review_item_delete` 触发器，在卡片被物理删除时同步删除其复习项，
`review_logs` 再通过外键级联删除，避免旧词条硬删除留下孤儿调度数据。

FSRS 引擎使用 **py-fsrs（MIT）**默认参数起步；0006 后每个有卡片的词条只有一个活跃
`item_type='vocabulary'` 复习项。`review_logs.card_id` 继续记录当次实际题型，保全量前后状态，
未来参数升级可从日志重算，不覆写历史。客观卡的 `answer.accept` 由后端统一展开；后端复判
结果、自动建议评分和用户最终评分同时入日志。

学习会话需要持久化选中的卡，保证同一 `session_id` 刷新后批次与每日上限口径不变：

```sql
CREATE TABLE study_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, uuid TEXT NOT NULL UNIQUE,
    plan_id INTEGER REFERENCES study_plans(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'active',
    card_limit INTEGER NOT NULL, new_quota INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL, last_accessed_at TEXT NOT NULL, completed_at TEXT
);
CREATE TABLE study_session_cards (
    session_id INTEGER NOT NULL REFERENCES study_sessions(id) ON DELETE CASCADE,
    card_id INTEGER NOT NULL REFERENCES vocabulary_cards(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL, bucket TEXT NOT NULL, -- due | new
    status TEXT NOT NULL DEFAULT 'pending', graded_at TEXT,
    PRIMARY KEY (session_id, card_id), UNIQUE (session_id, sequence)
);
```

### 0003 课程与知识点（W1 建表，W2 接入 UI）

```sql
CREATE TABLE courses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    stage TEXT NOT NULL DEFAULT '',       -- 阶段标签：primary/junior/senior/cet/postgraduate/...
    status TEXT NOT NULL DEFAULT 'active',-- active | paused | archived
    sequence INTEGER NOT NULL DEFAULT 0,
    deleted_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    course_id INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    sequence INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'draft', -- draft | ready | done
    deleted_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    parent_id INTEGER REFERENCES skills(id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    skill_type TEXT NOT NULL DEFAULT 'grammar', -- grammar | vocabulary | reading | listening | strategy
    stage TEXT NOT NULL DEFAULT '',
    difficulty INTEGER NOT NULL DEFAULT 3,      -- 1~5
    status TEXT NOT NULL DEFAULT 'active',
    deleted_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE skill_dependencies (
    skill_id INTEGER NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    prerequisite_id INTEGER NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    PRIMARY KEY (skill_id, prerequisite_id)
);
CREATE TABLE lesson_skills (
    lesson_id INTEGER NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
    skill_id INTEGER NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    PRIMARY KEY (lesson_id, skill_id)
);
CREATE TABLE question_skills (
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    skill_id INTEGER NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    source TEXT NOT NULL DEFAULT 'manual', -- manual | ai_label | import
    PRIMARY KEY (question_id, skill_id)
);
```

删除课程不得级联删除 skills（规划书 6.2 DoD）——外键方向已保证。

### 0004 任务、事件、掌握度（W2）

```sql
CREATE TABLE daily_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_date TEXT NOT NULL,              -- 本地日历日 YYYY-MM-DD（按用户时区生成，生成时刻记录 UTC）
    task_type TEXT NOT NULL,              -- review_due | continue_reading | redo_wrong | custom
    target_type TEXT NOT NULL DEFAULT '',
    target_id INTEGER,
    title TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending', -- pending | done | skipped | carried
    generated_by_rule TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL, completed_at TEXT,
    UNIQUE (task_date, task_type, target_type, target_id)  -- 幂等：重复生成不重复建任务
);

CREATE TABLE learning_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    verb TEXT NOT NULL,                   -- read | answer | review | import | collect_word | backup ...
    object_type TEXT NOT NULL,
    object_id INTEGER,
    result_json TEXT NOT NULL DEFAULT '{}',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    occurred_at TEXT NOT NULL
);
CREATE INDEX idx_events_time ON learning_events(occurred_at);
-- 只追加；修正用补偿事件，不改历史（规划书 C.3）。日报等派生数据必须可由本表重算。

CREATE TABLE mastery_states (
    skill_id INTEGER PRIMARY KEY REFERENCES skills(id) ON DELETE CASCADE,
    mastery_score REAL NOT NULL DEFAULT 0,  -- 0~1，W2 用简单加权正确率起步
    evidence_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE metrics_daily (
    metric_date TEXT PRIMARY KEY,
    data TEXT NOT NULL DEFAULT '{}',       -- 派生缓存，可由 learning_events 重算
    computed_at TEXT NOT NULL
);
```

### 0005 备份目录（W2）

```sql
CREATE TABLE backup_catalog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'manual',  -- manual | pre_migration | daily
    checksum TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    app_version TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'ok',    -- ok | verified | corrupt
    created_at TEXT NOT NULL
);
```

### 0006 词级调度与题型开关

`review_items` 增加只用于历史保全的归档元数据；归档行永不进入队列：

```sql
ALTER TABLE review_items ADD COLUMN archived_at TEXT;
ALTER TABLE review_items ADD COLUMN archive_reason TEXT NOT NULL DEFAULT '';

CREATE INDEX idx_review_vocabulary_due
    ON review_items(item_type, manually_suspended, state, due_at, ref_id)
    WHERE archived_at IS NULL;
```

人工暂停从旧卡级调度行迁为“词条 × 题型”的唯一事实。一个题型存在多个语境变体时，暂停
任一旧变体会暂停该词条的整个题型；全局 `study_settings.enabled_card_types` 再与此表取交集：

```sql
CREATE TABLE vocabulary_card_type_settings (
    entry_id INTEGER NOT NULL REFERENCES vocabulary_entries(id) ON DELETE CASCADE,
    card_type TEXT NOT NULL,
    manually_suspended INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (entry_id, card_type)
);
```

迁移数据规则：

1. 按 `vocabulary_cards.entry_id` 分组旧 `item_type='vocabulary_card'` 行，`reps` 最大者胜；
   平手时 `due_at` 最早者胜，再以 `id` 作为确定性兜底。
2. 胜出行原位改为 `item_type='vocabulary'、ref_id=entry_id`，保留 UUID、FSRS 全状态与其
   原有日志外键；同词其余旧行写入 `archived_at/archive_reason`，不物理删除。
3. `review_logs` 不改写、不重挂；`card_id` 仍指向当时作答题型，历史逐行可追溯。
4. 迁移前已持久化的活跃 session 由旧卡级策略选出，统一结束，下一次请求按词重新选卡。
5. 新建卡片只确保对应词条存在一个活跃词级复习项；grade API 仍收 `card_id`，先由卡片定位
   `entry_id`，再更新该词条的唯一 FSRS 状态。
6. 出卡轮换使用可参数化的阶段策略：new/首照面优先 forward；learning/relearning 在
   reverse/listening 间轮换；稳定 review 在 spelling/cloze 间轮换；有显式搭配事实时周期性
   选择 collocation。首选题型关闭或暂停时，只在剩余可用题型内确定性降级。

## 4. 存量词汇接入 FSRS（迁移 0002 数据部分；最终由 0006 收敛为词级）

1. 给所有旧词条回填 UUID、结构化词义/lemma/关系事实；旧 `phonetic` 只在新字段为空时复制到 `phonetic_uk`，原字段不清空。
2. 0002 为每个未删除旧词条至少生成一张 `forward/primary` 卡并建立旧式
   `item_type='vocabulary_card'` 行；0006 随后把胜出行原位收敛为
   `review_items(item_type='vocabulary', ref_id=entry.id)`。最终运行态不得再创建卡级调度行。
3. 旧 `next_review_at` / `last_reviewed_at` 是**本地 naive 时间**：按迁移执行机的本地时区换算成 UTC；若原值有效，即使已过期也保留换算后的精确时刻，缺失或损坏才回退迁移时刻。
4. 有有效 `last_reviewed_at` → `state='review'`；从未复习 → `state='new'`、`due_at=` 迁移时刻。旧 interval 只能近似初始化 stability/difficulty，评分后由正式 FSRS 接管，`scheduler_version='legacy-bootstrap-v1'` 明示来源。
5. `vocabulary_reviews` 旧历史**逐行只读保留**，不伪造 attempt_id、不迁入 review_logs；新复习一律写 review_logs。
6. 迁移可重复启动：不得产生重复卡片、review_items 或结构化事实。旧接口
   `POST /api/vocabulary/{id}/review` 保持可用，定位/补建正向卡后映射
   again→1、hard→2、mastered→4，内部走同一 FSRS 评分核心。

## 5. 存量错题接入复习中心（W2，迁移 0004 之后的服务逻辑）

`wrong_stats` 中 `wrong_count>0` 且未连续正确淘汰的题目，按需生成 `review_items(item_type='wrong_question', ref_id=question_id)`；由服务在错题发生时创建/更新，不做一次性全量回填（避免积压雪崩，规划书 6.12 每日上限原则）。
