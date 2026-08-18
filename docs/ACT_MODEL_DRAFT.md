# ACT_MODEL_DRAFT — 统一活动、尝试、响应与学习事件模型草案

> 状态：研究冻结 v0.1；Codex 与 Claude 已在 handoff 035/038/039 双签。后续实现仍须先并入 `DATA_MODEL.md` / `API_CONTRACT.md`。
> 本文只冻结边界与迁移方向，不授权建表或改接口；功能范围以 `docs/SPEC_V2.md` 为准。
> 覆盖核心约束：ACT-01/03/05/06/07/11/19/21/22/23/26；遵守 D-02，现有刷题机继续作为考试内核。

## 1. 冻结结论

1. **活动定义与一次作答分离。** `activities` 保存稳定身份；`activity_versions` 保存不可变内容与评分合同；`attempts` 绑定一个精确版本；`responses` 保存该次尝试的草稿与提交快照。
2. **发布版本不可原地改写。** 题干、稳定选项键、响应结构、评分规则或素材定位任一变化都生成新版本。旧尝试始终按旧版本回放和审计。
3. **提交前可覆盖草稿，提交后只追加。** 自动保存更新当前草稿；提交时写不可变快照并锁定。评分失败不能删除或回滚原始响应。
4. **自动评分、自评和最终证据分栏。** `auto_evaluation`、`self_evaluation`、`final_evaluation` 不互相冒充；自评不得计入客观正确率。
5. **统一事件是报告事实源，不是业务事务替代品。** 考试、词汇 FSRS、错题调度仍由现有领域表负责；`learning_events` 只追加跨域事实，日报和分析可从事件重算。
6. **现有内核不重写。** `practice_*` 继续是考试作答与判分事实源；`vocabulary_cards/review_items/review_logs` 继续是词汇呈现、FSRS 与审计事实源。统一层先用适配器和稳定引用接入。
7. **客户端 UUID 是写幂等基础。** 一次尝试的 `attempt_uuid` 由客户端生成；同 UUID 重试返回同一尝试和首次提交结果，不产生第二次评分或事件。

## 2. 核心对象

### 2.1 activities

稳定、可软删除的逻辑活动身份，不承载会变化的题干或答案。

```sql
CREATE TABLE activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    activity_type TEXT NOT NULL,
    source_kind TEXT NOT NULL,          -- native | practice_question | vocabulary_card | resource_task
    source_ref TEXT,                    -- 领域稳定引用；native 活动可空
    status TEXT NOT NULL DEFAULT 'draft', -- draft | published | retired
    current_version_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE UNIQUE INDEX idx_activities_source
    ON activities(source_kind, source_ref)
    WHERE source_ref IS NOT NULL;
```

`activity_type` 首批只登记已经有后端事实来源的 `single_choice`、`boolean`、`text_gap`、`cloze`、`spelling`、`reading_task`、`self_rating`，另保留只承载有序子活动引用的内部类型 `assessment_bundle`。登记类型不代表对应渲染器已经交付；`unsupported` 是运行时能力判定，不写成活动生命周期。未知类型必须保留原数据，API 返回不可作答能力，客户端展示修复说明而不白屏。

`source_ref` 优先使用领域稳定键：题目使用 `external_key`，缺失时使用 `content_hash + 题库包版本`；词汇卡使用 UUID；资源任务使用资源 UUID 与解析版本。只有本机自增 ID 的旧对象在映射前先获得 UUID 或进入独立映射表，禁止把 `question_id` 等本机 ID 当作可迁移身份。

`current_version_id` 必须为空或指向同一 activity 的 published version；发布事务在版本落盘后更新该指针。实现迁移时用延迟外键、触发器或等价服务校验落实该不变量，不能允许跨 activity 指针。

### 2.2 activity_versions

一次发布后的不可变合同。JSON 字段必须有版本号、JSON Schema 和确定性规范化规则，不能把任意前端状态直接落库。

