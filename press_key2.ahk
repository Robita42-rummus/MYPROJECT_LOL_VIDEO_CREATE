; LoLクライアントをアクティブにしてキー「2」を2回押す
#NoEnv
SetWorkingDir %A_ScriptDir%

WinActivate, League of Legends (TM) Client
WinWaitActive, League of Legends (TM) Client,, 5
if ErrorLevel
    ExitApp

Sleep, 100
Send, 2
Sleep, 300
Send, 2
ExitApp
