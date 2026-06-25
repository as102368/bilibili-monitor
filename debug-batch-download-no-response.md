# Debug Session: batch-download-no-response

## Status
[OPEN]

## Symptom
打包后的程序（`D:\BI\bilibili-monitor-dist\bilibili-monitor\bilibili-monitor.exe`）中，批量下载功能可以正常扫描/加载视频列表，但点击“下载选中”后没有任何反应（无弹窗、无下载、无日志）。

## Environment
- OS: Windows 11
- Packaged with: PyInstaller 6.21.0, Python 3.14.6, PySide6
- Source: `d:\BI\bilibili-monitor`
- Output: `d:\BI\bilibili-monitor-dist\bilibili-monitor`

## Hypotheses
1. `asyncio.create_task(self._redownload_worker())` 在打包后的 GUI（console=False）中抛出异常并被静默吞掉，导致任务未启动。
2. `_get_downloader()` 在打包环境中初始化 `BilibiliMonitor`/`Downloader` 失败并返回 `None`，但调用方仅 `continue` 未提示用户。
3. `QMessageBox.question` 的返回值与 `QMessageBox.Yes` 比较在 PySide6 中行为异常，导致点击“是”后被误判为“否”并直接返回。
4. `download_callback` 闭包（用户中心批量下载）在打包后捕获的 `self` 或事件循环上下文异常，调用时抛出但未显示。
5. 下载队列中的视频全部被 `db.is_downloaded(bvid)` 判定为已下载/已跳过，导致 worker 直接结束，用户看不到任何反馈。

## Instrumentation Plan
- 在 `src/gui/main_window.py` 的 `_setup_logging` 中追加文件日志处理器，日志写入 `D:\BI\bilibili-monitor\logs\app.log`。
- 在关键路径插入 DEBUG/INFO 日志：
  - `_on_batch_download`：进入、选中数量、确认框返回值、入队数量。
  - `download_callback`（用户中心）：进入、入队数量、是否启动 worker。
  - `_redownload_worker`：进入、队列长度、每次循环的 bvid/结果。
  - `_get_downloader`：进入、返回结果或异常。

## Verification
待用户复现后，根据 `logs/app.log` 中的运行时证据确认/排除假设，再实施最小范围修复。
