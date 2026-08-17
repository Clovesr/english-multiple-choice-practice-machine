# ACCEPTANCE — 验收标准（唯一事实来源）

> 五份契约文档之一。每个功能的"完成"以本文件场景全部通过为准，验收在**真实构建**（`run_app.py` 或打包产物）上用**真实文件**执行，不接受只在 dev server 或 mock 数据上的演示。PR 描述必须引用对应场景编号并附结果。

## 通用门槛（每个新功能都要过）

- G1 重启保持：操作后完全退出应用重启，数据与状态不丢。
- G2 空数据：新装环境打开页面，有解释和下一步引导，不白屏不报错。
- G3 错误路径：制造一次失败（坏文件/断网/非法输入），有可读提示，用户输入不丢。
- G4 回归：docs/AUDIT.md §6 不可破坏功能清单抽查通过，`pytest tests/ -q` 全绿。
- G5 中文环境：中文路径、中文文件名、中英混排内容不出错。

## W1 场景

### A1 TXT/Markdown 导入（Codex+Claude）
1. 导入一个含中文路径与中文文件名的 .txt（GBK 编码）→ 成功，标题默认取文件名，段落切分正确。
2. 导入 .md（含标题层级、列表、代码块）→ heading 段与 paragraph 段区分正确。
3. 粘贴纯文本创建 note → 成功。
4. 重复导入同一文件 → 409 提示已存在并可跳转既有资源，不产生重复。
5. 导入一个伪装 .txt 的二进制文件 → 资源落库 status=needs_review，原文件保留，parse_error 可读，列表可见可删除。
6. G1：重启后资源仍在，segment_count 一致。

### A2 资源列表与搜索
1. 列表按更新时间排序，状态筛选（inbox/active/archived）生效。
2. /api/search 搜英文词（≥3 字符）命中正文，snippet 高亮，点击跳到阅读器对应段落。
3. 搜二字中文词（LIKE 回退）能命中。
4. POST /api/search/rebuild 后结果一致（索引可重建）。

### A3 阅读器与进度
1. 打开资源，滚动到中部，退出应用重启 → 精确恢复到上次位置（G1）。
2. 阅读 30 秒以上，resource_progress.total_reading_ms 增长。
3. 空资源（0 段落，needs_review）打开 → 给出"解析失败原因 + 重新导入"引导，不崩。

### A4 选词收藏
1. 阅读器中选中一个词收藏 → 词条创建，语境句、resource_id、segment_id 正确。
2. 同一个词在第二篇资源再次收藏 → 合并到同一词条，encounter_count+1，两条语境都在，词条详情能跳回两处原文。
3. 收藏即出现在复习队列（state=new）。

### A5 FSRS 复习闭环
1. /review 队列出现到期与新卡，四键评分（Again/Hard/Good/Easy）后卡片离队，next_due_at 合理（Again 为分钟级，Easy 为多天）。
2. 评分写入 review_logs：state/due/stability/difficulty 前后值完整。
3. 同一卡 60 秒内重复评分（模拟前端重试）→ 不产生第二条 log。
4. 暂停卡片不再出现在队列；恢复后回来。
5. G1：评分后重启，队列计数与 due 时间不变。

### A6 存量词汇迁移
1. 用真实旧库（test-fixtures/existing-user-database.sqlite）启动 → 迁移自动执行，迁移前快照出现在 backups/pre-migration/。
2. 每个旧词条都有 review_item；已复习过的词 due_at 与旧 next_review_at 换算一致（本地→UTC）。
3. vocabulary_reviews 旧历史原样保留。
4. 旧接口 POST /api/vocabulary/{id}/review 仍工作（映射到 FSRS）。
5. 对同一旧库重复启动 → 迁移不重复执行，无重复 review_items。

### A7 W1 端到端（用户亲验）
导入一篇真实英语文章 → 阅读并收藏 3 个词 → /review 完成含这 3 词的复习 → 重启 → 全部状态保持。全程断网可完成。

## W2 场景

### B1 PDF/DOCX 导入
1. 文本型 PDF 与 DOCX 各导入一份真实文件，段落基本正确。
2. 扫描版 PDF（无文本层）→ needs_review + 原文件保留 + 原因提示（V1 不做 OCR）。

### B2 错题进复习中心
1. 练习答错一题 → wrong_question review_item 出现；复习卡显示题干与选项，可重做判分。
2. 同题再错 → 不产生重复 item，lapses/due 更新。
3. 错题在 /review 与 /wrong 两处数据一致。

### B3 知识点与掌握度
1. 给 5 道题挂知识点，答题后 mastery_states 更新，evidence_count 增长。
2. 删除课程不影响 skills 与掌握度（规划书 6.2 DoD）。

### B4 今日任务
1. 生成今日任务（到期复习/继续阅读/薄弱重做），重复生成不重复（幂等）。
2. 次日未完成任务按规则顺延为 carried。
3. 完成任务后首页计数即时更新。

### B5 日报
1. 报告数字与当日 learning_events 逐项可对账（规划书 6.13 DoD）。
2. 删除一条错误事件后重算，报告变化正确。

### B6 备份与恢复
1. 手动备份 → 文件生成、checksum 记录、verify 通过。
2. 空环境恢复该备份 → 资源/词条/复习项/练习记录对象计数与源库一致，抽查关联正确。
3. 恢复中途强制杀进程 → 原库完好（恢复前快照存在）。
4. 损坏备份（test-fixtures/corrupt-backup.zip）恢复 → 拒绝并给出原因，不碰现库。

### B7 V1 发布闸门（08-30）
- W1/W2 全场景通过；G4 回归全绿；KNOWN_ISSUES 无 P0/P1。
- 新装环境（空数据目录）首启正常，引导可用。
- 用户真实使用 ≥3 天无数据丢失。
