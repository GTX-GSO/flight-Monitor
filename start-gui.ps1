# 打开机票监控图形界面
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root
$env:PYTHONIOENCODING = "utf-8"
python gui.py
