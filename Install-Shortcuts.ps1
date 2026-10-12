# Creates "FF7 Bot" on the Desktop and in the Start Menu (Win key, type "ff7"),
# with Ctrl+Alt+F as a global hotkey. Safe to run again; -Remove deletes both.
param(
    [switch]$Remove,
    [string]$Hotkey = 'CTRL+ALT+F'
)
$ErrorActionPreference = 'Stop'
$shell = New-Object -ComObject WScript.Shell
$duck = Join-Path $env:LOCALAPPDATA 'Programs\DuckStation\duckstation-qt-x64-ReleaseLTCG.exe'
$places = @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))
foreach ($place in $places) {
    $path = Join-Path $place 'FF7 Bot.lnk'
    if ($Remove) {
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path }
        continue
    }
    $link = $shell.CreateShortcut($path)
    $link.TargetPath = Join-Path $PSScriptRoot 'FF7 Bot.cmd'
    $link.WorkingDirectory = $PSScriptRoot
    $link.WindowStyle = 7                       # minimized: only the panel comes to the front
    $link.Description = 'Start FF7 in DuckStation, open the control panel and play Story mode.'
    if (Test-Path -LiteralPath $duck) { $link.IconLocation = "$duck,0" }
    # One hotkey owner: Windows ignores a second shortcut with the same keys.
    if ($place -eq $places[0]) { $link.Hotkey = $Hotkey }
    $link.Save()
    Write-Output "Shortcut: $path"
}
if (-not $Remove) { Write-Output "Open with: Win key then type ff7, the Desktop icon, or $Hotkey." }
