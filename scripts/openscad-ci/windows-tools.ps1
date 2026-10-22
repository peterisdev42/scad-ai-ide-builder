$ErrorActionPreference = 'Stop'
# Do not filter by the x86/x64 component: native ARM64 installations can omit it.
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$vs = & $vswhere -latest -products '*' -version '[17,19)' -property installationPath
if (!$vs) { throw 'Visual Studio installation not found' }
$hostArch = if ($env:RUNNER_ARCH -eq 'ARM64') { 'arm64' } else { 'x64' }
$devcmd = Join-Path $vs 'Common7\Tools\VsDevCmd.bat'
$toolset = if ($env:BUILD_ARCH -eq 'arm64') { '-vcvars_ver=14.44' } else { '' }
$command = "`"$devcmd`" -no_logo -arch=$env:BUILD_ARCH -host_arch=$hostArch $toolset && set"
$variables = & cmd.exe /d /s /c $command
if ($LASTEXITCODE -ne 0) { throw 'Visual Studio developer environment failed' }
foreach ($line in $variables) {
    if ($line -match '^([^=]+)=(.*)$') {
        [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], 'Process')
        "$($Matches[1])=$($Matches[2])" | Out-File -FilePath $env:GITHUB_ENV -Append -Encoding utf8
    }
}
"VCPKG_VISUAL_STUDIO_PATH=$vs" | Out-File -FilePath $env:GITHUB_ENV -Append -Encoding utf8
Get-Command cl.exe | Format-List Source

$archive = Join-Path $env:RUNNER_TEMP 'winflexbison.zip'
Invoke-WebRequest 'https://github.com/lexxmark/winflexbison/releases/download/v2.5.25/win_flex_bison-2.5.25.zip' -OutFile $archive
if ((Get-FileHash $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne '8d324b62be33604b2c45ad1dd34ab93d722534448f55a16ca7292de32b6ac135') {
    throw 'WinFlexBison checksum mismatch'
}
$destination = Join-Path $env:RUNNER_TEMP 'winflexbison'
Expand-Archive $archive $destination
$destination | Out-File -FilePath $env:GITHUB_PATH -Append -Encoding utf8

# Host pkgconf avoids the removed MSYS2 i686 runtime used by the old baseline.
$pkgArch = if ($env:RUNNER_ARCH -eq 'ARM64') { 'arm64' } else { 'x64' }
$pkgHash = if ($pkgArch -eq 'arm64') { '6447773d59fca3260304aa8fe2f01c086ed93ee0e12f2c0e5c369fc86f588a7c' } else { '28cc9e44473a3ef277260f3dac20f58e2a667760393bd47dc309b6426e9bd727' }
$pkgMsi = Join-Path $env:RUNNER_TEMP 'pkgconf.msi'
Invoke-WebRequest "https://github.com/pkgconf/pkgconf/releases/download/pkgconf-3.0.10/pkgconf-$pkgArch-3.0.10.msi" -OutFile $pkgMsi
if ((Get-FileHash $pkgMsi -Algorithm SHA256).Hash.ToLowerInvariant() -ne $pkgHash) { throw 'pkgconf checksum mismatch' }
$pkgDir = Join-Path $env:RUNNER_TEMP 'pkgconf'
$install = Start-Process msiexec.exe -ArgumentList @('/a', "`"$pkgMsi`"", '/qn', "TARGETDIR=`"$pkgDir`"") -Wait -PassThru
if ($install.ExitCode -ne 0) { throw "pkgconf extraction failed: $($install.ExitCode)" }
$pkgExe = Get-ChildItem $pkgDir -Recurse -Filter pkgconf.exe | Select-Object -First 1
if (!$pkgExe) { throw 'pkgconf executable missing' }
"PKG_CONFIG=$($pkgExe.FullName)" | Out-File -FilePath $env:GITHUB_ENV -Append -Encoding utf8
$pkgExe.DirectoryName | Out-File -FilePath $env:GITHUB_PATH -Append -Encoding utf8
