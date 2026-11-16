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
$project = [IO.Path]::GetFullPath($PSScriptRoot)
if (-not $DataDir) { $DataDir = "${project}-local" }
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

function Get-GitHubCli {
    $candidates = @(
        (Join-Path $env:ProgramFiles 'GitHub CLI\gh.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\GitHub CLI\gh.exe')
    )
    $gh = Find-Executable 'gh.exe' $candidates
    if (-not $gh) {
        Install-WinGetPackage 'GitHub.cli'
        $gh = Find-Executable 'gh.exe' $candidates
    }
    if (-not $gh) { throw 'GitHub CLI is installed but gh.exe could not be located.' }
    try {
        & $gh auth status --hostname github.com *> $null
        $authorized = $LASTEXITCODE -eq 0
    } catch {
        $authorized = $false
    }
    if (-not $authorized) {
        Write-Step 'Open the GitHub login link and authorize an account with access to the private resource repository.'
        Invoke-Program $gh @('auth', 'login', '--hostname', 'github.com', '--web') 'Signing in to GitHub'
    }
    return $gh
}

function Install-ResourcePackages([string]$Python) {
    $installed = Join-Path $DataDir "resources-$releaseTag.ready"
    $required = @('android\rootfs\game\libil2cpp.so', 'android\rootfs\game\stage\StageEntity.json',
                  'stage_update\stage_tables.json', 'idle_koma\unit_index.json', 'battle_ui_assets\maps')
    if ((Test-Path -LiteralPath $installed -PathType Leaf) -and
        -not ($required | Where-Object { -not (Test-Path -LiteralPath (Join-Path $artifacts $_)) })) {
        Write-Step "Using installed resource release $releaseTag"
        return
    }
    $bundled = Join-Path $project 'resource-packages'
    if (-not $ResourceArchiveDir -and
        -not ($resourcePackages.Keys | Where-Object {
            -not (Test-Path -LiteralPath (Join-Path $bundled $_) -PathType Leaf)
        })) {
        $ResourceArchiveDir = $bundled
        Write-Step 'Using the bundled resource archives'
    }
    $cache = Join-Path $DataDir "packages\$releaseTag"
    $paths = @{}
    $gh = $null
    foreach ($name in $resourcePackages.Keys) {
        $path = Join-Path $cache $name
        if ($ResourceArchiveDir) {
            $path = Join-Path $ResourceArchiveDir $name
            if (-not (Test-FileHash $path $resourcePackages[$name])) {
                throw "Missing archive or SHA-256 mismatch: $path"
            }
        } elseif (-not (Test-FileHash $path $resourcePackages[$name])) {
            if (-not $gh) { $gh = Get-GitHubCli }
            $null = New-Item -Path $cache -ItemType Directory -Force
            for ($attempt = 1; $attempt -le 3; $attempt++) {
                Write-Step "Downloading $name (attempt $attempt of 3)"
                & $gh release download $releaseTag --repo $releaseRepo --pattern $name --dir $cache --clobber
                if ($LASTEXITCODE -eq 0 -and (Test-FileHash $path $resourcePackages[$name])) { break }
                if ($attempt -eq 3) {
                    throw "Could not download a verified $name. Check that the signed-in account can access $releaseRepo."
                }
                Start-Sleep -Seconds $attempt
            }
        }
        $paths[$name] = $path
    }
    foreach ($name in $resourcePackages.Keys) {
        Invoke-Program $Python @('-B', '-m', 'zipfile', '-e', $paths[$name], $DataDir) "Extracting $name"
    }
    Set-Content -LiteralPath $installed -Value $releaseTag -Encoding ASCII
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
    $requirements = Join-Path $project 'requirements-runtime.txt'
    $requirementsHash = Get-Sha256 $requirements
    $dependencyMarker = Join-Path $venv 'requirements.sha256'
    if (-not (Test-Path -LiteralPath $dependencyMarker -PathType Leaf) -or
        (Get-Content -LiteralPath $dependencyMarker -Raw).Trim() -ne $requirementsHash) {
        Invoke-Program $python @('-m', 'pip', 'install', '--disable-pip-version-check', '--only-binary=:all:', '-r', $requirements) 'Installing Python dependencies'
        Set-Content -LiteralPath $dependencyMarker -Value $requirementsHash -Encoding ASCII
    }
    Install-ResourcePackages $python
    $zigDir = Join-Path $artifacts 'zig_env'
    $zig = Join-Path $zigDir 'ziglang\zig.exe'
    if (-not (Test-Path -LiteralPath $zig -PathType Leaf)) {
        Invoke-Program $python @('-m', 'pip', 'install', '--disable-pip-version-check', '--target', $zigDir, 'ziglang==0.15.2') 'Installing the ARM64 build toolchain'
    }
    Invoke-Program $python @('-B', (Join-Path $scripts 'prepare_demo_formation.py')) 'Preparing a local test deployment'

    $selected = $Backend
    if ($Backend -eq 'Auto') {
        try {
            & $python -B -c "import sys; sys.path.insert(0, 'original_battle_reuse'); from android_bridge import find_mumu; find_mumu()" *> $null
            $connected = $LASTEXITCODE -eq 0
        } catch {
            $connected = $false
        }
        $selected = if ($connected) { 'MuMu' } else { 'QEMU' }
    } elseif ($Backend -eq 'MuMu') {
        Invoke-Program $python @('-B', '-c', "import sys; sys.path.insert(0, 'original_battle_reuse'); from android_bridge import find_mumu; find_mumu()") 'Checking the MuMu connection'
    }
    if ($selected -eq 'QEMU') { Initialize-Qemu $python }
    $env:GGET_BATTLE_BACKEND = $selected
    Write-Step "Setup complete. Backend: $selected"
} catch {
    Write-Step "Setup failed: $($_.Exception.Message)"
    throw
} finally {
    Pop-Location
    $setupLock.Dispose()
}

if (-not $NoLaunch) {
    Invoke-Program $python @('-B', (Join-Path $scripts 'battle_ui.py')) 'Opening the battle simulator'
}
