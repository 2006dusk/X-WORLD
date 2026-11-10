# Build world-work-cpp: a self-written llama.cpp compatible inference server.
#
# The server links against the llama.cpp b11753 CUDA DLLs. Their location comes
# from local.paths.json (or the XWORLD_LLAMACPP_DIR environment variable). No
# llama.cpp source is vendored here: only the public headers are used.
#
# Steps
#   1. import MSVC env (vcvars64)
#   2. make sure import libs exist (they are synthesized from the DLL exports)
#   3. compile + link all src\*.cpp into world-work-cpp.exe
#   4. stage the runtime DLLs next to the exe
#
# Usage: powershell -ExecutionPolicy Bypass -File .\build.ps1
#        powershell -ExecutionPolicy Bypass -File .\build.ps1 -NoDllStage

param(
    [switch]$NoDllStage,
    [switch]$ShowPaths,
    [string]$Native = '',
    [string]$Vcvars = '',
    [string]$CudaBin = ''
)

$ErrorActionPreference = 'Stop'

$ROOT      = Split-Path -Parent $MyInvocation.MyCommand.Path
$SRC       = Join-Path $ROOT 'src'
$OUT       = Join-Path $ROOT 'build'
$LOCALJSON = Join-Path $ROOT 'local.paths.json'
. (Join-Path $ROOT 'paths.ps1')

$NATIVE   = Resolve-LocalPath 'llamacpp_dir' 'XWORLD_LLAMACPP_DIR' $Native $LOCALJSON
$VCVARS   = Resolve-LocalPath 'vcvars' 'XWORLD_VCVARS' $Vcvars $LOCALJSON
$CUDA_BIN = Resolve-LocalPath 'cuda_bin' 'XWORLD_CUDA_BIN' $CudaBin $LOCALJSON
$INC      = Join-Path $NATIVE 'include'
$LIBOUT   = Join-Path $NATIVE 'lib'

if ($ShowPaths) {
    Write-Host "llamacpp_dir : $NATIVE"
    Write-Host "include      : $INC"
    Write-Host "import libs  : $LIBOUT"
    Write-Host "vcvars       : $VCVARS"
    Write-Host "cuda_bin     : $CUDA_BIN"
    exit 0
}

foreach ($d in @($OUT)) { if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null } }

if (-not (Test-Path $INC)) { throw "llama.cpp headers not found: $INC" }

# ---- 1. MSVC environment -------------------------------------------------
Write-Host "[1/4] loading MSVC env"
$vc = & cmd /c "`"$VCVARS`" >nul 2>&1 && set"
foreach ($line in $vc) {
    if ($line -match '^([^=]+)=(.*)$') {
        [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process')
    }
}
$msvcBin = Split-Path (Get-Command cl.exe -ErrorAction SilentlyContinue).Source
if (-not $msvcBin) { throw "cl.exe not found after vcvars64" }
Write-Host "      cl.exe : $msvcBin"

# ---- 2. import libraries -------------------------------------------------
Write-Host "[2/4] checking import libs"
if (-not (Test-Path (Join-Path $LIBOUT 'llama.lib'))) {
    Write-Host "      generating import libs from DLL export tables"
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $NATIVE 'gen_libs.ps1') `
        '-Dumpbin' (Join-Path $msvcBin 'dumpbin.exe') `
        '-Lib' (Join-Path $msvcBin 'lib.exe') `
        '-Bin' (Join-Path $NATIVE 'b11753') `
        '-Out' $LIBOUT
    if ($LASTEXITCODE -ne 0) { throw "import lib generation failed" }
} else {
    Write-Host "      reuse $LIBOUT"
}

# ---- 3. compile + link ---------------------------------------------------
Write-Host "[3/4] compiling world-work-cpp.exe"
$sources = Get-ChildItem -Path $SRC -Filter '*.cpp' | ForEach-Object { $_.FullName }
if (-not $sources) { throw "no sources found in $SRC" }
$exe = Join-Path $OUT 'world-work-cpp.exe'
Push-Location $OUT
try {
    & cl.exe /nologo /std:c++17 /O2 /EHsc /W3 /utf-8 `
        "/I$INC" /Fe:"$exe" `
        $sources `
        "/link" "/LIBPATH:$LIBOUT" llama.lib ggml.lib ggml-base.lib ws2_32.lib
    if ($LASTEXITCODE -ne 0) { throw "compile/link failed ($LASTEXITCODE)" }
} finally {
    Pop-Location
}
if (-not (Test-Path $exe)) { throw "exe not produced" }
Write-Host ("      {0}  {1:N0} bytes" -f $exe, (Get-Item $exe).Length)

# The activation capture tool is a separate binary: it loads its own context
# with an eval callback attached, so it cannot share the server's context.
$toolsPs1 = Join-Path $ROOT 'build_tools.ps1'
if (Test-Path $toolsPs1) {
    Write-Host "[3b] building standalone analysis tools"
    & powershell -NoProfile -ExecutionPolicy Bypass -File $toolsPs1
    if ($LASTEXITCODE -ne 0) { throw "tools build failed ($LASTEXITCODE)" }
}

# ---- 4. stage runtime DLLs ----------------------------------------------
if ($NoDllStage) {
    Write-Host "[4/4] skipped (runtime DLLs not staged)"
} else {
    Write-Host "[4/4] staging runtime DLLs"
    $staged = Join-Path $NATIVE 'build'
    foreach ($f in @('llama.dll','ggml.dll','ggml-base.dll','ggml-cuda.dll','ggml-cpu-alderlake.dll','ggml-cpu-x64.dll','libomp.dll')) {
        $src = Join-Path $NATIVE ('b11753\' + $f)
        if (-not (Test-Path $src)) { $src = Join-Path $staged $f }
        if (Test-Path $src) { Copy-Item $src $OUT -Force } else { Write-Warning "missing dll: $f" }
    }
    foreach ($f in @('cudart64_12.dll','cublas64_12.dll','cublasLt64_12.dll')) {
        $src = Join-Path $CUDA_BIN $f
        if (Test-Path $src) { Copy-Item $src $OUT -Force } else {
            $alt = Join-Path $staged $f
            if (Test-Path $alt) { Copy-Item $alt $OUT -Force } else { Write-Warning "missing cuda dll: $f" }
        }
    }
    Get-ChildItem $OUT -Filter '*.dll' | ForEach-Object { Write-Host ("      {0,-28} {1,12:N0}" -f $_.Name, $_.Length) }
}
Write-Host "BUILD OK -> $exe"