```sql
CREATE TABLE activity_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    activity_id INTEGER NOT NULL REFERENCES activities(id) ON DELETE RESTRICT,
    version INTEGER NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    content_json TEXT NOT NULL,
    response_schema_json TEXT NOT NULL,
    scoring_json TEXT NOT NULL,
    renderer_contract_json TEXT NOT NULL DEFAULT '{}',
    checksum TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft', -- draft | published | retired
    created_at TEXT NOT NULL,
    published_at TEXT,
    UNIQUE (activity_id, version),
    UNIQUE (activity_id, checksum)
);
```

- ACT-03：选择题使用 `stable_option_key` 判分；显示顺序写入尝试上下文，不能用 A/B/C/D 位置作事实键。
- ACT-05：判断题内部响应是布尔值或稳定陈述键，题面里的 `T/F/是/否` 只属于渲染层。
- ACT-06/11：每个输入位有稳定 `response_key`，评分合同显式列出 Unicode、大小写、空格、标点、缩写和英美拼写规范化选项。
- ACT-07：篇章与空位都使用稳定 ID；共享词库和独立选项不能靠数组下标关联。
- ACT-19：阅读任务保存 `resource_uuid + content_version + locator` 与完成规则；“滚到底”最多是一条证据，不能单独判完成。

### 2.3 attempts

一次用户尝试的生命周期与提交锁。`uuid` 即 API 的 `attempt_id`。

```sql
CREATE TABLE attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    activity_id INTEGER NOT NULL REFERENCES activities(id),
    activity_version_id INTEGER NOT NULL REFERENCES activity_versions(id),
    actor_type TEXT NOT NULL DEFAULT 'local_profile',
    actor_ref TEXT NOT NULL DEFAULT 'default',
    mode TEXT NOT NULL DEFAULT 'practice', -- practice | exam | review | consolidation
    status TEXT NOT NULL DEFAULT 'active', -- active | submitted | scored | abandoned | needs_repair
    context_json TEXT NOT NULL DEFAULT '{}',
    submission_policy_json TEXT NOT NULL DEFAULT '{}',
    revision INTEGER NOT NULL DEFAULT 0,
    score REAL,
    max_score REAL,
    started_at TEXT NOT NULL,
    last_saved_at TEXT NOT NULL,
    submitted_at TEXT,
    locked_at TEXT,
    scored_at TEXT,
    duration_ms INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_attempts_activity ON attempts(activity_id, started_at DESC);
```

- `activity_version_id` 和 `submission_policy_json` 在创建后冻结。
- `revision` 做乐观并发；过期草稿更新返回 409 并带服务器当前 revision，不静默覆盖另一个窗口的新答案。
- `locked_at` 非空后禁止覆盖已提交响应。允许重试时创建新的 `attempt_uuid`，不解锁旧尝试。
- 考试模式未答提交、尝试次数、是否可改答案等规则由创建时快照决定，不能随全局设置变化而回写历史。

### 2.4 responses

每个响应位的当前草稿和提交快照。开放题、录音或多文件响应只保存资产引用，不把大对象塞进 JSON。

```sql
CREATE TABLE responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uuid TEXT NOT NULL UNIQUE,
    attempt_id INTEGER NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
    response_key TEXT NOT NULL,
    draft_json TEXT NOT NULL DEFAULT '{}',
    submitted_json TEXT,
    auto_evaluation_json TEXT,
    self_evaluation_json TEXT,
    final_evaluation_json TEXT,
    score REAL,
    max_score REAL,
    revision INTEGER NOT NULL DEFAULT 0,
    last_saved_at TEXT NOT NULL,
    submitted_at TEXT,
    locked_at TEXT,
    evaluated_at TEXT,
    UNIQUE (attempt_id, response_key)
);
```

ACT-25 所需的逐次变化不依赖覆盖后的 `draft_json`；实现阶段另设只追加的 `response_events`，至少记录 `client_event_id`、attempt、response_key、revision、value_hash、occurred_at。敏感正文是否存完整快照由活动类型与隐私策略决定。

## 3. 提交、评分与事件状态机

```text
active --autosave--> active --submit--> submitted --score--> scored
   |                     |                  |
   +--abandon----------> abandoned          +--scoring failed--> submitted
```

