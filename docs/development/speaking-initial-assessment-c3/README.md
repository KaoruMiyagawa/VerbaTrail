# Speaking 初始评估 C3 验收

在 `backend` 目录使用项目 Python 环境运行 `python -m scripts.verify_speaking_assessment_c3`。脚本启动 Uvicorn、Vite 和两份独立用户目录的 Headless Chrome，并将每次执行写入独立的时间戳目录。需先安装项目依赖，可用 `CHROME_BIN` 指定 Chrome 路径。

浏览器报告 `browser.json` 包含四类风险的独立结果、Chrome 请求与故障时间线、MediaRecorder 输出、IndexedDB 状态、触控与键盘检查。后端报告 `backend.json` 包含 Uvicorn 实际收到的请求、SQLite 行及正式数据库前后哈希。登记请求、响应和设备记录使用隔离环境中的测试数据。

## 最终复测

四类风险均通过。[浏览器报告](20261117T034049382604Z/browser.json)和[后端报告](20261117T034049382604Z/backend.json)记录了全部断言。[Premium Academic](20261117T034049382604Z/touch-premium-restored.png)和[Noir & Champagne](20261117T034049382604Z/touch-noir-restored.png)截图来自触控登记后刷新恢复的页面。两套主题中，「停止录音」按钮聚焦后的下沿均在约 824px，位于 844px 视口内。

## 故障注入

- Chrome 在 Uvicorn 返回 200 后丢弃登记响应，并丢弃自动核对的 GET 响应，确保页面必须等待用户重试。测试在重试前独立读取真实任务，确认服务端登记已提交。重试只需 GET 即可恢复登记。
- 另一运行的第一次登记在发出前被 Chrome 拦截。浏览器刷新后保持原 Blob、请求字段和幂等键，重试先 GET，再发送同一 POST。Uvicorn 只收到一次登记。
- A/B 切换分别延迟真实 HTTP 响应和 IndexedDB 事务的完成通知。两个来源都保留原始结果，切换到 B 后才释放 A 的结果。
- 通过 Chrome 权限控制触发 `NotAllowedError`，停止真实媒体轨道触发中断，拦下 `dataavailable` 测试空录音，并中止真实 IndexedDB 写事务测试保存失败。未直接设置 React 状态，也未替换服务端响应。

## 失败与修正

- [首轮浏览器报告](20261117T033542437717Z/browser.json)中，测试误把导航栏内的主题切换按钮判为遮挡。修正了触控命中规则，以实际元素和导航栏位置为准。
- [第二轮浏览器报告](20261117T033724564899Z/browser.json)发现 Tab 聚焦时，「停止录音」按钮下沿超出 390 × 844 视口约 7px。[截图](20261117T033724564899Z/failure.png)保留了焦点环被裁切的状态。
- [第三轮浏览器报告](20261117T033831782748Z/browser.json)确认 CSS 滚动留白无效。[第四轮浏览器报告](20261117T033930762533Z/browser.json)确认在焦点事件中尝试滚动也未解决问题。最终修改把计时放入已有录音状态行，录音开始时不再插入一行把按钮往下推。

浏览器回归要求两套主题的开始任务、开始录音、停止录音、登记按钮均可通过 Tab 到达，焦点环可见，控件边界位于导航栏与视口底部之间。

Chrome 使用虚拟麦克风。验收覆盖真实浏览器录制、回放、轨道生命周期及权限拒绝；未验证物理麦克风、真实手机或浏览器权限矩阵。故障注入重置连接时，Windows asyncio 可能输出 `WinError 10054`，成功与否以浏览器、Uvicorn 请求和数据库断言为准。
