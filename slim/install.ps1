# talos installer for Windows x64.
#
#   powershell -ExecutionPolicy Bypass -File install.ps1 [-Version vX.Y.Z] [-Dir $env:LOCALAPPDATA\talos] [-- <setup args>]
#
# Downloads one prebuilt bundle from this repo's GitHub releases ($repo below),
# verifies its sha256, unpacks it, and runs talos-setup. Nothing is
# compiled and no package manager runs.
param(
    [string]$Version = "latest",
    [string]$Dir = "",
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$SetupArgs
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$repo = "talos-hq/talos"
if (-not $Dir) {
    $Dir = Join-Path $env:LOCALAPPDATA "talos"
    # Install from before the rename (legacy dir): move it over so it is upgraded in place.
    $legacyDir = Join-Path $env:LOCALAPPDATA "hermes-slim"
    if ((Test-Path $legacyDir) -and -not (Test-Path $Dir)) {
        & (Join-Path $legacyDir "current\bin\hermes.cmd") gateway stop 2>$null | Out-Null
        try {
            Move-Item $legacyDir $Dir
            $legacyBin = Join-Path $legacyDir "current\bin"
            $p = [Environment]::GetEnvironmentVariable("Path", "User")
            [Environment]::SetEnvironmentVariable("Path", (($p -split ";") | Where-Object { $_ -and $_ -ne $legacyBin }) -join ";", "User")
        } catch {
            Write-Host "-> could not move $legacyDir ($_); upgrading it in place"
            $Dir = $legacyDir
        }
    }
}
$target = "win32-x64"

$api = if ($Version -eq "latest") { "https://api.github.com/repos/$repo/releases/latest" } else { "https://api.github.com/repos/$repo/releases/tags/$Version" }
$release = Invoke-RestMethod $api
$asset = $release.assets | Where-Object { $_.name -like "talos-*-$target.zip" -or $_.name -like "hermes-slim-*-$target.zip" } |
    Sort-Object { -not $_.name.StartsWith("talos-") } | Select-Object -First 1  # hermes-slim-: legacy asset prefix
if (-not $asset) { throw "no $target bundle in release $($release.tag_name)" }
$sum = $release.assets | Where-Object { $_.name -eq "$($asset.name).sha256" } | Select-Object -First 1

$tmp = Join-Path ([IO.Path]::GetTempPath()) ("talos-" + [guid]::NewGuid())
New-Item -ItemType Directory $tmp | Out-Null
try {
    $zip = Join-Path $tmp $asset.name
    Write-Host "-> downloading $($asset.name)"
    Invoke-WebRequest $asset.browser_download_url -OutFile $zip
    $expected = ((Invoke-WebRequest $sum.browser_download_url).Content -split '\s+')[0]
    $actual = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
    if ($expected -ne $actual) { throw "sha256 mismatch" }

    Write-Host "-> installing to $Dir"
    New-Item -ItemType Directory -Force $Dir | Out-Null
    $new = Join-Path $Dir "new"; $current = Join-Path $Dir "current"; $old = Join-Path $Dir "old"
    Remove-Item -Recurse -Force $new, $old -ErrorAction SilentlyContinue
    Expand-Archive $zip -DestinationPath $new
    if (Test-Path $current) { Move-Item $current $old }
    # Bundles from before the rename unpack to a hermes-slim\ folder (legacy name).
    $top = Get-ChildItem $new -Directory | Select-Object -First 1
    Move-Item $top.FullName $current
    Remove-Item -Recurse -Force $new
    Get-ChildItem -Recurse $current | Unblock-File

    $bin = Join-Path $current "bin"
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (($userPath -split ";") -notcontains $bin) {
        [Environment]::SetEnvironmentVariable("Path", "$userPath;$bin", "User")
    }
    $env:Path = "$env:Path;$bin"
    Write-Host "OK installed; 'hermes' is on your PATH (new terminals)"
}
finally {
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}

& (Join-Path $bin "talos-setup.cmd") --service @SetupArgs
exit $LASTEXITCODE
