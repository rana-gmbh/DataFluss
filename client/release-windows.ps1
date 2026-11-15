# Builds DataFluss for Windows and writes the release files to client\release\<version>\:
#   DataFluss-<version>-setup.exe   the installer, for people downloading the app and for updates
#   appcast.xml                     the update feed WinSparkle reads (goes to rana-gmbh/DataFluss, windows/)
#
#   powershell -ExecutionPolicy Bypass -File client\release-windows.ps1
#
# Needs what build-sidecar.ps1 needs, plus Rust and Node (client/README.md, "Build on Windows").
#
# The update is signed with the Sparkle key, the same one that signs the macOS updates: its private
# half in SPARKLE_PRIVATE_KEY (one line, as exported with generate_keys -x) or in a file named by
# SPARKLE_PRIVATE_KEY_FILE. Without either the installer is built but no appcast.xml is written.
#
# Code signing (Authenticode) with Azure Artifact Signing happens only when AZURE_SIGNING_ACCOUNT is
# set (ranagmbh), together with AZURE_SIGNING_ENDPOINT (https://neu.codesigning.azure.net),
# AZURE_SIGNING_PROFILE (rana-public) and the service principal in AZURE_TENANT_ID, AZURE_CLIENT_ID
# and AZURE_CLIENT_SECRET. It needs artifact-signing-cli on the PATH, plus the Azure CLI and signtool.
# Without it the installer is unsigned and Windows SmartScreen warns before it runs.
#
# For trying updates locally: DATAFLUSS_UPDATE_KEY (public key the shell was built with, see
# src/winsparkle.rs) and DATAFLUSS_DOWNLOADS (where the feed says the installer is).
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

function Run($exe) {
    & $exe @args
    if ($LASTEXITCODE -ne 0) { throw "$exe $args failed ($LASTEXITCODE)" }
}
function Step($text) { Write-Host "`n== $text" }

$releases = "https://github.com/rana-gmbh/DataFluss"
$version = (Get-Content client\shell\src-tauri\tauri.conf.json -Raw | ConvertFrom-Json).version
$out = "client\release\$version"
$setup = "DataFluss-$version-setup.exe"
$downloads = if ($env:DATAFLUSS_DOWNLOADS) { $env:DATAFLUSS_DOWNLOADS.TrimEnd("/") } else { "$releases/releases/download/v$version" }
$publicKey = if ($env:DATAFLUSS_UPDATE_KEY) { $env:DATAFLUSS_UPDATE_KEY } else {
    [regex]::Match((Get-Content client\shell\src-tauri\src\winsparkle.rs -Raw), 'None => "([A-Za-z0-9+/=]{44})"').Groups[1].Value
}
if (-not $publicKey) { throw "Public update key not found in src/winsparkle.rs" }

Step "DataFluss $version"
if (Test-Path $out) { Remove-Item -Recurse -Force $out }
New-Item -ItemType Directory -Force $out | Out-Null

Step "Sidecar"
# Explorer or a virus scanner may still hold files of the last build, which makes PyInstaller fail.
foreach ($attempt in 1..5) {
    if (-not (Test-Path client\dist\schule-sidecar)) { break }
    try { Remove-Item -Recurse -Force client\dist\schule-sidecar } catch { Start-Sleep 1 }
}
Run powershell -NoProfile -ExecutionPolicy Bypass -File client\build-sidecar.ps1

Step "WinSparkle"
Run powershell -NoProfile -ExecutionPolicy Bypass -File client\fetch-winsparkle.ps1

$tauriArgs = @()
if ($env:AZURE_SIGNING_ACCOUNT) {
    Step "Code signing with Azure Artifact Signing"
    foreach ($name in "AZURE_SIGNING_ENDPOINT", "AZURE_SIGNING_PROFILE", "AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET") {
        if (-not [Environment]::GetEnvironmentVariable($name)) { throw "$name is not set" }
    }
    $sign = @("-e", $env:AZURE_SIGNING_ENDPOINT, "-a", $env:AZURE_SIGNING_ACCOUNT, "-c", $env:AZURE_SIGNING_PROFILE, "-d", "DataFluss")
    # The sidecar is a resource, which Tauri does not sign; its program is signed here.
    Run artifact-signing-cli @sign client\dist\schule-sidecar\schule-sidecar.exe
    # Tauri signs DataFluss.exe, the uninstaller and the installer with this command.
    $override = Join-Path $env:TEMP "datafluss-signing.json"
    @{ bundle = @{ windows = @{ signCommand = @{ cmd = "artifact-signing-cli"; args = $sign + "%1" } } } } | ConvertTo-Json -Depth 6 | Set-Content -Encoding utf8 $override
    $tauriArgs = @("--config", $override)
}

