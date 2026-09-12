param([switch]$SkipTests)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$version = (python -c "import raspisanie_core as c; print(c.__version__)").Trim()
Write-Host "Версия $version"

if (-not $SkipTests) {
    python -m pytest -q
    if ($LASTEXITCODE) { throw "Тесты не прошли, сборка остановлена" }
}

if (-not (Test-Path assets\icon.ico)) { python assets\make_icon.py }

$common = @("--noconfirm", "--clean", "--windowed", "--icon", "assets\icon.ico",
            "--add-data", "assets\icon.ico;assets", "--distpath", "dist", "--workpath", "build")

python -m PyInstaller @common --name RaspisaniePrint raspisanie_gui.py
if ($LASTEXITCODE) { throw "PyInstaller (папка) упал" }

python -m PyInstaller @common --onefile --name "RaspisaniePrint-portable-$version" raspisanie_gui.py
if ($LASTEXITCODE) { throw "PyInstaller (portable) упал" }

$iscc = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Не найден Inno Setup 6 (ISCC.exe)" }

& $iscc "/DAppVersion=$version" installer.iss
if ($LASTEXITCODE) { throw "Inno Setup упал" }

Get-ChildItem dist -File | Select-Object Name, @{n = "MB"; e = { [math]::Round($_.Length / 1MB, 1) } }
