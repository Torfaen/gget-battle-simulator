[CmdletBinding()]
param(
    [string]$DataDir = $env:GGET_BATTLE_DATA_DIR,
    [string]$ResourceArchiveDir,
    [ValidateSet('Auto', 'MuMu', 'QEMU')]
    [string]$Backend = 'Auto',
    [switch]$NoLaunch
)

$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
# WinGet emits UTF-8 when piped. Match it and keep redirected Python output consistent.
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$env:PYTHONIOENCODING = 'utf-8'
$project = [IO.Path]::GetFullPath($PSScriptRoot)
$savedDataDir = Join-Path $project '.gget-data-dir'
if (-not $DataDir) {
    $legacy = "${project}-local"
    $parent = Split-Path -Parent $project
    $shared = Join-Path $parent 'gget-battle-simulator-local'
    if (Test-Path -LiteralPath $savedDataDir -PathType Leaf) {
        $saved = ([string](Get-Content -LiteralPath $savedDataDir -Raw -Encoding UTF8)).Trim()
        if ($saved -and (Test-Path -LiteralPath $saved -PathType Container)) { $DataDir = $saved }
    }
    if (-not $DataDir -and (Test-Path -LiteralPath $legacy -PathType Container)) { $DataDir = $legacy }
    if (-not $DataDir -and (Test-Path -LiteralPath $shared -PathType Container)) { $DataDir = $shared }
    if (-not $DataDir) {
        $nearby = @(Get-ChildItem -LiteralPath $parent -Directory -Filter 'gget-battle-simulator*-local' |
            Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'setup.log') -PathType Leaf })
        if ($nearby.Count -eq 1) { $DataDir = $nearby[0].FullName }
        elseif ($nearby.Count -gt 1) {
            throw "Multiple previous data directories were found. Run setup.cmd -DataDir with one of: $($nearby.FullName -join ', ')"
        } else { $DataDir = $shared }
    }
}
$DataDir = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($DataDir)
if ($ResourceArchiveDir) {
    $ResourceArchiveDir = (Resolve-Path -LiteralPath $ResourceArchiveDir).ProviderPath
}
$projectPrefix = $project.TrimEnd([char]'\') + '\'
if ($DataDir.TrimEnd([char]'\') -eq $project.TrimEnd([char]'\') -or
    $DataDir.StartsWith($projectPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Choose a data directory outside the source checkout.'
}

$env:GGET_BATTLE_DATA_DIR = $DataDir
$null = New-Item -Path $DataDir -ItemType Directory -Force
$log = Join-Path $DataDir 'setup.log'
$artifacts = Join-Path $DataDir 'original_battle_reuse\artifacts'
$scripts = Join-Path $project 'original_battle_reuse'
$releaseRepo = 'Torfaen/gget-battle-simulator-resources'
$releaseTag = '2026.11.16'
$resourcePackages = [ordered]@{
    'battle-ui-assets.zip' = 'a3f112b221d3caa2128d30d318aec960a8c189640b448b6c3ef9aa0c35a79d40'
    'battle-runtime-data.zip' = '9048c2cf0b98caceb5e4bf110c2f4a4bf93eb75ea03cc4df25f952292dc0286f'
    'gget-unit-portraits.zip' = 'df506d98e0e58e2ac6abbb517a2f03b17b13e912ed68e0a5953606112fd73c93'
}

function Write-Step([string]$Message) {
    Write-Host $Message
    Add-Content -LiteralPath $log -Value "[$(Get-Date -Format s)] $Message" -Encoding UTF8
}

function Invoke-Program([string]$Executable, [string[]]$ArgumentList, [string]$Step) {
    Write-Step $Step
    & $Executable @ArgumentList | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE. See the console output and $log."
    }
}

function Find-Executable([string]$Name, [string[]]$Candidates = @()) {
    $command = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) { return $command.Source }
    foreach ($candidate in $Candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
}

function Install-WinGetPackage([string]$Id, [string[]]$Options = @()) {
    $winget = Find-Executable 'winget.exe'
    if (-not $winget) {
        throw "WinGet is required to install $Id. Install or update Windows App Installer, or install $Id manually."
    }
    $arguments = @('install', '--id', $Id, '--exact', '--source', 'winget', '--architecture', 'x64',
                   '--accept-package-agreements', '--accept-source-agreements') + $Options
    Invoke-Program $winget $arguments "Installing $Id"
    $env:Path = $env:Path + ';' + [Environment]::GetEnvironmentVariable('Path', 'Machine') +
        ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}

function Find-Python {
    $launcher = Find-Executable 'py.exe'
    $python = Find-Executable 'python.exe' @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
        (Join-Path $env:ProgramFiles 'Python312\python.exe')
    )
    $candidates = @()
    if ($launcher) {
        $candidates += @{Exe = $launcher; Flags = @('-3.12')}
        $candidates += @{Exe = $launcher; Flags = @('-3')}
    }
    if ($python -and $python -notlike '*\Microsoft\WindowsApps\python.exe') {
        $candidates += @{Exe = $python; Flags = @()}
    }
    foreach ($relative in @('Programs\Python\Python312\python.exe', 'Programs\Python\Python311\python.exe')) {
        $installed = Join-Path $env:LOCALAPPDATA $relative
        if (Test-Path -LiteralPath $installed -PathType Leaf) {
            $candidates += @{Exe = $installed; Flags = @()}
        }
    }
    $probe = "import json, sys, sysconfig, tkinter; assert sys.version_info >= (3, 10) and sysconfig.get_platform() == 'win-amd64'; print(json.dumps(dict(path=sys.executable)))"
    foreach ($candidate in $candidates) {
        try {
            $flags = $candidate.Flags
            $result = & $candidate.Exe @flags -c $probe 2>$null
            if ($LASTEXITCODE -eq 0 -and $result) {
                return ($result | Select-Object -Last 1 | ConvertFrom-Json).path
            }
        } catch {
            continue
        }
    }
}

function Get-Sha256([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '')
    } finally {
        $sha.Dispose()
        $stream.Dispose()
    }
}

