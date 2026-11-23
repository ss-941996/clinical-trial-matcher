[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern('^[a-z0-9]+(?:-[a-z0-9]+)*$')]
    [string]$Owner,

    [Parameter(Mandatory)]
    [ValidatePattern('^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$')]
    [string]$AppVersion,

    [ValidatePattern('^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$')]
    [string]$IndexVersion = $env:HYBRID_INDEX_VERSION,

    [switch]$AppOnly
)

$ErrorActionPreference = 'Stop'

function Invoke-Docker {
    & docker @args
    if ($LASTEXITCODE -ne 0) {
        throw "Docker command failed (exit ${LASTEXITCODE}): docker $($args -join ' ')"
    }
}

if (-not $AppOnly -and -not $IndexVersion) {
    throw 'Pass -IndexVersion or set HYBRID_INDEX_VERSION to the version used for the local build.'
}
if ($AppVersion -eq 'latest' -or (-not $AppOnly -and $IndexVersion -eq 'latest')) {
    throw 'Use explicit version tags for the app and index.'
}

$images = @(
    @{ Local = 'clinical-trial-matcher:hybrid'; Remote = "ghcr.io/$Owner/clinical-trial-matcher:${AppVersion}" }
)
if (-not $AppOnly) {
    $images = @(
        @{ Local = "clinical-trial-hybrid-index:${IndexVersion}"; Remote = "ghcr.io/$Owner/clinical-trial-hybrid-index:${IndexVersion}" }
    ) + $images
}

# Check all requested images before attempting any upload.
foreach ($image in $images) {
    $local = Invoke-Docker image inspect --format '{{json .}}' $image.Local | ConvertFrom-Json
    if ($local.Os -ne 'linux' -or $local.Architecture -ne 'amd64') {
        throw "Image $($image.Local) must be built for linux/amd64."
    }
    $image.Id = $local.Id
}

foreach ($image in $images) {
    Invoke-Docker tag $image.Id $image.Remote
    Invoke-Docker push $image.Remote
    Write-Host "Published $($image.Remote)"
}
