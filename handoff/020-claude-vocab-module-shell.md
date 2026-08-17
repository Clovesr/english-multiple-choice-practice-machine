# 020 ｜ Claude → Codex ｜ 词汇模块统一外壳重构（用户二次反馈）
- 日期：2026-08-17T18:10:00+00:00（UTC）
- 关联：018 的合并被用户指出"三个页面塞一起，不是一个页面分三个模块"；本次架构性修正
- 需要对方行动：否（纯前端通报）

## 改动

1. 新增 views/VocabModuleView.vue 作为路由父级外壳：统一大标题「词汇」、分段切换控件、**常驻学习数据条**（今日新学/复习/待复习/积压/连续天数/保持率，via provide/inject 供子页刷新）。
2. 路由嵌套：父 /vocab（redirect /study），子路由保持绝对路径 /study、/wordbooks、/vocabulary——历史深链接零破坏；移除根级重复 /vocabulary 路由。
3. 三个子页剥离为纯内容窗格（vocab-pane）：删除各自的 page 壳、页头 h1、独立 VocabTabs（组件已删除）；StudyView 的 overview 数据条上移至外壳，评分后经 inject 刷新。
4. 验证：三标签切换下 h1/数据条/页宽全部稳定，仅内容区变化；38 前端测试全绿、构建通过。

## 遗留（V1.1 清理项）

VocabularyView 内不可达的旧 reviewMode 代码块（入口已移除）待删；`.vocabulary-page` 三栏布局在 1240 宽度下可再调优。

—— Claude
