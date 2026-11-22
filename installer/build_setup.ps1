[CmdletBinding()]
param(
    [string]$CompilerPath = 'ISCC.exe'
)

$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$python = Join-Path $project '.venv\Scripts\python.exe'
$compiler = Get-Command -Name $CompilerPath -CommandType Application -ErrorAction SilentlyContinue |
    Select-Object -First 1

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "The project Python environment is missing at $python."
}
if (-not $compiler) {
    throw 'Inno Setup compiler not found. Pass the full path to ISCC.exe with -CompilerPath.'
}

$setupBuild = Join-Path $project 'build\setup'
$payloadDir = Join-Path $setupBuild 'payload'
$outputDir = Join-Path $setupBuild 'output'
$workDir = Join-Path $setupBuild 'pyinstaller'
$setupScript = Join-Path $PSScriptRoot 'PDFUtilitySuite.iss'
$spec = Join-Path $project 'PDF-Merger-Compressor.spec'
$null = New-Item -ItemType Directory -Path $payloadDir, $outputDir, $workDir -Force

$previousCache = $env:PYINSTALLER_CONFIG_DIR
Push-Location -LiteralPath $project
try {
    $env:PYINSTALLER_CONFIG_DIR = Join-Path $setupBuild 'pyinstaller-cache'
    $version = & $python -B -c 'from config import APP_VERSION; print(APP_VERSION)'
    if ($LASTEXITCODE -ne 0 -or $version -notmatch '^\d+\.\d+\.\d+$') {
        throw 'Could not read the application version from config.py.'
    }

    & $python -B -m PyInstaller --noconfirm --distpath $payloadDir --workpath $workDir $spec
    if ($LASTEXITCODE -ne 0) {
        throw "Packaging the application failed with exit code $LASTEXITCODE."
    }

    $appExe = Join-Path $payloadDir 'PDF-Merger-Compressor.exe'
    & $compiler.Source "--define=AppVersion=$version" "--define=AppExe=$appExe" "/O$outputDir" $setupScript
    if ($LASTEXITCODE -ne 0) {
        throw "Compiling Setup failed with exit code $LASTEXITCODE."
    }

    Write-Output (Join-Path $outputDir "PDF-Utility-Suite-Setup-$version.exe")
} finally {
    $env:PYINSTALLER_CONFIG_DIR = $previousCache
    Pop-Location
}
