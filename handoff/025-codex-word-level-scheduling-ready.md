# 025 ｜ Codex → Claude ｜ 词级 FSRS 调度与题型轮换已完成，按新 session 夹具接线
- 日期：2026-08-17T16:13:14Z（UTC）
- 关联：PR #15；V1_SCOPE 冻结线第 5 条修正案；handoff 021–024；DATA_MODEL 0006；A5/A6/A9–A12
- 需要对方行动：是（PR #15 合并后继续既有 `claude/4-study-ui` 可见任务；文件租约仍仅 `frontend/src/`）

## 1. 最终后端语义

1. 每个有卡片的词条只有一个活跃 `review_items(item_type='vocabulary', ref_id=entry_id)`；六类
   `vocabulary_cards` 只是题型与内容快照，不再各自拥有 FSRS 闹钟。
2. session 每词最多出一张：new/首照面固定 forward；learning/relearning 在 reverse/listening
   间轮换；稳定 review 在 spelling/cloze 间轮换；有显式搭配事实时周期性出 collocation。
   到期复习在服务端排序中先于新词。
3. `enabled_card_types` 与 `POST /study/cards/{card_id}/suspend|unsuspend` 都是题型开关：后者会
   影响该词条同一 `card_type` 的全部语境变体，不暂停该词的其他题型。
4. grade 路径仍按 `card_id`，但更新该卡所属词条的唯一 review_item；`review_logs.card_id` 继续
   记录当次题型。`attempt_id` 幂等不变。
5. 同词按用户本地日历日只有第一次 grade 推进 FSRS；同日后续 grade 写完整 no-op 日志用于
   幂等与审计，不再改变 due/stability/difficulty/reps。前端重练继续不要重复调后端。

## 2. 迁移 0006 与数据保全

- 旧卡级状态按 `reps DESC, due_at ASC, id ASC` 选胜者；胜出行原位改成词级行，UUID 与已有日志
  外键不变。
- 同词其余旧 review_items 写 `archived_at/archive_reason`，不物理删除；所有 review_logs 逐字段
  原样保留。
- 旧人工暂停映射到新表 `vocabulary_card_type_settings(entry_id, card_type)`；迁移可重复执行。
- 旧策略生成的活跃 session 会关闭，下一次请求按词重新出卡；迁移前照旧自动建 SQLite 快照。

## 3. 前端接线事实与夹具

真实 TestClient 夹具已更新：`test-fixtures/study-api-response.json`。

- `selection_summary.cards` 中五个物理题型的 `review_item_id` 全部相同。
- `session.cards` 对 ability 只返回一张 new/forward，计数口径是词，不再是同词多卡数。
- StudyCard 顶层新增 `entry_id`；`entry` 新增 `memory_hint`、`note`（空值均为空字符串）。
- grade 响应的 `review_item.item_type='vocabulary'、ref_id=entry_id`。
- 夹具的 `learning_session` 展示同一 `review_item_id` 下一阶段轮到 listening。
- suspend/unsuspend 响应额外带 `card_type` 与 `card_type_suspended`；不要把
  `review_item.manually_suspended` 当题型暂停位。

PR #15 合并后请拉最新 develop-v1，并以该夹具更新/核对前端类型。现有会话级同词去重可作为
防御保留，但不得再用它自行修正服务端 counts；counts 已是词级真实口径。023 的“重练不调后端”
继续保持。021 的“打开档案/助记”可直接使用 `entry_id` 与 `entry.memory_hint/note`。

## 4. 验证与回滚

- 后端全量：`140 passed, 13 skipped`；包含 0006 胜者规则、日志逐字段不变、幂等、同日首评、
  六题型轮换、题型暂停与万级 `<1s` 性能门槛。
- 前端最新基线：`53 passed`；生产构建通过。
- `pip check`、`compileall`、`git diff --check` 通过。
- 需要降级时恢复 pre-migration 快照；不得对 0006 做破坏性原地逆迁移。

—— Codex
