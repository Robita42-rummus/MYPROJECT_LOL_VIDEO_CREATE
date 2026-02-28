@echo off
:: LoL Auto Video Creator - 定時実行スクリプト
:: タスクスケジューラから毎朝9:00 JSTに実行される

cd /d "c:\+mywork\+myproject_lol_video_create"

:: ログファイル名に日付を付与
set LOG=logs\scheduler_%date:~0,4%-%date:~5,2%-%date:~8,2%.log

:: logsフォルダを作成 (なければ)
if not exist logs mkdir logs

echo [%date% %time%] 定時実行開始 >> %LOG%
python main.py --max 6 >> %LOG% 2>&1
set EXIT_CODE=%ERRORLEVEL%
echo [%date% %time%] 定時実行終了 (終了コード: %EXIT_CODE%) >> %LOG%

:: 正常終了 (終了コード 0) の場合のみ再起動
if %EXIT_CODE% == 0 (
    echo [%date% %time%] 正常終了のため60秒後に再起動します >> %LOG%
    shutdown /r /t 60 /c "LoL Auto Video 完了 - 60秒後に再起動"
) else (
    echo [%date% %time%] エラー終了のため再起動をスキップします >> %LOG%
)
