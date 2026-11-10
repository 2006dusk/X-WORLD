# Build the standalone analysis tools in tools\*.cpp.
#
# Each tool is a separate binary: they load their own model instance and
# attach their own eval callback, so they cannot share the server context.
# The server executable (build.ps1) is not touched here, which means this
# script can run while world-work-cpp.exe is serving requests.
#
# Usage: powershell -ExecutionPolicy Bypass -File .\build_tools.ps1

param(
    [switch]$Quiet,
    [switch]$ShowPaths,
    [string]$Native = '',
    [string]$Vcvars = ''
)

$ErrorActionPreference = 'Stop'

$ROOT      = Split-Path -Parent $MyInvocation.MyCommand.Path
$TOOLS     = Join-Path $ROOT 'tools'
$OUT       = Join-Path $ROOT 'build'
$LOCALJSON = Join-Path $ROOT 'local.paths.json'
. (Join-Path $ROOT 'paths.ps1')

$NATIVE = Resolve-LocalPath 'llamacpp_dir' 'XWORLD_LLAMACPP_DIR' $Native $LOCALJSON
$VCVARS = Resolve-LocalPath 'vcvars' 'XWORLD_VCVARS' $Vcvars $LOCALJSON
$INC    = Join-Path $NATIVE 'include'
$LIBOUT = Join-Path $NATIVE 'lib'

if ($ShowPaths) {
    Write-Host "llamacpp_dir : $NATIVE"
    Write-Host "include      : $INC"
    Write-Host "import libs  : $LIBOUT"
    Write-Host "vcvars       : $VCVARS"
    exit 0
}

if (-not (Test-Path $INC)) { throw "llama.cpp headers not found: $INC" }
if (-not (Test-Path $OUT)) { New-Item -ItemType Directory -Path $OUT -Force | Out-Null }

Write-Host "[t1/3] loading MSVC env"
$vc = & cmd /c "`"$VCVARS`" >nul 2>&1 && set"
foreach ($line in $vc) {
    if ($line -match '^([^=]+)=(.*)$') {
        [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process')
    }
}
if (-not (Get-Command cl.exe -ErrorAction SilentlyContinue)) { throw "cl.exe not found after vcvars64" }

Write-Host "[t2/3] checking import libs"
if (-not (Test-Path (Join-Path $LIBOUT 'llama.lib'))) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $NATIVE 'gen_libs.ps1') `
        '-Dumpbin' (Join-Path (Split-Path (Get-Command cl.exe).Source) 'dumpbin.exe') `
        '-Lib' (Join-Path (Split-Path (Get-Command cl.exe).Source) 'lib.exe') `
        '-Bin' (Join-Path $NATIVE 'b11753') `
        '-Out' $LIBOUT
    if ($LASTEXITCODE -ne 0) { throw "import lib generation failed" }
}

$sources = Get-ChildItem -Path $TOOLS -Filter '*.cpp' | Sort-Object Name
if (-not $sources) { throw "no tool sources found in $TOOLS" }

Write-Host "[t3/3] compiling tools"
foreach ($src in $sources) {
    $exe = Join-Path $OUT ($src.BaseName + '.exe')
    Push-Location $OUT
    try {
        & cl.exe /nologo /std:c++17 /O2 /EHsc /W3 /utf-8 `
            "/I$INC" /Fe:"$exe" `
            $src.FullName `
            "/link" "/LIBPATH:$LIBOUT" llama.lib ggml.lib ggml-base.lib
        if ($LASTEXITCODE -ne 0) { throw "compile failed for $($src.Name) ($LASTEXITCODE)" }
    } finally {
        Pop-Location
    }
    if (-not $Quiet) {
        $exeName = $src.BaseName + '.exe'
        Write-Host ("      {0,-24} {1,12:N0} bytes" -f $exeName, (Get-Item $exe).Length)
    }
}
Write-Host "TOOLS BUILD OK"
