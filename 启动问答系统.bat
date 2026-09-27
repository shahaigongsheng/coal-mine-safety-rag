@echo off
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
