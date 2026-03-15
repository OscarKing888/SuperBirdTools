@echo off
setlocal

set "ROOT_DIR=%~dp0.."
call "%ROOT_DIR%\SuperBirdStamp\build_win.bat" %*
exit /b %errorlevel%

