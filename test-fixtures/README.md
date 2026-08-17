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
| existing-user-database.sqlite | **用户真实旧库副本**，迁移测试（A6）。被 .gitignore 排除，只存本地，不入 git | ⬜ 待用户提供 |
| sample-text.pdf / sample-scanned.pdf | PDF 导入（B1），W2 前补充真实文件 | ⬜ |
| sample-book.epub / sample-audio.mp3 | 后续里程碑 | ⬜ |

## 使用规则

- 夹具只读：测试代码不得修改夹具本身，需要写入时先拷贝到临时目录。
- 中文路径场景：测试时将夹具复制到含中文的临时路径下执行（G5）。
- 真实旧库：从你日常使用的旧克隆里复制 `backend/data/question_bank.db` 到本目录并改名 `existing-user-database.sqlite`。
