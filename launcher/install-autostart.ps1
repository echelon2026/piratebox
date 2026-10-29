# Makes PirateBox start at Windows login: splash screen -> loopMIDI -> PirateBox -> FL Studio -> ready.
# Run from PowerShell:  powershell -ExecutionPolicy Bypass -File launcher\install-autostart.ps1 [-AI] [-BLE] [-Remove]
param([switch]$AI, [switch]$BLE, [switch]$Remove)
$ErrorActionPreference = "Stop"

$startup = [Environment]::GetFolderPath("Startup")
$shortcut = Join-Path $startup "PirateBox.lnk"

if ($Remove) {
    Remove-Item -ErrorAction SilentlyContinue $shortcut
    Write-Host "PirateBox will no longer start at login."
    exit 0
}

$repo = Split-Path -Parent $PSScriptRoot
$bridge = Join-Path $repo "bridge"
# Prefer the project's virtual environment; pythonw.exe runs without a console window.
$pythonw = Join-Path $repo ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) {
    $found = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if (-not $found) { throw "pythonw.exe not found. Install Python 3 (with tcl/tk) or create .venv first." }
    $pythonw = $found.Source
}

$arguments = "-m piratebox.launcher"
if ($AI) { $arguments += " --ai" }
if ($BLE) { $arguments += " --ble" }

$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($shortcut)
$link.TargetPath = $pythonw
$link.Arguments = $arguments
$link.WorkingDirectory = $bridge
$link.Description = "Start PirateBox and FL Studio"
$link.Save()

Write-Host "PirateBox will start at login ($pythonw $arguments)."
Write-Host "Try it now without logging out:  cd `"$bridge`"; & `"$pythonw`" $arguments"
if ($AI -and -not $env:ANTHROPIC_API_KEY -and -not [Environment]::GetEnvironmentVariable("ANTHROPIC_API_KEY", "User")) {
    Write-Warning "ANTHROPIC_API_KEY is not set for this user. Set it with: setx ANTHROPIC_API_KEY your-key"
}
