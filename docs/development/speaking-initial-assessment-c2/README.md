# Speaking 初始评估 C2 验收

在 `backend` 目录使用项目 Python 环境运行 `python -m scripts.verify_speaking_assessment_c2`。脚本将启动 Uvicorn、Vite 和带独立用户目录的 Headless Chrome，并在临时 SQLite 中验证登记记录及其他任务状态。需要已安装的前端依赖和 Chrome，可通过 `CHROME_BIN` 指定浏览器路径。

- [浏览器报告](browser.json)记录 `getUserMedia` 返回的音轨、`MediaRecorder` 输出的字节数、IndexedDB 键和记录、登记请求字段、回放进度和双主题布局测量。
- [后端报告](backend.json)记录 Uvicorn 的 HTTP 状态、隔离数据库中的任务和证据引用，以及正式 SQLite 的前后哈希。
- [桌面本地录音](desktop-local-recording.png)、[390px 暗色主题恢复](mobile-restored-noir.png)、[已登记但本机缺少音频](desktop-missing-local-audio.png)展示三个实际界面状态。

Chrome 虚拟麦克风提供输入，实际录音由浏览器 `MediaRecorder` 生成。验收不覆盖物理麦克风、跨运行故障注入、权限矩阵和完整移动触控。普通 Speaking 回归覆盖打开工作区和加载原题库。
