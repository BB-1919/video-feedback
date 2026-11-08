@echo off
cd /d "%~dp0"
where py >nul 2>nul && goto usepy
python video_feedback.py %*
goto done
:usepy
py -3 video_feedback.py %*
:done
if errorlevel 1 goto fail
exit /b 0
:fail
echo.
echo Could not start. Python 3 is needed: https://www.python.org/downloads/
echo When installing, tick "Add python.exe to PATH", then run this file again.
pause
