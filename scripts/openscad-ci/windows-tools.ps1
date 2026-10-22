$ErrorActionPreference = 'Stop'
# Do not filter by the x86/x64 component: native ARM64 installations can omit it.
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$vs = & $vswhere -latest -products '*' -version '[17,18)' -property installationPath
if (!$vs) { throw 'Visual Studio 2022 installation not found' }
$hostArch = if ($env:RUNNER_ARCH -eq 'ARM64') { 'arm64' } else { 'x64' }
$devcmd = Join-Path $vs 'Common7\Tools\VsDevCmd.bat'
$command = "`"$devcmd`" -no_logo -arch=$env:BUILD_ARCH -host_arch=$hostArch && set"
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
