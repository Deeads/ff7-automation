$ErrorActionPreference = 'Stop'
[System.IO.File]::WriteAllText((Join-Path $PSScriptRoot 'STOP'), 'stop')
Write-Output 'Stop requested. Button holds end within 10 ms; an in-progress model decision finishes with the controller neutral.'
