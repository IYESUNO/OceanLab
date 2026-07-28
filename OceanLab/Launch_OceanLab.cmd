@echo off
setlocal
cd /d "%~dp0"

rem Versioned executable names avoid stale Windows Explorer icon-cache entries.
for %%F in ("%~dp0OceanLab-*.exe") do (
  if exist "%%~fF" (
    start "OceanLab" "%%~fF"
    endlocal
    exit /b 0
  )
)

rem Keep compatibility with earlier portable packages.
if exist "%~dp0OceanLab.exe" (
  start "OceanLab" "%~dp0OceanLab.exe"
  endlocal
  exit /b 0
)

set "PYTHON_EXE=C:\Python314\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
set "LOG_DIR=%TEMP%\OceanLab"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
set "LAUNCHER_LOG=%LOG_DIR%\OceanLab-launcher.log"
echo ==================================================>>"%LAUNCHER_LOG%"
echo Launcher started: %date% %time%>>"%LAUNCHER_LOG%"
"%PYTHON_EXE%" "%~dp0launcher.py" 1>>"%LAUNCHER_LOG%" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo OceanLab failed to start. Exit code: %EXIT_CODE%
  echo Launcher log: "%LAUNCHER_LOG%"
  echo Application log: "%TEMP%\OceanLab\OceanLab.log"
  echo.
  type "%LAUNCHER_LOG%"
  pause
)
endlocal
