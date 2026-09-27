# -*- coding: utf-8 -*-
"""把启动/停止脚本以 GBK 编码落盘。

原因：cmd.exe 按控制台代码页（中文 Windows 为 GBK）逐行解析 .bat，
     文件若存成 UTF-8，中文注释和提示会变成乱码，甚至被当成命令执行。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

START = r'''@echo off
title 煤矿安全标准智能问答系统
cd /d "%~dp0"

set "PY=C:\Users\Administrator\.workbuddy\binaries\python\envs\coalrag\Scripts\python.exe"
set "PORT=8501"
set "URL=http://127.0.0.1:%PORT%"

if not exist "%PY%" (
  echo [错误] 找不到 Python 环境，请检查虚拟环境是否还在。
  pause
  exit /b 1
)

echo 正在检查 %PORT% 端口 ...
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue) { exit 1 } else { exit 0 }"
if errorlevel 1 (
  echo 服务已在运行，直接打开浏览器。
  goto OPEN
)

echo 正在启动服务，首次加载索引约需 1-2 分钟 ...
start "coal-rag-server" /min "%PY%" -m streamlit run app/streamlit_app.py --server.port %PORT% --server.headless true

echo 等待服务就绪（最多 120 秒）...
powershell -NoProfile -Command "for($i=0;$i -lt 120;$i++){ Start-Sleep -Seconds 1; try{ $r=Invoke-WebRequest -Uri '%URL%/_stcore/health' -UseBasicParsing -TimeoutSec 2; if($r.StatusCode -eq 200){ exit 0 } }catch{} }; exit 1"
if errorlevel 1 (
  echo [错误] 服务启动超时，请检查 E:\coal-rag 目录是否完好。
  pause
  exit /b 1
)

:OPEN
echo 服务已就绪：%URL%
start "" %URL%
echo.
echo 本窗口可直接关闭，服务会在后台继续运行。
echo 需要停止时，运行桌面上的「停止问答系统.bat」。
timeout /t 5 >nul
exit /b 0
'''

STOP = r'''@echo off
title 停止煤矿安全标准智能问答系统
set "PORT=8501"
powershell -NoProfile -Command "$c = Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue; if ($c) { $c | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue; Write-Output ('已停止进程 ' + $_) } } else { Write-Output '服务未在运行' }"
echo.
echo %PORT% 端口已释放。
timeout /t 3 >nul
exit /b 0
'''

if __name__ == "__main__":
    (ROOT / "启动问答系统.bat").write_text(START, encoding="gbk", errors="replace")
    (ROOT / "停止问答系统.bat").write_text(STOP, encoding="gbk", errors="replace")
    print("bat written in gbk")

    import shutil
    desktop = Path.home() / "Desktop"
    shutil.copy(ROOT / "启动问答系统.bat", desktop / "煤矿安全标准智能问答.bat")
    shutil.copy(ROOT / "停止问答系统.bat", desktop / "停止问答系统.bat")
    print("copied to", desktop)
