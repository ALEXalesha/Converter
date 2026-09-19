# Build: tests -> app folder (PyInstaller onedir) -> portable zip -> installer (Inno Setup).
# Usage:  powershell -ExecutionPolicy Bypass -File build.ps1   [-SkipTests]
# ASCII only on purpose: Windows PowerShell 5.1 reads BOM-less .ps1 files as ANSI.
# Everything runs in the project's own .venv, never in the global Python.
param([switch]$SkipTests)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$root = $PSScriptRoot

$py = "$root\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "Creating .venv" -ForegroundColor Cyan
    py -3 -m venv "$root\.venv"
    if ($LASTEXITCODE) { python -m venv "$root\.venv" }
    if ($LASTEXITCODE) { throw "venv failed" }
}
& $py -m pip install --disable-pip-version-check -q -r "$root\requirements-dev.txt"
if ($LASTEXITCODE) { throw "pip install failed" }

$version = (& $py -c "import raspisanie_core as c; print(c.__version__)").Trim()
Write-Host "Version $version" -ForegroundColor Cyan

if (-not $SkipTests) {
    & $py -m pytest -q -p no:cacheprovider
    if ($LASTEXITCODE) { throw "Tests failed, build stopped" }
}

if (-not (Test-Path assets\icon.ico)) { & $py assets\make_icon.py }

& $py -m PyInstaller --noconfirm --clean --windowed --onedir --name RaspisaniePrint `
    --icon "$root\assets\icon.ico" --add-data "$root\assets\icon.ico;assets" `
    --exclude-module tkinter --exclude-module _tkinter `
    --exclude-module hypothesis --exclude-module pytest `
    --exclude-module PIL --exclude-module numpy --exclude-module win32ui --exclude-module pythonwin `
    --exclude-module PySide6.QtNetwork --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick `
    --exclude-module PySide6.QtSql --exclude-module PySide6.QtOpenGL --exclude-module PySide6.QtPdf `
    --exclude-module PySide6.QtSvg --exclude-module PySide6.QtDBus `
    --distpath dist --workpath build --specpath build `
    "$root\raspisanie_gui.py"
if ($LASTEXITCODE) { throw "PyInstaller failed" }

# Software OpenGL fallback is never used by Qt Widgets; keep only Russian Qt texts.
$qt = "$root\dist\RaspisaniePrint\_internal\PySide6"
Remove-Item -Force -ErrorAction SilentlyContinue "$qt\opengl32sw.dll"
if (Test-Path "$qt\translations") {
    Get-ChildItem "$qt\translations" -File | Where-Object { $_.Name -notlike "qtbase_ru*.qm" } | Remove-Item -Force
}

# Portable = the same app folder in a zip: unpack anywhere and run RaspisaniePrint.exe.
$zip = "$root\dist\RaspisaniePrint-portable-$version.zip"
if (Test-Path $zip) { Remove-Item -Force $zip }
Compress-Archive -Path "$root\dist\RaspisaniePrint" -DestinationPath $zip
Write-Host "Portable: $zip" -ForegroundColor Green

$iscc = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 (ISCC.exe) not found: winget install JRSoftware.InnoSetup" }

& $iscc /Q "/DAppVersion=$version" installer.iss
if ($LASTEXITCODE) { throw "Inno Setup failed" }

$size = (Get-ChildItem "$root\dist\RaspisaniePrint" -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("App folder: {0:N1} MB" -f $size)
Get-ChildItem dist -File | Where-Object { $_.Name -like "*$version*" } |
    Select-Object Name, @{n = "MB"; e = { [math]::Round($_.Length / 1MB, 1) } }
