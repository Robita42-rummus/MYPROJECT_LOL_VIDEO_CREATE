; LoLクライアントをアクティブにしてキー「W」を2回押す (レッドチーム ジャングラー)
#NoEnv
SetWorkingDir %A_ScriptDir%

WinActivate, League of Legends (TM) Client
WinWaitActive, League of Legends (TM) Client,, 5
if ErrorLevel
    ExitApp

Sleep, 100
Send, w
Sleep, 300
Send, w
ExitApp