1. 自动保存校验 `activity_version + response_schema`，以 `(attempt_uuid, response_key, expected_revision)` 幂等更新；失败保留客户端本地草稿并显示未同步状态。
2. 提交在单个事务内：复核完整性 → 将所有 `draft_json` 复制为 `submitted_json` → 设置 `submitted_at/locked_at` → 追加 `activity.submitted` 事件或事件 outbox。
3. 客观评分读取绑定版本的 `scoring_json`，绝不读取活动当前版本；评分成功后写评价与分数，再追加 `activity.scored`。
4. 评分器异常时尝试仍为 `submitted`，原始响应已锁定且可重试评分；不得把评分失败伪装成零分。
5. 主观活动先写 `self_evaluation_json`；未来 Provider 结果是另一份证据，不能覆盖用户自评。

## 4. learning_events v2

现有 0004 `learning_events(verb, object_type, object_id, result_json, duration_ms, occurred_at)` 保留。未来迁移只增列并回填，不重建旧事实：

- `uuid`：事件稳定 ID；
- `actor_type/actor_ref`：本地档案；
- `object_uuid`：跨导出稳定对象引用；
- `attempt_uuid`：连接活动尝试、`review_logs.attempt_id` 或考试适配器；
- `context_json`：来源任务、会话、资源、版本和设备上下文；
- `schema_version`：事件载荷版本；
- `correction_of_uuid`：补偿事件引用；
- `recorded_at`：本机真正落盘时间，与行为发生的 `occurred_at` 分离。

受控动词首批为 `activity.started`、`response.saved`、`activity.submitted`、`activity.scored`、`activity.abandoned`、`review.graded`、`resource.read`。旧 `read/answer/review/...` 事件继续可读，通过报告适配器映射，不原地改写。除上述字段外，v2 增列还必须包含 `object_version`、`response_uuid` 与 `source`（`manual/import/rule/provider`），完整满足 SPEC_V2 §12 的对象版本与来源追溯要求。

关键业务事务采用 outbox：业务提交与 `learning_event_outbox` 同事务写入，短事务随后搬运到事件表；序列化或事件表异常时保留 pending/failed 状态并进入健康检查。SQLite 文件整体不可写时，业务本身也无法安全完成，不承诺虚假成功。

## 5. 现有内核渐进映射（D-02）

| 现有事实源 | 统一活动映射 | 过渡期写入权威 | 禁止事项 |
|---|---|---|---|
| `questions/options` | 每题映射一个 `activity`；发布内容形成 `activity_version`；领域稳定键进入 source_ref | 题目内容仍由旧题库管理 | 不批量重写题库，不把本机 question_id 或显示标签当稳定键 |
| `practice_sessions/practice_answers/practice_answer_events/practice_unit_submissions` | 会话的篇目/题目顺序先固化为 `assessment_bundle` 版本，再适配为 exam attempt/response/response_event；先只读投影，后影子记录 | `practice_*` 继续负责考试保存、锁定和判分 | 不在统一引擎达到对等验收前切换考试写路径 |
| `vocabulary_cards` | card_type + content_version 映射版本化活动载荷 | 词汇卡片生成器 | 不把六题型重新变成六个调度时钟 |
| `review_items/review_logs` | attempt_uuid 连接一次活动证据；FSRS 前后状态仍在 review_logs | 词级 FSRS 与错题调度服务 | 不把复习调度状态塞进 attempts/responses |
| `resources/resource_progress` | reading_task 的 locator 与完成证据 | 资源与阅读服务 | 不以单次滚动位置代替完成规则 |

旧考试表逐表映射固定为：`practice_sessions → attempts`；`practice_answers → responses` 当前草稿/提交快照；`practice_answer_events → response_events` 只追加变化；`practice_unit_submissions → 对应 response 子集的 submitted_json/locked_at + activity.submitted checkpoint 事件`。按篇提交时总 attempt 可继续保持 active，但已锁定子集不得再改；整卷提交再锁定剩余响应并推进 attempt。

实施分三步：

