@echo off
setlocal

set "INSTALL_DIR=%LOCALAPPDATA%\ResearchOS"
set "APP_EXE=%INSTALL_DIR%\researchos-server.exe"
set "DESKTOP_LINK=%USERPROFILE%\Desktop\ResearchOS.cmd"

if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
copy /Y "%~dp0researchos-server.exe" "%APP_EXE%" >nul
if errorlevel 1 (
  echo Failed to copy researchos-server.exe to "%INSTALL_DIR%".
  echo Close any running ResearchOS server window and run this installer again.
  pause
  exit /b 1
)

(
  echo @echo off
  echo cd /d "%INSTALL_DIR%"
  echo start "ResearchOS Server" "%APP_EXE%"
  echo echo ResearchOS is starting. Check the server console for the Web UI URL.
) > "%DESKTOP_LINK%"

echo ResearchOS installed to "%INSTALL_DIR%".
echo A desktop launcher was created: "%DESKTOP_LINK%".
start "ResearchOS Server" "%APP_EXE%"
exit /b 0
