# Writing D4 集成验收手册

状态为待执行。用例涉及的作文和账号均为测试数据。此次工具编写没有产生运行结果。

## 环境与数据边界

- 脚本以当前工作区代码启动独立的回环 Uvicorn。各后端用例使用临时目录中的 SQLite，退出时清理。脚本不接收数据库路径。浏览器脚本也启动独立 Vite，并显式设置测试 API 地址。
- 测试应用把模型依赖替换为离线 FakeModelProvider。激活模型分支只对该测试应用生效。浏览器测试使用模型关闭模式，浏览器路由阻断非回环 HTTP 请求。
- 不需要读取正式库版本或文件。降级场景使用临时库。验收复用应用的 SQLite 外键初始化路径。

## 执行命令

以下为 PowerShell 命令。Python 使用仓库根目录的虚拟环境与后端开发依赖。Node 使用支持原生 TypeScript 擦除和同步模块解析钩子的版本，例如 Node 22.18 或更新版本。

    Set-Location 'D:\Projects\VerbaTrail\backend'
    ..\.venv\Scripts\python.exe -m pytest -v -s tests/test_writing_d4_migrations.py
    ..\.venv\Scripts\python.exe -m pytest -v tests/test_writing_d4_http.py
    ..\.venv\Scripts\python.exe -m pytest -v tests/test_assessment_runs.py::test_migrate_from_0009

    Set-Location '..\frontend'
    node --test tests/assessment-writing.test.mjs
    node --test

浏览器验收需要已安装的前端依赖、Python Playwright 和兼容的 Chromium。可以复用本机 Chrome，也可以清除 CHROME_BIN 并用 Playwright 自带的 Chromium。

    Set-Location 'D:\Projects\VerbaTrail\backend'
    # 只在当前虚拟环境缺少 Playwright 时安装
    ..\.venv\Scripts\python.exe -m pip install playwright
    $env:CHROME_BIN = 'C:\Program Files\Google\Chrome\Application\chrome.exe'
    ..\.venv\Scripts\python.exe -m scripts.verify_writing_d4_browser

若使用 Playwright 自带的 Chromium，执行以下命令后运行浏览器脚本。

    Remove-Item Env:CHROME_BIN -ErrorAction SilentlyContinue
    ..\.venv\Scripts\python.exe -m playwright install chromium

## 验收场景

| 层 | 断言 |
| --- | --- |
| SQLite 迁移 | 0013→0014→0015→0016 是唯一线性链。每一步核对 D4 ORM 的列、类型、默认值、约束和索引。额外比对 CHECK 和 SQLite 部分索引谓词。两个独立连接均验证外键已启用。升级保留 0013 原文和摘要。 |
| SQLite 约束 | 数据库直接拒绝借用其他作文的初始诊断、将训练关联到其他作文的诊断、重复阶段、训练、修订、迁移任务与正式作答。相同学习者和材料的首次派发互斥，允许另一个任务记录重复曝光。 |
| 旧结构识别 | 在临时库保留原 revision stamp，逐个模拟 0014 复合外键列改名、0015 关联索引缺失、0016 部分唯一索引缺失。重跑同一 revision 后检查实际结构，报告差异。三个场景采用合成样例。 |
| 降级影响 | 在有数据的临时库逐级降级。输出每次删除的表及记录数。0016 删除独立任务和作答，0015 删除训练和修订，0014 删除候选诊断和追问。D1 正文及独立模型审计保留，降级会丢失诊断到审计的链接。 |
| HTTP 链路 | 写作、诊断、追问、练习、修订及迁移完整绑定。验证 learner 和 run 隔离、正式文本 SHA-256、UTF-16 引文、版本竞争、相同意图重放、不同请求冲突和服务重启恢复。测试跨 run 的材料并发派发与重复曝光。 |
| 故障注入 | Fake 模拟超时、非法 JSON、跨作文证据 ID、合法 ID 加伪造引文、仅用追问回答支持假设、预算预检、模型预算拒绝和使用量超额。断言结果来源、审计状态、已报告用量、已计费用量和挂起预留。 |
| 中断与迟到响应 | Fake 调用挂起后并发重试，检查只有一次预留和调用。分别终止 Uvicorn 后重启、先恢复再放行迟到响应。测试时钟将恢复期限推进十分钟。迟到审计可关联到原决策，恢复结果保持原样。 |
| 前端状态 | 验证待确认请求快照、隔离键、旧缓存迁移、坏缓存防护、存储失败时的导航保护、包含补充平面及组合字符的 UTF-16 引文。 |
| Chromium | 草稿保存与刷新、切换评估、多标签页版本冲突、请求在提交前失败和提交后丢失、训练修订和迁移刷新恢复、迁移界面不展示训练答案或诊断假设、存储配额错误提示与防误提交。 |

## 记录与限制

- 执行后记录各套件的命令、用例数量、失败原因、三个结构差异及逐级降级输出。浏览器用例以断言验证，默认不保存截图、录像或个人数据。
- 旧版本样例用当前迁移和合成差异构造。历史部署结构、已损坏数据和回填策略需使用脱敏副本逐一核对。
- 当前用例范围为单节点 SQLite 与 Chromium。生产多 worker、其他数据库、跨浏览器与移动浏览器行为需要后续覆盖。重复曝光只证明站内派发记录。
- 离线 Fake 验证协议、持久化和故障分支。真实供应商的传输超时、分词和用量报告差异、模型引文与论证之间的语义关系需要独立验证。上述测试只核对文本证据和评价状态。
