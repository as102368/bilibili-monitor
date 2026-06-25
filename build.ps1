# 一键打包脚本
# 运行后会生成 dist/BilibiliMonitor/BilibiliMonitor.exe

$ErrorActionPreference = "Stop"

Write-Host "正在安装/更新 PyInstaller..." -ForegroundColor Cyan
python -m pip install pyinstaller -q

Write-Host "开始打包 BilibiliMonitor..." -ForegroundColor Cyan
python -m PyInstaller BilibiliMonitor.spec --clean -y --distpath D:\BI\bilibili-monitor-dist

if ($LASTEXITCODE -eq 0) {
    Write-Host "打包成功！" -ForegroundColor Green
    Write-Host "可执行文件位于: D:\BI\bilibili-monitor-dist\bilibili-monitor\bilibili-monitor.exe" -ForegroundColor Green
} else {
    Write-Host "打包失败，请检查上方错误信息。" -ForegroundColor Red
}
