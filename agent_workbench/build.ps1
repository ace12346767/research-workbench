param([switch]$SkipInstaller)

$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$temp = Join-Path $project '.tmp\build'
New-Item -ItemType Directory -Force -Path $temp | Out-Null
$env:TEMP = $temp
$env:TMP = $temp
$env:PYINSTALLER_CONFIG_DIR = Join-Path $project '.tmp\pyinstaller-cache'

$python = Join-Path $project '.venv\Scripts\python.exe'
& node --test (Join-Path $project 'tests\web\markdown.test.cjs')
if ($LASTEXITCODE -ne 0) { throw 'Web UI tests failed; build stopped. Install development dependencies with npm ci in agent_workbench.' }
& $python -m pytest (Join-Path $project 'tests') -q
if ($LASTEXITCODE -ne 0) { throw 'Tests failed; build stopped.' }
& $python -m PyInstaller --noconfirm --clean --distpath (Join-Path $project 'dist') --workpath (Join-Path $project 'build') (Join-Path $project 'agent_workbench.spec')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed; installer build stopped.' }

if (-not $SkipInstaller) {
    & (Join-Path $project 'scripts\prepare_webview2.ps1')
    $iscc = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($null -eq $iscc) {
        $candidates = @(
            'D:\Tools\Inno Setup 6\ISCC.exe',
            (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
            'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
            'C:\Program Files\Inno Setup 6\ISCC.exe'
        )
        foreach ($candidate in $candidates) {
            if (Test-Path -LiteralPath $candidate) {
                $iscc = Get-Command -Name $candidate
                break
            }
        }
    }
    if ($null -ne $iscc) {
        Push-Location $project
        try {
            & $iscc.Source (Join-Path $project 'installer.iss')
            if ($LASTEXITCODE -ne 0) { throw 'Inno Setup compilation failed.' }
        }
        finally { Pop-Location }
    } else {
        throw 'Inno Setup 6 was not found. Install it or run build.ps1 -SkipInstaller for an onedir-only build.'
    }
}
