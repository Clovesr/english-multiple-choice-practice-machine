# 现状审计（2026-08-17，develop-v1 起点）

> 规划书第 21.2 节第 1 周任务第 3 条的交付物。审计对象：`main` 分支 `ffe47c6`。
> 结论：**基线健康**。85 个后端测试通过、13 个跳过、0 失败；代码约 1.6 万行 Python + 前端 Vue 3。可以按"保留旧功能、增量加新表"的策略继续开发。

## 1. 运行形态

| 项 | 现状 |
|---|---|
| 启动 | `run_app.py` → uvicorn `127.0.0.1:8765`，就绪后自动开浏览器 |
| 前端 | Vue 3.5 + TypeScript + Vite；构建产物 `frontend/dist` 由 FastAPI 托管，开发模式 vite 5173 + CORS |
| 数据 | `backend/data/`（gitignore）：`question_bank.db`、`uploads/`、`question_banks/` |
| 版本 | 应用自称 0.1.0；许可 GPL-3.0-only |
| 首屏 | 服务端把 dashboard 数据注入 `window.__LINJIAN_STARTUP__` 加速首页 |

## 2. 数据库（24 张表）

| 分组 | 表 |
|---|---|
| 题库配置 | question_bank_profiles、app_settings |
| 试卷与题目 | papers、units、questions、options |
| 练习记录 | practice_sessions、practice_answers、practice_answer_events、practice_unit_submissions |
| 错题 | wrong_stats |
| 词汇 | vocabulary_entries、vocabulary_occurrences、vocabulary_reviews |
| 导入与内容 | import_jobs、revision_log、question_bank_packages、question_bank_assets、question_bank_revisions |
| 回收站 | trash_entries |
| AI | ai_settings、ai_profiles、ai_profile_models、ai_conversations、ai_messages、question_ai_labels、question_label_run_items、wrong_analysis_reports、wrong_analysis_states |

### 关键观察

- **无版本化迁移**：schema 为 `CREATE TABLE IF NOT EXISTS` + 每次启动跑 `_ensure_column` 补丁（`database.py:_run_migrations`），无 schema_migrations 表、无迁移前备份。新系统必须先补上（见 DATA_MODEL.md 迁移 0001）。
- **词汇复习不是 FSRS**：固定间隔 again=1 天 / hard=3 天 / mastered=7 天（`services/vocabulary.py:review_entry`）。
- **时区不一致（已存在的缺陷）**：复习字段用 `datetime.now()` 本地 naive 时间，其余 `CURRENT_TIMESTAMP` 为 UTC。`vocabulary_entries.next_review_at / last_reviewed_at` 与 `created_at` 语义不同。FSRS 迁移时必须显式换算（见 KNOWN_ISSUES #1）。
- **好的既有基础**（与规划书原则一致，直接复用）：软删除 + 回收站（7 天 purge）、revision_log 修订日志、questions.content_hash / external_key 稳定键、题库包 package_id + content_version 版本化、导入草稿-发布两段式。
- 连接方式：每请求新建 sqlite3 连接，`check_same_thread=False`；无 WAL（规划书建议开 WAL，迁移 0001 处理）。

## 3. API 面（9 个路由器，约 70 个端点，全部挂在 /api）

| 路由器 | 覆盖 |
|---|---|
| dashboard | startup / overview |
| papers | 列表、详情、删除 |
| practice | 会话创建/查询、逐题作答、整卷与按单元提交 |
| wrong | 错题列表、标记高频 |
| imports | Word/PDF 草稿导入：上传、解析、答案、发布、删除、AI 辅助 |
| question_banks | ESQ 包导入/导出/schema/资产 |
| question_bank_profiles | 多题库配置、激活、批量移动、回收站 |
| vocabulary | 增删改查、home 队列、翻译任务、复习（固定间隔） |
| ai | 配置、多 Profile、模型目录、对话、错因分析、题目标注 |

## 4. 前端

- 9 个路由页面：Dashboard、Library、Practice/:id、Wrong、Vocabulary、Imports、Assistant、Settings、Trash。
- 组件：AiAssistant、ContentBlocks、ListeningPlayer（考试听力已有）、QuestionBankSwitcher。
- `api.ts` 单文件封装全部请求；无 Pinia（组件内状态）、无 UI 组件库（自研 styles.css + lucide 图标）、**无前端测试框架**。

## 5. 测试

- 12 个 pytest 文件，全部针对后端：`85 passed, 13 skipped`（2026-08-17，Python 3.14.6 + lxml 6.1.1）。
- 覆盖：ESQ、docx 解析、批量导入、听力资产、词汇、错因分析、题目标注、题库配置。
- 缺口：前端 0 测试；无 E2E；无迁移/备份测试（对象尚不存在）。

## 6. 不可破坏功能清单（回归基线）

以下功能在任何 V1 改动后必须仍然可用，作为每日集成回归项：

1. 考研/四六级试卷列表、进入练习、逐题作答、提交判分、成绩展示
2. 错题本列表、按单元错因统计、标记高频
3. 单词本：从练习划词收藏、翻译队列、复习入口（接 FSRS 后旧入口语义映射，不得报错）
4. Word/PDF 导入草稿 → 校对 → 发布
5. ESQ 题库包导入/导出、内置题库安装
6. 考试听力播放（ListeningPlayer + 资产服务）
7. 回收站恢复、题库 Profile 切换
8. AI 助手在未配置模型时不阻塞其他功能（现状已满足，保持）

## 7. 环境发现

- **lxml==6.0.0 在 Python 3.14 无 Windows wheel**，无法安装；6.1.1 有 cp314 wheel 且全部测试通过。requirements.txt 已在 develop-v1 更新为 6.1.1。
- Python 3.14 下有 asyncio.iscoroutinefunction 弃用警告（来自 FastAPI 内部，暂不处理）。

## 8. 与规划书能力的差距映射

| 规划能力 | 现状 |
|---|---|
| 资源库（6.3）、阅读（6.7）、课程与知识点（6.2）、统一复习（6.12）、今日任务、学习事件（6.13）、备份（6.17）、全文搜索（6.14） | **全部缺失**，V1 起新建 |
| 词汇（6.5） | 有词条+语境+翻译，缺 FSRS、卡片模板、词义结构 |
| 活动/考试（6.11） | 客观题引擎完整，作为 Assessment 子系统保留 |
| 内容编辑（6.4） | 导入草稿编辑已有雏形（revision_log），无通用资源编辑 |
| 听力（6.8） | 考试听力播放已有，无精听时间轴 |
