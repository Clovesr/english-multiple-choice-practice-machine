# 双模型协作规则（Codex × Claude）

> 依据规划书第 15 章，结合实际情况调整：集成负责人为用户本人（不写代码），因此共享文件的修改规则按下文"共享文件"节执行。

## 1. 文件所有权

| 区域 | 主责 | 说明 |
|---|---|---|
| `backend/`、`tests/`（后端）、数据库迁移、`requirements.txt` | **Codex** | 数据库变更只能由 Codex 按 DATA_MODEL.md 实现 |
| `frontend/src/`、前端测试、UX 文档 | **Claude** | Claude 不直接改表、不改后端业务代码 |
| `frontend/src/api.ts` | Claude | 但新增/变更的接口必须已在 API_CONTRACT.md 定稿 |
| `backend/app/schemas.py` | Codex | 同上，契约先行 |
| 五份契约文档、README、启动脚本 | 双方可提议 | 修改必须单独提交并在 PR 描述中醒目声明，由用户确认后合并 |

**禁止**：两个模型在同一个分支周期内修改同一文件。发现必须交叉修改时，停下来先改契约文档。

## 2. 分支与提交

```
main            稳定版本（只接受 develop-v1 的发布合并）
└─ develop-v1   每日集成分支
   ├─ codex/<issue编号>-<slug>    后端功能分支
   ├─ claude/<issue编号>-<slug>   前端功能分支
   └─ release/v1-core             发布准备
```

- 一个功能 = 一个 Issue = 一个分支 = 一个 PR（目标 develop-v1）。
- PR 必须附：对应 ACCEPTANCE.md 场景编号、执行结果（通过/失败逐条列出）、数据变化说明、回滚方式。
- 提交信息中文，格式 `feat|fix|docs|test|chore: 描述 (#issue)`。
- 涉及迁移的 PR 必须包含：迁移脚本 + 幂等测试（重复执行不出错）+ 对 test-fixtures 旧库的升级测试。

## 3. 每日节奏（规划书 15.4）

1. **开发前**：拉取 develop-v1；读当日 Issue；复述验收标准到 PR 描述里。
2. **开发中**：小步提交；先写测试或与测试同步；禁止顺手加范围外功能（发现需求缺口 → 写入 KNOWN_ISSUES 延后需求）。
3. **集成顺序**：Codex 后端 PR 先合并（契约实现落地）→ Claude 拉取 develop-v1 再合并前端 PR。接口有变化时必须先更新 API_CONTRACT.md。
4. **自动检查**（每个 PR 本地必跑）：后端 `pytest tests/ -q`；前端 `pnpm build`（含 vue-tsc 类型检查）+ `pnpm test`（vitest 引入后）。
5. **人工验收**：用户用真实文件走当日 ACCEPTANCE 场景，在 Issue 里记录通过/失败。

## 4. 冲突与红线（规划书 15.6）

- 接口变更：先改 API_CONTRACT.md → 用户确认 → 再写代码。
- 数据库变更：先改 DATA_MODEL.md → 用户确认 → Codex 写迁移。
- 2026-08-26（第 10 天）起 V1 功能冻结，新需求进 V1.1。
- 出现 P0/P1：双方立即停功能开发，先修复、先保数据。
- 原始资源文件只读：任何代码不得改写用户导入的原文件。

## 5. 联调基准

- 后端：`.venv/Scripts/python run_app.py`（127.0.0.1:8765）。
- 前端开发：`cd frontend && pnpm dev`（5173，代理已配 CORS）。
- 前端交付验证：`pnpm build` 后走 8765 的真实托管路径（对应 ACCEPTANCE 的"真实构建"要求）。
- 测试夹具统一放 `test-fixtures/`（见其 README；真实旧库文件不入 git）。
