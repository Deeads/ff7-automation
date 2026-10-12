param(
    [switch]$Doctor,
    [switch]$ObserveOnly,
    [switch]$Panel,
    [switch]$Play,
    [double]$Seconds = 0,
    [string]$EmulatorPath,
    [string]$Disc,
    [string]$SaveState
)
$ErrorActionPreference = 'Stop'
$ff7Python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $ff7Python)) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 -m venv (Join-Path $PSScriptRoot '.venv')
    } else {
        throw 'Install Python 3 for Windows, then run Start-FF7.ps1 again.'
    }
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
}
if (-not $Doctor -and -not $ObserveOnly) {
    & $ff7Python -c 'import vgamepad' 2>$null
    if ($LASTEXITCODE -ne 0) {
        & $ff7Python -m pip install -r (Join-Path $PSScriptRoot 'requirements.txt')
        if ($LASTEXITCODE -ne 0) { throw 'Controller dependency installation failed.' }
    }
}
$ff7Options = @((Join-Path $PSScriptRoot 'launch.py'), '--seconds', $Seconds.ToString([cultureinfo]::InvariantCulture))
if ($Doctor) { $ff7Options += '--doctor' }
if ($ObserveOnly) { $ff7Options += '--observe' }
if ($Panel) { $ff7Options += '--panel' }
if ($EmulatorPath) { $ff7Options += '--emulator-path'; $ff7Options += $EmulatorPath }
if ($Disc) { $ff7Options += '--disc'; $ff7Options += $Disc }
if ($SaveState) { $ff7Options += '--save-state'; $ff7Options += $SaveState }
& $ff7Python @ff7Options
if ($LASTEXITCODE -ne 0) { throw 'FF7 startup failed; see the message above.' }
if ($Panel) { & (Join-Path $PSScriptRoot 'Start-UI.ps1') -Play:$Play }