Step "App and installer"
Push-Location client\shell
try {
    Run npm.cmd ci --silent
    Run npx.cmd tauri build --bundles nsis @tauriArgs
} finally { Pop-Location }
$target = "client\shell\src-tauri\target\release"
foreach ($file in "$target\sidecar\schule-sidecar.exe", "$target\WinSparkle.dll") {
    if (-not (Test-Path $file)) { throw "$file is missing from the build" }
}
Copy-Item "$target\bundle\nsis\DataFluss_${version}_x64-setup.exe" "$out\$setup"
if ($env:AZURE_SIGNING_ACCOUNT) {
    $signature = Get-AuthenticodeSignature "$out\$setup"
    if ($signature.Status -ne "Valid") { throw "Installer signature: $($signature.Status)" }
}

$key = $env:SPARKLE_PRIVATE_KEY
if (-not $key -and $env:SPARKLE_PRIVATE_KEY_FILE) { $key = (Get-Content $env:SPARKLE_PRIVATE_KEY_FILE -Raw) }
if (-not $key) {
    Write-Warning "No SPARKLE_PRIVATE_KEY: the installer is built, but there is no appcast.xml."
} else {
    Step "Update signature and appcast"
    $tool = "client\build\winsparkle\bin\winsparkle-tool.exe"
    $keyFile = Join-Path $env:TEMP ("datafluss-" + [guid]::NewGuid() + ".key")
    try {
        Set-Content -NoNewline -Encoding ascii -Path $keyFile -Value $key.Trim()
        $derived = [regex]::Match((& $tool public-key -f $keyFile | Out-String), 'Public key: (\S+)').Groups[1].Value
        if ($derived -ne $publicKey) { throw "The private key does not belong to the app's public key $publicKey" }
        $signed = & $tool sign -v -f $keyFile "$out\$setup" | Out-String
        if ($LASTEXITCODE -ne 0) { throw "winsparkle-tool sign failed" }
    } finally { Remove-Item -ErrorAction SilentlyContinue $keyFile }
    $edSignature = [regex]::Match($signed, 'sparkle:edSignature="([^"]+)"').Groups[1].Value
    $length = (Get-Item "$out\$setup").Length
    Run $tool verify -p $publicKey -s $edSignature "$out\$setup"

    $notesFile = "client\release-notes\$version.html"
    $notes = if (Test-Path $notesFile) { (Get-Content -Raw -Encoding utf8 $notesFile).Trim() } else { "<h2>DataFluss $version</h2>" }
    $date = [DateTime]::UtcNow.ToString("ddd, dd MMM yyyy HH:mm:ss +0000", [Globalization.CultureInfo]::InvariantCulture)
    # /P /UPDATE /R: Tauri's installer runs passive (progress only), keeps the shortcuts and starts
    # DataFluss again when done.
    $appcast = @"
<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">
  <channel>
    <title>DataFluss</title>
    <link>$releases</link>
    <item>
      <title>DataFluss $version</title>
      <pubDate>$date</pubDate>
      <sparkle:version>$version</sparkle:version>
      <description><![CDATA[$notes]]></description>
      <enclosure url="$downloads/$setup" sparkle:os="windows-x64" sparkle:installerArguments="/P /UPDATE /R" length="$length" type="application/octet-stream" sparkle:edSignature="$edSignature"/>
    </item>
  </channel>
</rss>
"@
    [IO.File]::WriteAllText((Join-Path (Get-Location) "$out\appcast.xml"), $appcast, (New-Object Text.UTF8Encoding $false))
}

Step "Done"
Get-ChildItem $out | Format-Table Name, @{ n = "MB"; e = { "{0:N1}" -f ($_.Length / 1MB) } }
