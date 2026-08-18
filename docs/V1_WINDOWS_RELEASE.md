# V1 Windows 发布与回滚手册

> V1 沿用现有的 Windows 本地启动方式。规划书明确将 Tauri/轻量桌面封装放到核心功能稳定之后评估，本版本不临时更换运行架构。

## 发布包构建

发布负责人在干净的 `develop-v1` 或 `release/v1-core` 提交上执行：

```powershell
.\tools\build_v1_windows_release.ps1 -Version v1-core
```

脚本先执行 `tools/v1_release_preflight.ps1`，只有依赖检查、后端全量、前端全量、类型检查和 production build 全部通过才生成 ZIP。输出位于：

```text
outputs/releases/
├── english-practice-machine-v1-core-windows-<commit>.zip
└── english-practice-machine-v1-core-windows-<commit>.zip.sha256
```

ZIP 只收录 Git 已跟踪文件与本次 production build，不收录 `.venv`、`node_modules`、`backend/data`、数据库或 `.env`。构建脚本会重新打开 ZIP，检查启动文件、前端产物和许可证是否存在，并拒绝包含私有数据的包。

## 安装与启动

已验证环境：Windows 10/11、Python 3.12、Node.js 24.x（含 Corepack）、pnpm 11.x。

1. 校验下载文件的 SHA-256 与同名 `.sha256` 文件一致。
2. 将 ZIP 解压到一个可写目录；不要覆盖正在使用的旧版本目录。
3. 在解压后的根目录打开 PowerShell。
4. 首次安装运行 `setup.ps1`；完成后运行 `start.ps1`。
5. 浏览器打开 `http://127.0.0.1:8765`。服务仅监听本机回环地址。

若 PowerShell 阻止本次会话执行脚本，可仅为当前进程临时放开：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

基础刷题、资源阅读、离线词典和复习不要求联网；安装 Python/Node 依赖时可能需要访问包源。

## 数据位置与升级前检查

默认用户数据位于每个版本目录的 `backend/data/`。升级前必须：

1. 完全退出应用，确认 8765 端口已释放。
2. 在旧版本中创建手动备份并执行校验；若旧版本没有可用的备份页面，则关闭应用后复制整个 `backend/data/` 目录。
3. 记录备份文件的 checksum、应用版本和 schema 版本。
4. 将新版本解压到新目录，不直接覆盖旧程序目录。
5. 先保留旧版本目录和升级前备份，再启动新版本完成迁移。

正式备份包记录 `app_version`、`schema_version`、数据库 checksum、附件 checksum 和关键对象计数。V1 恢复接口只允许恢复到相同 schema 版本；版本不兼容时必须停止，不得强行替换数据库。

## 升级验证

新版本首次启动后至少核对：

- 首页题库、资源、词汇和复习计数与升级前一致。
- 随机打开一篇题目、一篇资源和一个词条，关联内容可访问。
- 学习概览的到期量、今日完成量和词书计划未被重置。
- 备份列表可见，最近备份校验通过。
- 完成一次评分后退出并重启，队列与到期时间保持一致。

迁移会在写入前创建 pre-migration 快照；重复启动不得重复迁移。任何迁移错误都视为 P0，停止继续使用新版本并按下节回滚。

## 回滚

禁止让旧版本程序直接打开已经被新版本迁移过的数据库。正确回滚方式：

1. 完全退出新版本，不再写入数据。
2. 保存新版本现场副本，供问题定位；不要删除失败日志、恢复日志或 pre-migration 快照。
3. 回到保留的旧版本程序目录。
4. 用升级前校验通过的备份恢复旧版本的 `backend/data/`；若使用目录副本，先将当前目录另存为故障现场，再整体替换。
5. 启动旧版本，核对题库、资源、词汇、复习历史和练习记录。
6. 将失败提交、错误信息、备份 checksum、schema 版本和可复现步骤登记为 P0/P1；修复并通过完整发布闸门前不得再次升级。

如果新版本已产生必须保留的新学习数据，不应直接回滚并丢弃这些数据。先冻结两个数据目录，由集成负责人设计经验证的前向修复或数据导出/合并方案。

## 发布 Go / No-Go

只有以下条件全部满足才能把 ZIP 标记为正式 V1：

- `ACCEPTANCE.md` 的 W1/W2 场景全部有当前提交的证据。
- `KNOWN_ISSUES.md` 未修 P0/P1 为 0。
- `tools/v1_release_preflight.ps1` 在干净工作树通过。
- 新装空数据首启、完全退出重启、断网核心闭环和损坏备份拒绝均通过。
- ZIP 内容审计与 SHA-256 校验通过。
- 回滚演练使用升级前备份成功恢复。
- 达到 `ACCEPTANCE.md` B7 要求的真实使用观察期。

任一条件缺失时，生成的包只能作为候选包，不得宣称正式发布。
