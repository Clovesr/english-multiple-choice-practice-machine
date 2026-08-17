# AGENTS — Codex 工作指引

你是本项目的**后端主责**。项目正从"考研英语刷题机"扩展为"个人英语学习系统"（本地离线、单用户、Windows、无强制 AI）。完整规划见用户的规划书；仓库内约定如下，按优先级：

1. **V1_SCOPE.md** — 当前版本做什么/不做什么。范围外的想法写进 KNOWN_ISSUES.md 延后需求，不要实现。
2. **API_CONTRACT.md** — 接口唯一事实来源。实现必须与之一致；要改接口，先改文档并请用户确认。
3. **DATA_MODEL.md** — 表结构与迁移唯一事实来源。旧表只增列不改语义；迁移带编号、幂等、迁移前自动备份。
4. **ACCEPTANCE.md** — 每个功能的完成标准。PR 描述引用场景编号并附执行结果。
5. **KNOWN_ISSUES.md** — 已知问题登记。
6. **docs/COLLABORATION.md** — 文件所有权、分支、每日节奏。**你不改 `frontend/`**（Claude 主责）。
7. **docs/AUDIT.md** — 现状审计与不可破坏功能清单（回归基线）。

## 环境

- Python 3.14 venv：`.venv/`（lxml 用 6.1.1，见 KNOWN_ISSUES KI-3）。
- 测试：`.venv/Scripts/python -m pytest tests/ -q`，当前基线 85 passed, 13 skipped。提交前必须全绿。
- 启动：`.venv/Scripts/python run_app.py` → 127.0.0.1:8765。
- 数据库：SQLite `backend/data/question_bank.db`；新代码时间戳一律 UTC ISO-8601 带偏移，禁止 naive `datetime.now()`。
- FSRS 用 py-fsrs（加入 requirements.txt 时在 PR 中声明）。

## 红线

- 迁移失败不得破坏原库；用户导入的原始文件只读。
- 不动 `frontend/`、不擅自改共享文件（契约文档、README、requirements.txt 的变更单独提交并声明）。
- 每个 PR 小步、带测试、附验收结果。
