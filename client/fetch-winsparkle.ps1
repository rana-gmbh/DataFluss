# Puts WinSparkle (the Windows update library, the twin of Sparkle) into client\build\winsparkle:
# x64\Release\WinSparkle.dll for the app, bin\winsparkle-tool.exe for signing updates.
# Version and checksum are pinned. Every Windows build of the shell needs it (tauri.windows.conf.json).
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$version = "0.9.4"
$sha256 = "6037df37fc263bd1650a1c4949681a9d40ffe991d01f35892a406cb5d103c976"
$dest = "client\build\winsparkle"
if ((Test-Path "$dest\.version") -and (Get-Content "$dest\.version") -eq $version) { exit 0 }

$downloads = "client\build\winsparkle-download"
$archive = "$downloads\WinSparkle-$version.zip"
New-Item -ItemType Directory -Force $downloads | Out-Null
if (-not (Test-Path $archive)) {
    $ProgressPreference = "SilentlyContinue"
    Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/vslavik/winsparkle/releases/download/v$version/WinSparkle-$version.zip" -OutFile $archive
}
if ((Get-FileHash -Algorithm SHA256 $archive).Hash -ne $sha256) {
    Remove-Item $archive
    throw "WinSparkle archive does not match its checksum"
}
$unpacked = "$downloads\unpacked"
if (Test-Path $unpacked) { Remove-Item -Recurse -Force $unpacked }
Expand-Archive -Path $archive -DestinationPath $unpacked
if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
Move-Item "$unpacked\WinSparkle-$version" $dest
Remove-Item -Recurse -Force $unpacked
Set-Content -Path "$dest\.version" -Value $version -Encoding ascii