function Test-FileHash([string]$Path, [string]$Sha256) {
    return (Test-Path -LiteralPath $Path -PathType Leaf) -and (Get-Sha256 $Path) -eq $Sha256
}

function Get-ResourceArchive([string]$Python, [string]$Name) {
    $bundled = Join-Path $project 'resource-packages'
    foreach ($directory in @($ResourceArchiveDir, $bundled)) {
        if (-not $directory) { continue }
        $path = Join-Path $directory $Name
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            if (-not (Test-FileHash $path $resourcePackages[$Name])) {
                throw "Resource archive SHA-256 mismatch: $path"
            }
            Write-Step "Using local archive $Name"
            return $path
        }
    }
    $cache = Join-Path $DataDir "packages\$releaseTag"
    $path = Join-Path $cache $Name
    if (Test-FileHash $path $resourcePackages[$Name]) { return $path }
    $null = New-Item -Path $cache -ItemType Directory -Force
    $url = "https://github.com/$releaseRepo/releases/download/$releaseTag/$Name"
    $helper = Join-Path $project 'setup_resources.py'
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        Write-Step "Downloading $Name (attempt $attempt of 3)"
        & $Python -B $helper download $url $path | Out-Host
        if ($LASTEXITCODE -eq 0 -and (Test-FileHash $path $resourcePackages[$Name])) { return $path }
        if ($attempt -lt 3) { Start-Sleep -Seconds $attempt }
    }
    throw "Could not download a verified $Name from $url. Check your connection and rerun setup."
}

function Install-ResourcePackages([string]$Python) {
    $installed = Join-Path $DataDir "resources-$releaseTag.ready"
    $required = @('android\rootfs\game\libil2cpp.so', 'android\rootfs\game\stage\StageEntity.json',
                  'stage_update\stage_tables.json', 'idle_koma\unit_index.json', 'battle_ui_assets\maps')
    if ((Test-Path -LiteralPath $installed -PathType Leaf) -and
        -not ($required | Where-Object { -not (Test-Path -LiteralPath (Join-Path $artifacts $_)) })) {
        Write-Step "Using installed resource release $releaseTag"
    } else {
        foreach ($name in @('battle-ui-assets.zip', 'battle-runtime-data.zip')) {
            $path = Get-ResourceArchive $Python $name
            Invoke-Program $Python @('-B', '-m', 'zipfile', '-e', $path, $DataDir) "Extracting $name"
        }
        Set-Content -LiteralPath $installed -Value $releaseTag -Encoding ASCII
    }
    $name = 'gget-unit-portraits.zip'
    $portraitDir = Join-Path $artifacts 'battle_ui_assets\unit_portraits'
    $portraitMarker = Join-Path $portraitDir 'archive.sha256'
    if ((Test-Path -LiteralPath $portraitMarker -PathType Leaf) -and
        (Test-Path -Path (Join-Path $portraitDir 'ub_*.png') -PathType Leaf) -and
        (Get-Content -LiteralPath $portraitMarker -Raw).Trim() -eq $resourcePackages[$name]) {
        Write-Step 'Using the installed unit portraits'
        return
    }
    $path = Get-ResourceArchive $Python $name
    Invoke-Program $Python @('-B', (Join-Path $project 'setup_resources.py'), 'portraits', $path, $portraitDir) "Extracting $name"
    Set-Content -LiteralPath $portraitMarker -Value $resourcePackages[$name] -Encoding ASCII
}

