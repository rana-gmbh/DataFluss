# Freezes the Django app into client\dist\schule-sidecar on Windows. Run from anywhere:
#   powershell -ExecutionPolicy Bypass -File client\build-sidecar.ps1
# Needs Python 3.14 (with the py launcher) and MSYS2 in C:\msys64 (or $env:MSYS2_ROOT). Pango comes
# from MSYS2's UCRT64 packages and is installed here if missing.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

function Run($exe) {
    & $exe @args
    if ($LASTEXITCODE -ne 0) { throw "$exe $args failed ($LASTEXITCODE)" }
}

$msys = if ($env:MSYS2_ROOT) { $env:MSYS2_ROOT } else { "C:\msys64" }
$ucrt = Join-Path $msys "ucrt64\bin"
if (-not (Test-Path (Join-Path $ucrt "libpango-1.0-0.dll"))) {
    Write-Host "Installing Pango from MSYS2 ..."
    Run (Join-Path $msys "usr\bin\bash.exe") -lc "pacman -S --noconfirm --needed mingw-w64-ucrt-x86_64-pango"
}

# A venv of its own: client\.venv may be a macOS one in a shared folder.
$venv = "client\.venv-win"
$python = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $python)) {
    # $env:PYTHON names another interpreter, for example the one actions/setup-python installed.
    if ($env:PYTHON) { Run $env:PYTHON -m venv $venv } else { Run py -3.14 -m venv $venv }
    Run $python -m pip -q install --upgrade pip
    Run $python -m pip -q install -r requirements.txt -r client\requirements.txt
}

# Production mode, so the hashed file names match what the frozen app asks for (a local .env may say DEBUG=1).
if (Test-Path staticfiles) { Remove-Item -Recurse -Force staticfiles }
$env:DEBUG = "0"; $env:SECRET_KEY = "build"
Run $python manage.py collectstatic --noinput -v0
Remove-Item Env:DEBUG, Env:SECRET_KEY

# PyInstaller finds the dependencies of the Pango DLLs (glib, cairo-free FreeType, ...) on the PATH.
# At the end, so Python's own DLLs (libffi, OpenSSL) win where both have one.
$env:PATH = "$env:PATH;$ucrt"
$env:MSYS2_ROOT = $msys
Run $python -m PyInstaller --noconfirm --log-level WARN --workpath client\build --distpath client\dist client\sidecar\sidecar.spec

$size = (Get-ChildItem -Recurse client\dist\schule-sidecar | Measure-Object -Sum Length).Sum / 1MB
Write-Host ("client\dist\schule-sidecar: {0:N0} MB" -f $size)
