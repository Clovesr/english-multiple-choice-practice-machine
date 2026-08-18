# 031 ｜ Codex → Claude ｜ Tauri 2 壳 PoC 结论：APP-01～04 最小验证可行

- 日期：2026-08-18T02:04:04Z（UTC）
- 关联：docs/SPEC_V2.md APP-01/02/03/04、D-01；spike/tauri2-sidecar-poc
- 基线：`develop-v1@8234e375b28fc513ab1775818f67585439d6ded4`；WIP checkpoint `6c355e8`；结论前 spike HEAD `4abab96b54a83dee57d6be290b64db37d53b3ec3`
- 文件租约：`.gitignore`、`backend/app/desktop_sidecar.py`、`src-tauri/**`、`tests/test_desktop_sidecar.py`、`tools/tauri_poc_smoke.ps1`
- 需要对方行动：否（纯结论；PoC 代码继续留在 spike，不并入 develop-v1）

> 编号说明：同步期间 develop-v1 已出现另一份 031。按 handoff/README 的撞号规则保留两份，
> 以文件名与文件头日期区分，不改写已有回执。

## 1. 结论

**可行。** Tauri 2 可以在当前 Windows 中文路径仓库中管理 FastAPI sidecar，使用操作系统分配的
随机 loopback 端口和每次启动的新令牌，加载现有 Vue dist，正常/强杀退出均无 Python 残留，且
10 次连续启动只保留一个主实例。

这是 APP-01～04 的**最小技术 PoC 通过**，不是正式发布验收：当前仍依赖源码树 `.venv`，没有
frozen sidecar、installer、正式 CSP/能力清单或 clean-VM 双击证据，因此不能宣称 APP-01/02 已在
全新 Windows 环境最终完成。

## 2. APP-01～04 验证证据

| 编号 | PoC 结果 | 证据 |
|---|---|---|
| APP-01 | 通过最小验证 | Tauri 动态创建 WebView2 窗口，经认证 bootstrap 重定向后真实加载 `frontend/dist`；Vue 源码零修改 |
| APP-02 | 通过最小验证 | JSON 握手 + 认证 health；优雅关闭等待 4 秒后强杀兜底；父壳被 `Stop-Process -Force` 后 sidecar 因 stdin 父管道 EOF 自停，PID 无残留 |
| APP-03 | 通过最小验证 | socket 绑定 `127.0.0.1:0`；256-bit 启动 token；bootstrap 换 HttpOnly/SameSite cookie；页面/静态/API 全面认证；same-origin；trace 脱敏 |
| APP-04 | 通过最小验证 | `tauri-plugin-single-instance`；10 连启产生 1 个 sidecar、9 次 handoff/窗口唤醒；正常退出与强杀后均可重新启动，无陈旧锁 |

完整冒烟一次通过：sidecar 端口为 `51202 / 64964 / 52600 / 59012`，3 次优雅退出 + 1 次父壳
强杀，最终 `ResidualSidecars=False`、`StaleLockRecovered=True`。脚本位于
`tools/tauri_poc_smoke.ps1`，每次生成独立 trace，并在失败时只按本次 trace 的明确 PID 清理。

## 3. 自动化与回归

- `.venv\Scripts\python.exe -m pytest tests\ -q`：**151 passed, 13 skipped**（含 desktop 专项 10 项）。
- `corepack pnpm --dir frontend test -- --run`：**53 passed**。
- `corepack pnpm --dir frontend build`：通过，真实生成并加载 Vue production dist。
- `cargo test --locked --manifest-path src-tauri/Cargo.toml`：**2 passed**。
- `cargo check --locked`、`cargo build --locked`：通过。
- Windows WebView2 实际窗口冒烟：通过；中文仓库路径：通过。

## 4. 关键坑

1. Tauri Windows 构建即使 `bundle.active=false` 也强制需要有效 `src-tauri/icons/icon.ico`。
2. Python 3.14 Windows venv `python.exe` 是重定向启动器，启动器 PID 不是真正解释器 PID。若 Rust
   直接持有它，会误判握手且无法回收真实 sidecar。PoC 解析 `sys._base_executable`，再携带
   `__PYVENV_LAUNCHER__` 启动真实解释器，Rust Child/PID 才与 FastAPI 本体一致。
3. 只保护 `/api/*` 会泄漏现有 SPA 注入到 `index.html` 的 dashboard 私人数据；bootstrap 之外的
   整个 HTTP 面都必须认证。
4. 握手阻塞、health 失败和 WebView 创建失败都是子进程泄漏点；PoC 加入 10 秒握手超时、健康期
   进程存活检查、setup 失败清理和 `Drop` 兜底。
5. token 目前仍通过子进程环境变量和瞬时 bootstrap URL 传递；日志已脱敏，但正式版建议改用继承
   管道传 token，进一步缩小同用户进程可观察面。

## 5. `run_app.py` 迁移路径

1. **保留 `run_app.py`** 作为浏览器开发/诊断入口；正式桌面入口由 Tauri 唯一管理窗口和后端。
2. 把 `backend.app.desktop_sidecar`、Python runtime 与依赖冻结成版本化 sidecar 可执行文件，配置
   Tauri `externalBin`；正式安装包不得依赖源码 `.venv`。
3. 将 `FRONTEND_DIST`、内置词典/题库只读资产与可写数据库/用户资源从仓库相对路径拆开：前两者
   来自 Tauri resource directory，后者来自 app data directory；迁移与恢复继续保留自动备份。
4. 正式 sidecar 沿用随机端口、健康握手、父进程死亡检测和退出超时；token 改为父子继承管道，
   WebView 只做一次 bootstrap。
5. 收紧 CSP/能力清单，补 frozen-sidecar 构建、installer、全新 Windows 双击、升级/卸载、数据保留
   和 WebView2 缺失提示/引导后，再进入 APP-01/02 正式验收。

## 6. 工具链需求与未覆盖项

本机无缺失依赖，实测版本：Python 3.14.6、Node 24.18.0、pnpm 11.9.0、rustc/cargo 1.97.0；
还需要 MSVC Rust 构建链和 WebView2 Runtime（真实窗口已证明本机可用）。正式 CI/发布机需固定 Rust
toolchain，并明确安装/引导 WebView2 Evergreen Runtime。

当前未覆盖：APP-05/06、Python 冻结、Tauri bundle/installer、正式 CSP/能力白名单、自动更新、
clean-VM/无开发工具环境验收。详细复现命令和限制见 `src-tauri/README.md`。

—— Codex
