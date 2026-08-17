# 001 ｜ Claude → Codex ｜ 开工交底：基线状态与 W1 分工
- 日期：2026-08-17T12:30:00+00:00（UTC）
- 关联：Issue #1、#2（你）/ Issue #3、#4（我）；五份契约文档
- 需要对方行动：是（见文末清单）

## 内容

欢迎进场。这个仓库正从"考研英语刷题机"扩展为"个人英语学习系统"，用户的完整规划书在仓库外，你需要知道的全部约定都已经落在仓库里。你是后端主责，我是前端主责，用户负责验收与仲裁。

### 当前基线（develop-v1，提交 0a4f0e8 之后）

- 测试基线：`.venv/Scripts/python -m pytest tests/ -q` → **85 passed, 13 skipped**。你的每个 PR 合并前必须保持全绿。
- 我已把 requirements.txt 的 lxml 从 6.0.0 升到 6.1.1（Python 3.14 Windows 没有 6.0.0 的 wheel，KNOWN_ISSUES KI-3）。如果你的环境是 Python ≤3.13，6.1.1 同样兼容。
- 现状审计在 docs/AUDIT.md。特别注意 §6"不可破坏功能清单"——那是回归红线。

### 你的任务顺序

1. **Issue #1**（先做）：版本化迁移机制 + 迁移 0001/0002。SQL 定义全部在 DATA_MODEL.md §3，逐字实现即可；歧义之处按"旧表不改语义、迁移前必备份、幂等可重跑"三原则裁决，并在回执里说明你的裁决。
2. **Issue #2**：W1 全部 API，端点行为在 API_CONTRACT.md §2–§6。

三个容易踩的坑，审计时发现的，提前告诉你：

- **时区**：旧代码 `services/vocabulary.py` 用 naive `datetime.now()` 写复习时间，但表默认值是 UTC。迁移 0002 里存量 `next_review_at` 必须按本地时区换算成 UTC（DATA_MODEL.md §4.2），新代码禁止 naive 时间。
- **FTS5 trigram**：查询 <3 字符时 MATCH 匹配不到，契约要求 LIKE 回退（API_CONTRACT.md §3），别漏。
- **导入永不丢文件**：解析失败也要先落盘原文件、建 needs_review 资源（A1.5 场景会验）。

### 我这边的状态

我并行开发 Issue #3/#4 的前端页面，直接按 API_CONTRACT 写 api.ts 和页面，用契约里的响应结构做本地 mock。你的 #2 合并后我立刻切到真实接口联调。**合并顺序永远是你先我后**（docs/COLLABORATION.md §3）。

### 需要你行动

1. 环境搭好后跑一次测试，在回执里确认你复现的基线数字（应为 85 passed, 13 skipped）。
2. 阅读五份契约文档 + docs/AUDIT.md + AGENTS.md，有任何契约异议写进回执（标"待用户确认"），不要直接改文档。
3. Issue #1 完成后写回执：迁移编号、对空库/旧库各自的测试结果、你做过的裁决。旧库夹具（test-fixtures/existing-user-database.sqlite）用户还没提供，到位前先用空库路径验证，回执里注明"旧库迁移未验"。
4. 每次收工按 handoff/README.md 的规则写回执并更新索引表。

—— Claude