function Cache-BundledUpdatePacks {
    $bundled = Join-Path $project 'resource-packages\updates'
    if (-not (Test-Path -LiteralPath $bundled -PathType Container)) { return }
    $cache = Join-Path $artifacts 'art-update-cache'
    foreach ($descriptor in @('art-update.json', 'roster-update.json')) {
        $path = Join-Path $artifacts "battle_ui_assets\$descriptor"
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { continue }
        $update = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($pack in $update.packs) {
            if ($pack.asset -notmatch '^[A-Za-z0-9_-]+\.zip$' -or $pack.sha256 -notmatch '^[a-f0-9]{64}$') { continue }
            $source = Join-Path $bundled $pack.asset
            $target = Join-Path $cache $pack.asset
            if ((Test-FileHash $target $pack.sha256) -or -not (Test-FileHash $source $pack.sha256)) { continue }
            Write-Step "Using bundled update pack $($pack.asset)"
            $null = New-Item -Path $cache -ItemType Directory -Force
            $pending = "$target.part"
            try {
                Copy-Item -LiteralPath $source -Destination $pending -Force
                Move-Item -LiteralPath $pending -Destination $target -Force
            } finally {
                if (Test-Path -LiteralPath $pending -PathType Leaf) { Remove-Item -LiteralPath $pending }
            }
        }
    }
}

function Initialize-Qemu([string]$Python) {
    $kernel = Join-Path $artifacts 'kernel'
    $manifest = Join-Path $kernel 'manifest.json'
    if ((Test-Path -LiteralPath $manifest -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $kernel 'Image') -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $kernel 'official\qemu-system-aarch64.exe') -PathType Leaf)) {
        $linux = (Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json).linux
        if ($linux.archive -and (Test-Path -LiteralPath (Join-Path $kernel "downloads\$($linux.archive)") -PathType Leaf)) {
            Write-Step 'Using the prepared QEMU runtime'
            return
        }
    }
    $candidates = @((Join-Path $env:ProgramFiles '7-Zip\7z.exe'), (Join-Path $kernel 'unpacker\7z.exe'))
    $sevenZip = Find-Executable '7z.exe' $candidates
    if (-not $sevenZip) {
        Install-WinGetPackage '7zip.7zip'
        $sevenZip = Find-Executable '7z.exe' $candidates
    }
    if (-not $sevenZip) { throw '7-Zip is installed but 7z.exe could not be located.' }
    Invoke-Program $Python @('-B', (Join-Path $scripts 'prepare_kernel.py'), '--seven-zip', $sevenZip) 'Preparing portable QEMU and its ARM64 kernel'
}

function Initialize-Tk([string]$Python) {
    $runtime = Join-Path $artifacts 'tk_runtime'
    if ((Test-Path -LiteralPath (Join-Path $runtime 'tcl8.6\init.tcl')) -and
        (Test-Path -LiteralPath (Join-Path $runtime 'tk8.6\tk.tcl'))) { return }
    try {
        & $Python -B -c 'import tkinter; tkinter.Tcl()' *> $null
        if ($LASTEXITCODE -eq 0) { return }
    } catch {
        # Stage the Tcl libraries locally if Python cannot resolve them in place.
    }
    $base = & $Python -B -c 'import json, sys; print(json.dumps(sys.base_prefix))' | ConvertFrom-Json
    $tcl = Join-Path $base 'tcl'
    if (-not (Test-Path -LiteralPath (Join-Path $tcl 'tcl8.6\init.tcl')) -or
        -not (Test-Path -LiteralPath (Join-Path $tcl 'tk8.6\tk.tcl'))) {
        throw 'Python could not start Tcl/Tk. Repair the x64 Python installation with Tcl/Tk support enabled.'
    }
    Write-Step 'Preparing the local Tcl/Tk desktop libraries'
    $null = New-Item -Path $runtime -ItemType Directory -Force
    $folders = @((Join-Path $tcl 'tcl8.6'), (Join-Path $tcl 'tk8.6'))
    Copy-Item -LiteralPath $folders -Destination $runtime -Recurse -Force
}

