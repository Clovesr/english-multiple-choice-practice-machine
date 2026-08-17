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
| 0002 | review_items、review_logs、词汇存量数据接入 FSRS（§4） | W1 |
| 0003 | courses、lessons、skills、skill_dependencies、lesson_skills、question_skills | W1 定稿 / W2 使用 |
| 0004 | daily_tasks、learning_events、mastery_states、metrics_daily | W2 |
| 0005 | backup_catalog | W2 |

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

### 0002 统一复习（FSRS）

```sql
CREATE TABLE review_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    item_type TEXT NOT NULL,              -- vocabulary | wrong_question（V1 只有这两类；后续: sentence/grammar/dictation）
    ref_id INTEGER NOT NULL,              -- vocabulary → vocabulary_entries.id；wrong_question → questions.id
    state TEXT NOT NULL DEFAULT 'new',    -- new | learning | review | relearning
    due_at TEXT NOT NULL,
    stability REAL NOT NULL DEFAULT 0,
    difficulty REAL NOT NULL DEFAULT 0,
    reps INTEGER NOT NULL DEFAULT 0,
    lapses INTEGER NOT NULL DEFAULT 0,
    last_review_at TEXT,
    suspended INTEGER NOT NULL DEFAULT 0,
    scheduler TEXT NOT NULL DEFAULT 'fsrs',
    scheduler_version TEXT NOT NULL,      -- py-fsrs 版本号
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (item_type, ref_id)
);
CREATE INDEX idx_review_due ON review_items(suspended, due_at);

CREATE TABLE review_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_item_id INTEGER NOT NULL REFERENCES review_items(id) ON DELETE CASCADE,
    rating INTEGER NOT NULL,              -- 1 Again | 2 Hard | 3 Good | 4 Easy
    state_before TEXT NOT NULL,
    due_before TEXT NOT NULL,
    due_after TEXT NOT NULL,
    stability_after REAL NOT NULL,
    difficulty_after REAL NOT NULL,
    elapsed_days REAL NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    reviewed_at TEXT NOT NULL
);
CREATE INDEX idx_review_logs_item ON review_logs(review_item_id, reviewed_at);
```

FSRS 引擎：**py-fsrs（MIT）**，默认参数起步；`review_logs` 保全量历史，未来参数优化可重算，不覆写历史（规划书 C.3）。

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

## 4. 存量词汇接入 FSRS（迁移 0002 数据部分）

1. 为每个未删除 `vocabulary_entries` 建一条 `review_items(item_type='vocabulary', ref_id=entry.id)`。
2. 旧 `next_review_at` / `last_reviewed_at` 是**本地 naive 时间**：按迁移执行机的本地时区换算成 UTC 后写入。
3. 初始状态映射：有 `last_reviewed_at` → `state='review'`，`due_at=`换算后的 next_review_at（已过期则为迁移时刻）；从未复习 → `state='new'`，`due_at=` 迁移时刻。stability/difficulty 用 FSRS 默认初始化，首次评分后由算法正式接管（近似值，可接受，写入迁移说明）。
4. `vocabulary_reviews` 旧历史**只读保留**，不迁入 review_logs；新复习一律写 review_logs。
5. 旧接口 `POST /api/vocabulary/{id}/review` 保持可用：评分映射 again→1、hard→2、mastered→4，内部走 FSRS（见 API_CONTRACT.md §6）。

## 5. 存量错题接入复习中心（W2，迁移 0004 之后的服务逻辑）

`wrong_stats` 中 `wrong_count>0` 且未连续正确淘汰的题目，按需生成 `review_items(item_type='wrong_question', ref_id=question_id)`；由服务在错题发生时创建/更新，不做一次性全量回填（避免积压雪崩，规划书 6.12 每日上限原则）。
