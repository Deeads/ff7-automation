# Starts the Laya control panel in the background and opens it in your browser.
$ErrorActionPreference = 'Stop'
$port = 7777
$url = "http://127.0.0.1:$port/"
$up = $false
try { Invoke-WebRequest "$url`api/status" -UseBasicParsing -TimeoutSec 1 | Out-Null; $up = $true } catch {}
if (-not $up) {
    Start-Process -FilePath (Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe') `
        -ArgumentList ('"' + (Join-Path $PSScriptRoot 'laya_ui.py') + '"') `
        -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
    Start-Sleep -Milliseconds 700
}
Start-Process $url
Write-Output "Control panel: $url  (Stop-UI.ps1 closes it; the bot keeps its own Start/Stop.)"
