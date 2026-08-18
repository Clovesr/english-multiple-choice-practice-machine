# Tauri 2 sidecar PoC

结论：Tauri 2 + FastAPI sidecar 方案在当前 Windows/中文路径仓库中可行。这个 spike
完成 APP-01～APP-04 的最小技术验证，但仍是开发态 PoC，不是安装包或全新机器发布验收。

## 已验证边界

- APP-01：Tauri 创建独立 WebView2 窗口；现有 `frontend/dist` 由 FastAPI 托管并在窗口内加载，
  Vue 业务代码无需为 PoC 修改。
- APP-02：Tauri 启动并持有真实 Python/FastAPI 进程，等待 JSON 握手和认证健康检查；正常退出先
  请求 uvicorn 优雅关闭，4 秒后强杀兜底。父壳被强杀时，继承的 stdin 管道关闭，sidecar 自停。
- APP-03：操作系统分配 `127.0.0.1:0` 随机端口；每次启动生成 256-bit token。bootstrap 将
  token 换成 HttpOnly/SameSite cookie，之后页面、静态文件和 API 都要求认证；API/关停同时检查
  same-origin，trace 不记录明文 token。
- APP-04：`tauri-plugin-single-instance` 拦截后续启动并唤醒已有窗口；正常退出和强杀后都能重新
  建立主实例，不遗留陈旧锁。

完整冒烟会执行一次 10 连启、一次正常重启、一次强杀父壳和一次强杀后的陈旧锁恢复，并逐个检查
sidecar PID 无残留。

## 本地验证

需要 Python 3.14 venv、Node/corepack/pnpm、Rust MSVC 工具链和 WebView2 Runtime：

```powershell
corepack pnpm --dir frontend build
.venv\Scripts\python.exe -m pytest tests\test_desktop_sidecar.py -q
cargo test --locked --manifest-path src-tauri/Cargo.toml
cargo build --locked --manifest-path src-tauri/Cargo.toml
powershell -NoProfile -ExecutionPolicy Bypass -File tools/tauri_poc_smoke.ps1
```

本次验证环境为 Python 3.14.6、Node 24.18.0、pnpm 11.9.0、rustc/cargo 1.97.0；真实 WebView2
冒烟通过。`frontend/dist` 和 `src-tauri/target` 都是本地产物，不提交仓库。

## 已发现的坑

1. Tauri Windows 构建即使不打 bundle 也要求有效的 `src-tauri/icons/icon.ico`。
2. Python 3.14 Windows venv 的 `python.exe` 是重定向启动器：启动器 PID 与真正解释器 PID 不同。
   直接把它当 Child 会误判握手并无法可靠回收。PoC 先解析 `sys._base_executable`，再携带
   `__PYVENV_LAUNCHER__` 启动真实解释器，使 Rust 持有 FastAPI 本体。
3. 仅保护 `/api/*` 不够：现有 `index.html` 会注入私人 dashboard 数据，因此 bootstrap 之外的
   整个 HTTP 面都必须认证。
4. sidecar 握手和窗口创建都是失败泄漏点；PoC 对握手设 10 秒超时，并在握手、健康检查、窗口
   创建失败及 panic/drop 路径统一回收子进程。

## 从 `run_app.py` 迁移到正式客户端

1. 保留 `run_app.py` 作为浏览器开发入口；正式入口改由 Tauri 唯一负责窗口和后端生命周期。
2. 将 `backend.app.desktop_sidecar` 及 Python 依赖冻结为版本化 sidecar 可执行文件，随 Tauri
   `externalBin` 打包；正式包不能依赖源码树中的 `.venv`。
3. 把 `FRONTEND_DIST`、词典/题库只读资源和可写数据目录从“仓库相对路径”拆成 Tauri resource
   directory 与 app data directory，并保留迁移前备份语义。
4. 生产侧继续使用随机 loopback 端口、短期 token、健康握手和父进程死亡检测；令牌最好改为继承
   管道传递，避免放进环境变量和瞬时 bootstrap URL。
5. 启用最小 CSP/能力清单、构建 frozen-sidecar、installer 与 clean-VM 双击/升级/卸载测试后，
   才能宣告 APP-01/APP-02 的正式验收完成。

当前明确未做：APP-05/06、安装器、自动更新、冻结 Python runtime、正式 CSP/能力收敛、全新 Windows
虚拟机验收。
