# Starts the Laya control panel in the background and opens it in its own window.
#   -Play     also starts Story mode (battles on) unless the bot is already running.
#   -Browser  open a normal browser tab instead of an app window.
param(
    [switch]$Play,
    [switch]$Browser
)
$ErrorActionPreference = 'Stop'
$port = 7777
$url = "http://127.0.0.1:$port/"
function Test-Panel {
    try { Invoke-WebRequest "${url}api/status" -UseBasicParsing -TimeoutSec 1 | Out-Null; return $true } catch { return $false }
}
if (-not (Test-Panel)) {
    Start-Process -FilePath (Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe') `
        -ArgumentList ('"' + (Join-Path $PSScriptRoot 'laya_ui.py') + '"') `
        -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
    for ($i = 0; $i -lt 25 -and -not (Test-Panel); $i++) { Start-Sleep -Milliseconds 200 }
}
if ($Play) {
    # The bot reconnects by itself, so this is safe while the emulator is still booting.
    try {
        $status = Invoke-RestMethod "${url}api/status" -TimeoutSec 3
        if (-not $status.running) {
            Invoke-RestMethod "${url}api/start" -Method Post -ContentType 'application/json' `
                -Body '{"mode":"story","battle_assist":true}' -TimeoutSec 40 | Out-Null
        }
    } catch { Write-Output "Could not start the bot: $($_.Exception.Message) Press START in the panel." }
}
# An app window (no tabs, own taskbar button) when Edge or Chrome is installed.
$app = $null
if (-not $Browser) {
    $app = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
             "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
             "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
             "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe") |
        Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if ($app) { Start-Process -FilePath $app -ArgumentList "--app=$url", '--window-size=1180,860' }
else { Start-Process $url }
Write-Output "Control panel: $url  (Stop-UI.ps1 closes it; the bot keeps its own Start/Stop.)"
