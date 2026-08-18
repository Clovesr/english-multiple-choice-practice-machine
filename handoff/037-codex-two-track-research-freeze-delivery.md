# 037 ｜ Codex → Claude ｜ 两轨编号、ACT 草案与后端状态核对交付
- 日期：2026-08-18T02:30:33Z（UTC）
- 关联：handoff/033 §2/§4/§5；docs/SPEC_V2 ACT-01/03/05/06/07/11/19/21/22/23/26
- 起始基线：用户指定 `develop-v1@28e810d8c0ac295d7b7e9c2b464a01cf6db01d1a`
- 最终提交：`4e2d0a2`、`83adf10`、`cf86488`、`15e281c`
- 文件租约：Codex 释放 `docs/ACT_MODEL_DRAFT.md` 与 `docs/FEATURE_STATUS.md`；全程未修改 `frontend/`
- 需要对方行动：是（按 035 编号修订增加阶段归属列，并审 ACT 草案）

## 1. 两轨检查点编号

033 §4 原文无异议，继续作为唯一联合引用文本。033 §2 的显式编号全部保留，并以 035 落档以下编号级修订：

- 增加 `PLN-09（部分）`、`VOC-23（部分）`、`REV-04/15（部分）`；
- EXM 精确为 `EXM-01/03/05/08/09/12/15/16/19/20/22`；
- CUR 精确为 `CUR-05/07（部分）`；
- `APP-01~06` 仍归 V1 核心初段；现有词书冲刺由 `REV-14` 表示，不提前宣称通用 `PLN-11`。

这些修改都有既存表/测试或 028 已认领前端闭环，不增加本批后端范围。

## 2. ACT 统一活动模型草案

`docs/ACT_MODEL_DRAFT.md` 已交付研究冻结候选，只提案、不迁移、不改接口。草案冻结：

1. `activities → activity_versions → attempts → responses → learning_events` 的稳定身份、不可变发布版本、提交快照和事件边界；
2. 单选/判断/填空/完形/拼写/阅读/自评的稳定响应键与评分合同；
3. 自动保存 revision、客户端 attempt UUID 幂等、按篇响应子集锁定、评分失败保留原响应；
4. `practice_sessions/practice_answers/practice_answer_events/practice_unit_submissions` 逐表适配，考试内核继续为权威事实源；
5. 词级 FSRS 保持不变，影子记录只可异步派生并可重建；
6. learning_events v2 补 actor/object version/context/source/补偿事件/outbox，旧事件不原地改写。

## 3. FEATURE_STATUS 后端核对

- APP/LIB/ACT/VOC/REV/DAT/EXT 共 **138/138** 行后端核对非空；每行给出表、测试/脚本或“无对应实现”的证据。
- 036 的“只保留双方差异、暂不改状态”不满足用户本批指令，已由 `15e281c` 追加收敛：状态列与后端核对一致，差异当行写明理由。
- 总计由 `✅37 / 🟡71 / ⬜289` 修正为 **`✅28 / 🟡100 / ⬜269`**；七域分区计数已用脚本复算，397 行总数不变。
- 关键纠正：DAT-07 的 `BackupCreate` 当前只有 `kind`，没有用户备注/取消，因此由 ✅ 降为 🟡；LIB-09/17、VOC-01/06/11/12、REV-01/03 等未达完整 V2 边界的条目同样已降级；ACT 已有领域能力的条目不再误标完全未开始。

## 4. 独立回归

- 后端：`140 passed, 14 skipped`；
- 前端：`53 passed`；
- 前端生产构建：通过（vue-tsc + Vite）；
- `git diff --check`：通过；
- 仅有 KI-5 已登记的 FastAPI/Python 3.14 弃用警告，无失败。

下一步由 Claude 依 033/035 串行增加阶段归属列，并对 ACT 草案给出确认或编号/字段级替代文本；任何建表或 API 变更仍须先进入 DATA_MODEL/API_CONTRACT 合同流程。

—— Codex
