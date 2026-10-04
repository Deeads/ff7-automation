param(
    [switch]$ObserveOnly,
    [switch]$AssistOnly,
    [switch]$BattleAssist,
    [switch]$Walk,
    [switch]$NoCollect,
    [switch]$Explore,
    [switch]$BattleProbe,
    [switch]$Capture,
    [double]$StopAtHp = 0,
    [double]$Seconds = 0,
    [double[]]$Target
)
$ErrorActionPreference = 'Stop'
$ff7Python = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
$ff7Script = Join-Path $PSScriptRoot 'laya_loop.py'
$ff7Args = @(('"' + $ff7Script + '"'), '--seconds', $Seconds.ToString([cultureinfo]::InvariantCulture))
if (-not $ObserveOnly) { $ff7Args += '--execute' }
if (-not $AssistOnly -and -not $Target) { $ff7Args += '--story' }
if ($BattleAssist) { $ff7Args += '--battle-assist' }
if ($Walk) { $ff7Args += '--walk' }
if ($NoCollect) { $ff7Args += '--no-collect' }
if ($Explore) { $ff7Args += '--explore' }
if ($BattleProbe) { $ff7Args += '--battle-probe' }
if ($Capture) { $ff7Args += '--capture' }
if ($StopAtHp -gt 0) { $ff7Args += '--stop-hp'; $ff7Args += $StopAtHp.ToString([cultureinfo]::InvariantCulture) }
if ($Target) {
    if ($Target.Count -ne 3) { throw 'Target needs three numbers: field ID, X, Y.' }
    $ff7Args += '--target'
    foreach ($ff7Number in $Target) { $ff7Args += $ff7Number.ToString([cultureinfo]::InvariantCulture) }
}
$ff7Process = Start-Process -FilePath $ff7Python -ArgumentList $ff7Args -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $PSScriptRoot 'laya-output.log') -RedirectStandardError (Join-Path $PSScriptRoot 'laya-errors.log') -PassThru
Write-Output "Laya starting in background (PID $($ff7Process.Id)). See laya-status.json; Stop-Laya.ps1 releases the controller and stops it."
