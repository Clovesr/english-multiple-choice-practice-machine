# 测试夹具（规划书 16.2）

真实文件回归的统一素材库。**用真实文件测试，不用理想化数据。**

| 文件 | 用途 | 状态 |
|---|---|---|
| sample-article.txt | UTF-8 英文文章导入（A1） | ✅ |
| sample-article-gbk.txt | GBK 编码 + 中英混排导入（A1.1、G5） | ✅ |
| sample-note.md | Markdown 标题/列表/代码块切分（A1.2） | ✅ |
| sample-broken.txt | 伪装 txt 的二进制，解析失败路径（A1.5） | ✅ |
| sample-document.docx | DOCX 导入（B1） | ✅ |
| sample-subtitle.vtt | 字幕导入（后续里程碑） | ✅ |
| corrupt-backup.zip | 损坏备份恢复拒绝（B6.4） | ✅ |
| sample-bank.esq | ESQ 回归 → 直接用 `examples/demo-bank.esq` | 引用 |
| existing-user-database.sqlite | 旧版仿真库，迁移测试（A6）。由 main@ffe47c6 旧代码初始化并填充（用户未实际用过旧版，无真实数据）。被 .gitignore 排除，只存本地；重新生成方法见 handoff/009 | ✅ 已生成（两处克隆各一份） |
| study-api-response.json | `/api/study/session`、grade、settings、overview 的真实 TestClient 响应夹具，供前端联调与回执核对 | ✅ |
| w2-api-response.json | W2 错题复习、任务、日报、知识点与备份接口的真实 TestClient 响应夹具 | ✅ |
| sample-text.pdf / sample-scanned.pdf | PDF 导入（B1），W2 前补充真实文件 | ⬜ |
| sample-book.epub / sample-audio.mp3 | 后续里程碑 | ⬜ |

## 使用规则

- 夹具只读：测试代码不得修改夹具本身，需要写入时先拷贝到临时目录。
- 中文路径场景：测试时将夹具复制到含中文的临时路径下执行（G5）。
- 旧版夹具库如需重建：临时检出 main@ffe47c6 → 用其 `initialize_database()` 建库 → 按 handoff/009 的数据清单填充。