1. **适配读取**：统一 DTO 与事件名，不建新写入口；补齐稳定 source_ref。
2. **影子记录**：从已提交的原领域事实异步、幂等派生 attempts/responses/outbox；失败进入可重建队列，不反向阻塞或修改考试事实。旧会话增加 nullable UUID 或由映射表保存 UUID，禁止以本机 session ID 充当稳定 attempt ID。差异监控但仍由旧内核判分。
3. **新活动原生写入**：仅新建且无旧内核依赖的活动走统一写路径；考试内核切换必须另有对等回归和用户数据迁移方案。

## 6. API 边界候选（尚未写入 API_CONTRACT）

- `GET /api/activities/{uuid}`：返回指定或当前发布版本、renderer contract 与 capabilities。
- `POST /api/activities/{uuid}/attempts`：请求携带客户端 `attempt_id`、mode；同键重试返回原对象。
- `PUT /api/attempts/{attempt_id}/responses/{response_key}`：携带 `expected_revision` 和 JSON 响应；只保存草稿。
- `POST /api/attempts/{attempt_id}/submit`：幂等提交与锁定；响应明确 `submitted` 或 `scored`，评分失败仍返回已保存的提交状态和可恢复错误。
- `GET /api/attempts/{attempt_id}`：恢复草稿/提交状态；权限与本地档案校验不能仅靠知道 UUID。

现有 `/api/practice/**`、`/api/study/**`、`/api/review/**` 不在本阶段删除或改语义。

## 7. 核心约束验收表

| 功能 | 研究冻结验收 |
|---|---|
| ACT-01 | 活动稳定身份、不可变版本、未知类型降级已定义 |
| ACT-03 | stable_option_key 与 display_order 分离 |
| ACT-05 | 内部布尔/陈述键与题面标签分离 |
| ACT-06 | 多空稳定键和逐项 normalizer 合同 |
| ACT-07 | passage/blank/shared bank 均用稳定键 |
| ACT-11 | 主动输入、规范化和差异证据分离 |
| ACT-19 | 资源版本定位与复合完成证据 |
| ACT-21 | auto/self/final 三类评价分栏 |
| ACT-22 | revision、冲突恢复、未同步提示边界 |
| ACT-23 | 提交快照、锁定、评分失败保留原响应 |
| ACT-26 | 事件 v2、补偿事件和 outbox 边界 |

## 8. 草案冻结选择

1. 表名采用 030 与本批次约定的通用名 `attempts/responses`。两表都通过外键绑定活动版本，当前仓库无同名表；若实施前出现真实命名碰撞，再走 DATA_MODEL 提案变更，研究冻结阶段不预防性改名。
2. `response_events` 默认只保存 hash、长度、修订号和差异元数据；离散短答案可由活动策略显式保存完整值，作文/录音只保存版本或资产引用。`responses.submitted_json` 始终保留锁定时的完整提交快照，不能因历史策略而只剩 hash。
3. 影子差异的聚合健康状态归 APP-05；APP-12 诊断包只导出脱敏明细。两者共享同一 outbox/差异事实，不建立两套状态源。
4. 草稿冲突策略进入 `renderer_contract_json.conflict_policy`，只允许合同定义的受控枚举；409 必须返回服务器 revision、可恢复草稿和允许的处置动作。任何渲染器都不得静默使用 last-write-wins 覆盖另一份草稿。
5. 拼写、填空等有长度上限且经活动策略判定为低敏感的离散短答案，`response_events` 默认可保存完整值，以支持 ACT-25 尝试历史；仍须执行长度上限、敏感字段排除和本地隐私设置。作文、录音和其他长内容继续只保存版本、hash 或资产引用。

## 9. 明确非目标与生效条件

- 本文不修改 `DATA_MODEL.md`、`API_CONTRACT.md`，不授权迁移、路由或双写；任何实施仍需“提案 → 用户确认 → 合同文档 → 编号迁移”。
- 本阶段不回填全部旧练习、不改变 `/api/practice/**`、`/api/study/**`、`/api/review/**` 语义，也不把词级 FSRS 退回卡级调度。
- 草案只冻结对象边界、版本/锁定/事件不变量与渐进映射；字段类型、索引和 API 载荷在进入实现阶段时另行逐项验收。
