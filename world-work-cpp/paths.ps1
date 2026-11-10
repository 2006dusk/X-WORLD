# Resolve machine-local paths for the build scripts.
# Order: script parameter, then environment variable, then local.paths.json
# next to the scripts. The repository carries no machine path.
function Resolve-LocalPath {
    param(
        [Parameter(Mandatory = $true)][string]$Key,
        [Parameter(Mandatory = $true)][string]$EnvName,
        [string]$Override = '',
        [Parameter(Mandatory = $true)][string]$LocalJson
    )
    if ($Override) { return $Override }
    if (Test-Path "Env:$EnvName") {
        $v = [Environment]::GetEnvironmentVariable($EnvName)
        if ($v) { return $v }
    }
    if (Test-Path -LiteralPath $LocalJson) {
        try {
            $data = Get-Content -LiteralPath $LocalJson -Raw -Encoding UTF8 | ConvertFrom-Json
        } catch {
            throw "cannot read $LocalJson : $($_.Exception.Message)"
        }
        $v = $data.$Key
        if ($v) { return [string]$v }
    }
    throw "$Key is not set: pass the parameter, export $EnvName, or put `"$Key`" in $LocalJson"
}
