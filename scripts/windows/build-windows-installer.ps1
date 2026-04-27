param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$FrontendDir = Join-Path $ProjectRoot "frontend"
$DistDir = Join-Path $ProjectRoot "dist"
$StageDir = Join-Path $ProjectRoot "build\installer"
$ServerExe = Join-Path $DistDir "researchos-server.exe"
$SetupExe = Join-Path $DistDir "ResearchOS-Windows-Setup.exe"
$InstallerCmd = Join-Path $ProjectRoot "scripts\windows\install-researchos.cmd"
$SfxSource = Join-Path $ProjectRoot "scripts\windows\ResearchOSSfxInstaller.cs"
$StubExe = Join-Path $StageDir "ResearchOS-SfxStub.exe"
$PayloadZip = Join-Path $StageDir "payload.zip"

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = "python"
}

New-Item -ItemType Directory -Force -Path $DistDir, $StageDir | Out-Null

Push-Location $FrontendDir
try {
    npm run build
    if ($LASTEXITCODE -ne 0) {
        throw "Frontend build failed."
    }
}
finally {
    Pop-Location
}

Push-Location $ProjectRoot
try {
    $pyinstallerArgs = @("-m", "PyInstaller", "--noconfirm")
    if ($Clean) {
        $pyinstallerArgs += "--clean"
    }
    $pyinstallerArgs += "researchos-server.spec"
    & $Python @pyinstallerArgs
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller build failed."
    }
}
finally {
    Pop-Location
}

if (-not (Test-Path -LiteralPath $ServerExe)) {
    throw "Server executable was not created: $ServerExe"
}

$cscCandidates = @(
    (Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"),
    (Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe")
)
$Csc = $cscCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $Csc) {
    throw "C# compiler was not found. Install .NET Framework 4.x or Windows developer tools."
}

Copy-Item -LiteralPath $ServerExe -Destination (Join-Path $StageDir "researchos-server.exe") -Force
Copy-Item -LiteralPath $InstallerCmd -Destination (Join-Path $StageDir "install-researchos.cmd") -Force

& $Csc /nologo /optimize+ /target:exe "/out:$StubExe" /reference:System.IO.Compression.dll /reference:System.IO.Compression.FileSystem.dll $SfxSource
if ($LASTEXITCODE -ne 0) {
    throw "Self-extracting installer stub build failed."
}

Remove-Item -LiteralPath $PayloadZip, $SetupExe -Force -ErrorAction SilentlyContinue
Compress-Archive -LiteralPath `
    (Join-Path $StageDir "researchos-server.exe"), `
    (Join-Path $StageDir "install-researchos.cmd") `
    -DestinationPath $PayloadZip `
    -Force

Copy-Item -LiteralPath $StubExe -Destination $SetupExe -Force

$marker = [Text.Encoding]::ASCII.GetBytes("`nRESEARCHOS_SFX_PAYLOAD_V1`n")
$outStream = [IO.File]::Open($SetupExe, [IO.FileMode]::Append, [IO.FileAccess]::Write)
try {
    $outStream.Write($marker, 0, $marker.Length)
    $payloadStream = [IO.File]::OpenRead($PayloadZip)
    try {
        $payloadStream.CopyTo($outStream)
    }
    finally {
        $payloadStream.Dispose()
    }
}
finally {
    $outStream.Dispose()
}

Get-Item -LiteralPath $ServerExe, $SetupExe | Select-Object FullName, Length, LastWriteTime
