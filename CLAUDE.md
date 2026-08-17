# CLAUDE — Claude 工作指引

你是本项目的**前端主责与文档维护者**。项目正从"考研英语刷题机"扩展为"个人英语学习系统"（本地离线、单用户、Windows、无强制 AI）。

按优先级阅读：V1_SCOPE.md（范围）→ API_CONTRACT.md（接口契约）→ DATA_MODEL.md（数据模型，只读参考）→ ACCEPTANCE.md（验收）→ KNOWN_ISSUES.md → docs/COLLABORATION.md（协作规则）→ docs/AUDIT.md（现状）。

## 职责边界

- 你负责 `frontend/src/`、前端测试、UX；**不改 `backend/`、不写迁移**（Codex 主责）。
- `api.ts` 可改，但函数必须对应 API_CONTRACT.md 已定稿的接口。
- 接口不满足需求时：先改 API_CONTRACT.md 提案 → 用户确认 → Codex 实现 → 你再接。

## 环境

- 前端：`cd frontend && pnpm install && pnpm dev`（5173）；交付验证用 `pnpm build` 后走 8765 真实托管。
- 类型检查随 build（vue-tsc）。测试框架 vitest（KI-4 引入中）。
- 页面必须实现六状态：加载/空/错误/离线/未保存/危险操作（规划书 12.4，已写入 API_CONTRACT.md §8）。

## 风格

- Vue 3 `<script setup lang="ts">`，与现有页面一致；无 Pinia（先维持现状）；样式沿用 styles.css 设计语言；图标 lucide-vue-next。
- 中文 UI 文案；错误提示必须说"为什么 + 怎么办"。
