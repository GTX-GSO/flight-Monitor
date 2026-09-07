# 打包可运行目录（Windows）
# 用法：在项目根目录执行  .\scripts\build_exe.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "==> 安装/检查打包依赖" -ForegroundColor Cyan
python -m pip install -r requirements.txt pyinstaller -q
python -m playwright install chromium

Write-Host "==> PyInstaller 构建" -ForegroundColor Cyan
python -m PyInstaller --noconfirm --clean JiPiaoMonitor.spec

$out = Join-Path $root "dist\JiPiaoMonitor"
if (-not (Test-Path (Join-Path $out "JiPiaoMonitor.exe"))) {
    throw "未找到 dist\JiPiaoMonitor\JiPiaoMonitor.exe"
}

# 附带示例配置与说明
Copy-Item (Join-Path $root "config.example.yaml") (Join-Path $out "config.example.yaml") -Force
@"
机票价格监控 - 便携运行说明
========================
1. 首次运行前，在本机已安装的 Python 环境中执行（只需一次）:
   python -m playwright install chromium
   （Playwright 浏览器内核约数百 MB，不会打进本目录以控制体积。）

2. 复制 config.example.yaml 为 config.yaml，按需修改航线/推送。

3. 双击 JiPiaoMonitor.exe 启动图形界面。
   或命令行: .\JiPiaoMonitor.exe

4. 日志与数据库默认写在 exe 同目录下的 logs/、data/（若程序以该目录为工作目录）。
"@ | Set-Content -Path (Join-Path $out "使用说明.txt") -Encoding UTF8

Write-Host "==> 完成: $out\JiPiaoMonitor.exe" -ForegroundColor Green