$lockPath = Join-Path $DataDir 'setup.lock'
try {
    $setupLock = [IO.File]::Open($lockPath, 'OpenOrCreate', 'ReadWrite', 'None')
} catch {
    throw "Could not lock $DataDir for setup. Close any other running setup and check directory permissions."
}
Push-Location -LiteralPath $project
try {
    Write-Step "Using data directory $DataDir"
    $venv = Join-Path $DataDir '.venv'
    $python = Join-Path $venv 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        $systemPython = Find-Python
        if (-not $systemPython) {
            Install-WinGetPackage 'Python.Python.3.12' @('--scope', 'user')
            $systemPython = Find-Python
        }
        if (-not $systemPython) { throw 'Python 3.10+ x64 with Tk could not be located.' }
        Invoke-Program $systemPython @('-m', 'venv', $venv) 'Creating the local Python environment'
    }
    Initialize-Tk $python
    Install-ResourcePackages $python
    Write-Step 'Checking for startup updates'
    $runtime = $project
    $resolved = & $python -B (Join-Path $project 'startup_update.py') $project $DataDir $log
    if ($LASTEXITCODE -eq 0 -and $resolved) {
        $runtime = $resolved | ConvertFrom-Json
    } else {
        Write-Step 'Update check failed; starting the bundled program'
    }
    Cache-BundledUpdatePacks
    $scripts = Join-Path $runtime 'original_battle_reuse'
    $requirements = Join-Path $runtime 'requirements-runtime.txt'
    $requirementsHash = Get-Sha256 $requirements
    $dependencyMarker = Join-Path $venv 'requirements.sha256'
    if (-not (Test-Path -LiteralPath $dependencyMarker -PathType Leaf) -or
        (Get-Content -LiteralPath $dependencyMarker -Raw).Trim() -ne $requirementsHash) {
        Invoke-Program $python @('-m', 'pip', 'install', '--disable-pip-version-check', '--only-binary=:all:', '-r', $requirements) 'Installing Python dependencies'
        Set-Content -LiteralPath $dependencyMarker -Value $requirementsHash -Encoding ASCII
    }
    $zigDir = Join-Path $artifacts 'zig_env'
    $zig = Join-Path $zigDir 'ziglang\zig.exe'
    if (-not (Test-Path -LiteralPath $zig -PathType Leaf)) {
        Invoke-Program $python @('-m', 'pip', 'install', '--disable-pip-version-check', '--target', $zigDir, 'ziglang==0.15.2') 'Installing the ARM64 build toolchain'
    }
    Invoke-Program $python @('-B', (Join-Path $scripts 'prepare_demo_formation.py')) 'Preparing a local test deployment'

    $selected = $Backend
    if ($Backend -eq 'Auto') {
        try {
            & $python -B -c 'import sys; sys.path.insert(0, sys.argv[1]); from android_bridge import find_mumu; find_mumu()' $scripts *> $null
            $connected = $LASTEXITCODE -eq 0
        } catch {
            $connected = $false
        }
        $selected = if ($connected) { 'MuMu' } else { 'QEMU' }
    } elseif ($Backend -eq 'MuMu') {
        Invoke-Program $python @('-B', '-c', 'import sys; sys.path.insert(0, sys.argv[1]); from android_bridge import find_mumu; find_mumu()', $scripts) 'Checking the MuMu connection'
    }
    if ($selected -eq 'QEMU') { Initialize-Qemu $python }
    $env:GGET_BATTLE_BACKEND = $selected
    try {
        Set-Content -LiteralPath $savedDataDir -Value $DataDir -Encoding UTF8
    } catch {
        Write-Step "Could not remember the data directory: $($_.Exception.Message)"
    }
    Write-Step "Setup complete. Backend: $selected"
} catch {
    Write-Step "Setup failed: $($_.Exception.Message)"
    throw
} finally {
    Pop-Location
    $setupLock.Dispose()
}

if (-not $NoLaunch) {
    Push-Location -LiteralPath $runtime
    try {
        Invoke-Program $python @('-B', (Join-Path $scripts 'battle_ui.py')) 'Opening the battle simulator'
    } finally {
        Pop-Location
    }
}
