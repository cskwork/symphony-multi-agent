@echo off
REM tui-open.bat - launch Symphony's TUI in a new console window (Windows).
REM
REM Usage:
REM   tui-open.bat [path\to\WORKFLOW.md]
REM
REM - Defaults to .\WORKFLOW.md.
REM - Prefers .venv\Scripts\symphony.exe; falls back to PATH.
REM - Runs `symphony --tui WORKFLOW.md` (single process: orchestrator + TUI).
REM - Spawns a NEW console window via `start "title" cmd /k ...` so this
REM   script returns control to the caller immediately.

setlocal ENABLEDELAYEDEXPANSION

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

if "%~1"=="" (
  set "WORKFLOW=%SCRIPT_DIR%\WORKFLOW.md"
) else (
  set "WORKFLOW=%~1"
)

if not exist "%WORKFLOW%" (
  echo tui-open: workflow not found: %WORKFLOW% 1>&2
  exit /b 2
)

if exist "%SCRIPT_DIR%\.venv\Scripts\symphony.exe" (
  set "SYMPHONY=%SCRIPT_DIR%\.venv\Scripts\symphony.exe"
) else (
  where symphony >nul 2>&1
  if errorlevel 1 (
    echo tui-open: 'symphony' not found on PATH and no .venv\Scripts\symphony.exe 1>&2
    echo tui-open: install with: py -3.11 -m venv .venv ^&^& .venv\Scripts\pip install -e . 1>&2
    exit /b 3
  )
  for /f "delims=" %%i in ('where symphony') do set "SYMPHONY=%%i"
)

echo tui-open: running doctor preflight...
"%SYMPHONY%" doctor "%WORKFLOW%"
if errorlevel 1 (
  echo tui-open: doctor reported FAIL - aborting launch 1>&2
  exit /b 4
)

echo tui-open: opening new console window...
start "Symphony TUI" cmd /k "cd /d "%SCRIPT_DIR%" && "%SYMPHONY%" --tui "%WORKFLOW%""

echo tui-open: launched. Headless logs (if any) tail with:
echo   powershell -c "Get-Content -Wait '%SCRIPT_DIR%\log\symphony.log'"

endlocal
