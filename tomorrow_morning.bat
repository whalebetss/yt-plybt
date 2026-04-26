@echo off
REM Run after YouTube API quota reset (~10:00 TRT daily).
REM 1) Flip yesterday's videos to public
REM 2) Backfill missing caption tracks
REM 3) Restart the scheduler in the background

cd /d C:\Users\murat\Desktop\YT-PLYBT

echo === Setting both videos to PUBLIC ===
python set_public.py PIH-iTkbk-g zzxx1w2LBo4

echo === Backfilling captions on PIH-iTkbk-g (pt/hi/id) ===
python add_missing_captions.py output\2026-04-25T14-11-43 PIH-iTkbk-g

echo === Backfilling captions on zzxx1w2LBo4 (en/es/pt/hi/id/tr) ===
python add_missing_captions.py output\2026-04-25T19-01-24 zzxx1w2LBo4

echo === Restarting scheduler in background ===
start "WhaleBets Scheduler" /B python scheduler.py

echo === DONE ===
