# Copies the PirateBox controller script into FL Studio's hardware scripts folder.
# Run from PowerShell:  powershell -ExecutionPolicy Bypass -File flstudio-controller\install.ps1
$ErrorActionPreference = "Stop"

# GetFolderPath follows OneDrive/redirected Documents folders, unlike $HOME\Documents.
$docs = [Environment]::GetFolderPath("MyDocuments")
$target = Join-Path $docs "Image-Line\FL Studio\Settings\Hardware\PirateBox"
$source = Join-Path $PSScriptRoot "PirateBox\device_PirateBox.py"

if (-not (Test-Path (Join-Path $docs "Image-Line\FL Studio"))) {
    Write-Warning "FL Studio user data folder not found under $docs. Has FL Studio been run at least once?"
}
New-Item -ItemType Directory -Force -Path $target | Out-Null
Copy-Item -Force $source $target
Write-Host "Installed controller script to $target"
Write-Host "Restart FL Studio (or reselect the controller type) to load the new version."
