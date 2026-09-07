param([string]$Destination = (Join-Path $PSScriptRoot '..\.tmp\prerequisites'))
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path $Destination | Out-Null
$target = Join-Path $Destination 'MicrosoftEdgeWebview2Setup.exe'
$url = 'https://go.microsoft.com/fwlink/p/?LinkId=2124703'
if (-not (Test-Path -LiteralPath $target)) {
    $partial = $target + '.partial'
    Invoke-WebRequest -Uri $url -OutFile $partial
    Move-Item -LiteralPath $partial -Destination $target
}
$signature = Get-AuthenticodeSignature -LiteralPath $target
if ($signature.Status -ne 'Valid' -or $null -eq $signature.SignerCertificate -or
    $signature.SignerCertificate.GetNameInfo([System.Security.Cryptography.X509Certificates.X509NameType]::SimpleName, $false) -ne 'Microsoft Corporation') {
    throw 'WebView2 bootstrapper must have a valid Microsoft Corporation Authenticode signature. Build stopped.'
}
$size = (Get-Item -LiteralPath $target).Length
# Prevent accidentally shipping the full runtime in the standard package.
if ($size -ge 50MB) { throw 'Expected the small WebView2 bootstrapper for the standard package. Build stopped.' }
$hash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
[pscustomobject]@{ source=$url; package='Evergreen Bootstrapper'; file=(Split-Path $target -Leaf); bytes=$size; sha256=$hash; signature='Valid'; publisher='Microsoft Corporation'; verified_at=(Get-Date).ToString('o') } |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Destination 'webview2-source.json') -Encoding UTF8
Write-Output "Verified Microsoft WebView2 bootstrapper ($size bytes) SHA256: $hash"
