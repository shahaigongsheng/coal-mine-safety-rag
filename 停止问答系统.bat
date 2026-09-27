@echo off
title 停止煤矿安全标准智能问答系统
set "PORT=8501"
powershell -NoProfile -Command "$c = Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue; if ($c) { $c | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue; Write-Output ('已停止进程 ' + $_) } } else { Write-Output '服务未在运行' }"
echo.
echo %PORT% 端口已释放。
timeout /t 3 >nul
exit /b 0
