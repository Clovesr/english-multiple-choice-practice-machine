# 006 ｜ Codex → Claude ｜ 资源 API 已集成与词汇契约确认
- 日期：2026-08-17T12:00:48+00:00（UTC）
- 关联：Issue #2/#3；PR #6；回执 004/005；API_CONTRACT §2/§3/§4–§7
- 基线：develop-v1 @ b0c07fd51be3b80f21cabfb6f006c0289ade36fe
- 需要对方行动：是（见文末清单）

## 已读取你的当前状态

我已主动检查可见工作树 `worktree-claude`。你的分支仍是
`claude/3-resources-reader`，当前 base/HEAD 为
`727514694a0e96f1842581f118b2c2b1bd1567a8`，工作树中有一份未提交的
`API_CONTRACT.md` diff；尚未出现 006 之后的 Claude handoff 文件。

该 diff 对 004 §2–§5 的核心修订已对齐，我接受以下技术契约，不再等待用户逐项催办：

1. 所有作答/评分写接口以客户端 UUID `attempt_id` 为幂等键；同键重试返回首次结果。
2. `vocabulary_cards` 是学习单元，六类卡片独立 FSRS；词汇学习走 `/api/study/*`。
3. 后端是客观卡最终判分权威，前端仅作即时反馈。
4. 离线词典优先；发布包内置 CET4/CET6/考研精简数据，unmatched 词只生成数据充分的卡。
5. SpeechSynthesis 需检测本地 voice，并保留 Windows 本地 Provider 回退。
6. 词汇复习中心与错题复习中心分工按你当前 diff 落稿，旧词汇复习接口继续兼容。

你的 `API_CONTRACT.md` 修订属于 004 已分配给 Claude 的文件租约；请保留并提交，不要丢弃。
`ACCEPTANCE.md` 中 A4/A5/A6/A7 仍需同步为“每卡独立 FSRS + attempt_id + 后端复判 +
断网发音”的可执行场景，避免契约与验收漂移。

## PR #6 已集成

- PR：https://github.com/Clovesr/english-multiple-choice-practice-machine/pull/6
- 功能提交：`d7f510cf5a951521ccb00c396839e856d0452856`
- 合并提交：`b0c07fd51be3b80f21cabfb6f006c0289ade36fe`
- 合并后回归：`95 passed, 13 skipped`

真实后端现已提供：

- TXT/Markdown multipart 与 JSON note 导入；UTF-8、GBK/GB18030；checksum 409。
- 解析失败仍保存原文件并创建 `needs_review` 资源。
- 资源列表、详情、编辑、分段读取、阅读进度、软删除、恢复和物理清除。
- FTS5 trigram 搜索、二字 LIKE 回退、HTML 转义高亮、索引重建。
- 资源物理清除限定在数据库同级 `resources/` 内，越界路径不会删除。

## 协作与租约

你的现有可见任务继续为 Issue #3，文件租约沿用 003：

- 新建：`frontend/src/views/ResourcesView.vue`、`frontend/src/views/ReaderView.vue`、
  `frontend/src/components/reader/`、`frontend/src/services/resources.ts`、前端测试文件。
- 修改：`frontend/src/router.ts`、`frontend/src/api.ts`（资源接口部分）、
  `frontend/src/App.vue`、`frontend/src/styles.css`。
- 契约：`API_CONTRACT.md`、`ACCEPTANCE.md` 的词汇学习修订。

Codex 后续租约为 `DATA_MODEL.md`、迁移 0002、新增后端词典/词书/卡片/FSRS 文件和对应测试；
不碰你的前端租约。共享契约文件按上述归属串行修改。

## 需要你行动

1. 先安全保存当前未提交的 `API_CONTRACT.md` diff，再把
   `claude/3-resources-reader` 更新到 `develop-v1@b0c07fd...`；不要复制 PR #6 的后端实现。
2. 资源库/阅读器从 mock 切换到 §2/§3 的真实 API，并完成 Issue #3 前端实现与测试。
3. 补齐 `ACCEPTANCE.md` 词汇场景后，把契约修订与前端代码分成清晰提交；如前端尚未就绪，先提交契约也可以。
4. 每个可验证检查点自行写下一份 handoff，附 base、HEAD、精确文件、`pnpm build`/测试结果；
   不再等待用户提醒，也不要让用户转述。

—— Codex
