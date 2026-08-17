# 内置离线词典资源

`ecdict-essential.dbpkg` 是从 ECDICT 固定版本筛选出的离线词典包，包含 CET4、
CET6、考研标签词和有 BNC/COCA 频率排名的常用词，随应用发布，运行时以只读
SQLite 方式打开。三个标签只决定内置词书范围，不限制阅读器的离线查词范围。

- 上游：https://github.com/skywind3000/ECDICT
- 固定提交：`bc015ed2e24a7abef49fc6dbbb7fe32c1dadaf8b`
- 原始文件：`ecdict.csv`
- 原始文件 SHA-256：`1a6947e04785db63613a92e14903cdae7954f7e84860b10e68e5c7cbb3f9c3cf`
- 生成包 SHA-256：`e8dfcdd36cbb247f4805471541df86368ecbe83ea7a95ed2caf33d41083d78ea`
- 生成包大小：`19,329,024` 字节
- 去重词条：`57,027`
- 词形：`70,178`
- 标签计数：CET4 `3,849`、CET6 `5,407`、考研（ky）`4,801`
- 许可证：MIT，全文见 `ECDICT-LICENSE.txt`

从固定版本的原始 CSV 重建：

```powershell
.\.venv\Scripts\python.exe tools\build_ecdict_bundle.py `
  path\to\ecdict.csv `
  backend\resources\ecdict-essential.dbpkg
```

构建器会先校验固定源文件 SHA-256，再创建数据库、执行
`PRAGMA quick_check`、压缩数据库并打印最终包哈希与计数。不要把未校验的网络下载
直接替换发布资产。
