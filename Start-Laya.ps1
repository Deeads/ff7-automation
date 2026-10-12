param(
    [switch]$ObserveOnly,
    [switch]$AssistOnly,
    [switch]$BattleAssist,
    [switch]$NoBattle,
    [switch]$Walk,
    [switch]$NoCollect,
    [switch]$Explore,
    [switch]$BattleProbe,
    [switch]$Capture,
    [switch]$FailFast,
    [ValidateSet('auto','duckstation','retroarch','bizhawk','mednafen','epsxe','pcsx')]
    [string]$Emulator = 'auto',
    [ValidateSet('deterministic','laya')]
    [string]$Policy = 'deterministic',
    [int]$EmulatorPid = 0,
    [string]$RamBase,
    [double]$StopAtHp = 0,
    [double]$HealAtHp = 50,
    [double]$RetreatAtMp = 0,
    [switch]$Grind,
    [double]$Seconds = 0,
    [double[]]$Target
)
$ErrorActionPreference = 'Stop'
$ff7Python = Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
$ff7Script = Join-Path $PSScriptRoot 'laya_loop.py'
$ff7Args = @(('"' + $ff7Script + '"'), '--seconds', $Seconds.ToString([cultureinfo]::InvariantCulture))
$ff7Args += '--emulator'; $ff7Args += $Emulator
$ff7Args += '--policy'; $ff7Args += $Policy
if (-not $FailFast) { $ff7Args += '--reconnect' }
if ($EmulatorPid -gt 0) { $ff7Args += '--pid'; $ff7Args += $EmulatorPid.ToString() }
if ($RamBase) {
    if ($RamBase -notmatch '^(0x[0-9a-fA-F]+|[0-9]+)$') { throw 'RamBase must be a decimal or hex address.' }
    $ff7Args += '--ram-base'; $ff7Args += $RamBase
}
if (-not $ObserveOnly) { $ff7Args += '--execute' }
if (-not $AssistOnly -and -not $Target) { $ff7Args += '--story' }
# Battles are fought by default; -BattleAssist is accepted for older callers.
if (-not $NoBattle) { $ff7Args += '--battle-assist' }
if ($Walk) { $ff7Args += '--walk' }
if ($NoCollect) { $ff7Args += '--no-collect' }
if ($Explore) { $ff7Args += '--explore' }
if ($BattleProbe) { $ff7Args += '--battle-probe' }
if ($Capture) { $ff7Args += '--capture' }
if ($StopAtHp -gt 0) { $ff7Args += '--stop-hp'; $ff7Args += $StopAtHp.ToString([cultureinfo]::InvariantCulture) }
$ff7Args += '--heal-hp'; $ff7Args += $HealAtHp.ToString([cultureinfo]::InvariantCulture)
if ($RetreatAtMp -gt 0) { $ff7Args += '--retreat-mp'; $ff7Args += $RetreatAtMp.ToString([cultureinfo]::InvariantCulture) }
if ($Grind) { $ff7Args += '--grind' }
if ($Target) {
    if ($Target.Count -ne 3) { throw 'Target needs three numbers: field ID, X, Y.' }
    $ff7Args += '--target'
    foreach ($ff7Number in $Target) { $ff7Args += $ff7Number.ToString([cultureinfo]::InvariantCulture) }
}
$ff7Process = Start-Process -FilePath $ff7Python -ArgumentList $ff7Args -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $PSScriptRoot 'laya-output.log') -RedirectStandardError (Join-Path $PSScriptRoot 'laya-errors.log') -PassThru
Write-Output "Laya starting in background (PID $($ff7Process.Id)). See laya-status.json; Stop-Laya.ps1 releases the controller and stops it."
