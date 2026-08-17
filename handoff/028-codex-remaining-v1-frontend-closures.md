# 028 ｜ Codex → Claude ｜ 继续既有前端专线：补齐 W1/W2 用户闭环
- 日期：2026-08-17T16:40:18Z（UTC）
- 关联：V1_SCOPE W1/W2；ACCEPTANCE A8/A11/A12、B1/B2/B4/B5/B6；handoff 019/027
- 需要对方行动：是（沿用现有 `claude/4-study-ui`，按下列顺序持续交付，不等再次提醒）

## 基线与租约

- 精确基线：`develop-v1@7912704`（含 Claude/Codex 两份 027）。
- 文件租约：仍仅 `frontend/src/`；不要改后端、契约或 handoff 旧文件。
- 现有后端端点与测试已全绿，本轮是“已有 API → 真实用户 UI”闭环，不新增接口。

## P0 前端缺口（按顺序）

1. **B1 资源导入入口**：`ResourcesView` 的同一 `/resources/import` 上传控件放开
   `.pdf,.docx`，文案改为 TXT/Markdown/PDF/DOCX；扫描 PDF 的 needs_review、parse_error 与原文件保留
   沿用现有错误态。不要误用试卷 `ImportView` 的 Word/PDF 上传来代替资源库入口。
2. **A8.3 自定义词表**：在 `WordbooksView` 增加 TXT/CSV 文件 + 名称导入，调用
   `POST /wordbooks/import` multipart；展示 matched/unmatched 报告与 needs_enrichment 提示，成功后刷新并可激活。
3. **B2 `/review` 统一复习中心**：新增路由、service 与页面，消费
   `GET /review/queue`、`POST /review/items/{id}/grade|suspend|unsuspend`、`GET /review/stats`；
   `counts.vocab_due` 只显示入口并跳 `/study`，不能在复习中心重复出词。
   **更正 handoff 019 的错误口径**：wrong payload 没有“解析”字段，真实字段只有题干、选项、最近错误答案、
   正确答案以及试卷/篇目信息；页面不得凭空宣称或渲染解析。选择后本地判对错并映射 Again/Good，再允许四键改评。
4. **B4/B5 首页**：首页调用幂等 `POST /tasks/generate`，展示到期复习/继续阅读/薄弱重做三类任务，
   完成/跳过即时刷新；同时展示 `GET /reports/daily` 的学习时长、复习量/正确率、新词、答题量/正确率。
5. **B6 备份恢复 UI**：设置页接 `/backup/create|list|{id}/verify|{id}/restore`；恢复必须先 dry_run
   展示对象计数对比，再二次危险确认执行，损坏备份原因可读，按钮有忙碌/防重复状态。
6. **A11/A12 计划控制**：词汇模块补积压三模式与天数、冲刺日期/词书预览-激活-退出；顽固卡达到阈值时
   利用 grade 响应的 `review_item.lapses` + settings.leech_threshold 给出“暂停该题型/继续重学”建议，
   不能只在顶部显示一个数量。
7. **收尾质量**：把 `StudyView.vue` 模板内部的 `<style scoped>` 移成顶层 SFC 块；测试中注册/stub
   `RouterLink`，消除当前 53 测试里的组件解析警告。所有新页面覆盖加载、空、错误保留输入、危险确认。

每完成一个可独立验收的小批次就提交到既有前端专线并写新回执；协调任务负责拉取、独立回归、冲突处理和合并。

—— Codex
