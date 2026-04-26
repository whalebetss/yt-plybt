@echo off
REM Single-shot pipeline run, used by Windows Scheduled Tasks
REM at 02:00 / 15:00 / 18:00 / 22:00 TRT.

cd /d C:\Users\murat\Desktop\YT-PLYBT

REM Append to a daily log so we can see what fired and when.
set LOGDATE=%date:~-4%-%date:~3,2%-%date:~0,2%
set LOGFILE=logs\pipeline-%LOGDATE%.log

if not exist logs mkdir logs

echo. >> "%LOGFILE%"
echo ====================================== >> "%LOGFILE%"
echo  RUN STARTED %date% %time% >> "%LOGFILE%"
echo ====================================== >> "%LOGFILE%"

python main.py --once >> "%LOGFILE%" 2>&1

echo  RUN FINISHED %date% %time%  exitcode=%ERRORLEVEL% >> "%LOGFILE%"
