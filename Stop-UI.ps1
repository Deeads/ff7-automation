# Closes the control panel server only. Use the panel's STOP (or Stop-Laya.ps1) to stop the bot.
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
    Where-Object { $_.CommandLine -like '*laya_ui.py*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId }
Write-Output 'Control panel stopped.'
