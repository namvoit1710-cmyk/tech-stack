<#
.SYNOPSIS
    Creates a Python virtual environment at .wvenv and installs every requirements
    file found in the current directory and all subdirectories.

.DESCRIPTION
    Run this script from the directory you want to set up. It will:
      1. Create a virtual environment at <Root>\.wvenv (skipped if it already exists,
         unless -Force is given).
      2. Upgrade pip inside the venv.
      3. Recursively discover requirements files matching -Pattern (default
         "requirements*.txt"), skipping .wvenv, .venv, node_modules and .git.
      4. Install each requirements file using the venv's pip, continuing on failure.
      5. Print a summary and exit non-zero if any install failed.

.PARAMETER Root
    Directory to operate in. Defaults to the current working directory.

.PARAMETER Pattern
    Glob used to discover requirements files. Defaults to "requirements*.txt".

.PARAMETER Force
    Remove and recreate the .wvenv directory if it already exists.

.EXAMPLE
    pwsh -File .\setup-wvenv.ps1

.EXAMPLE
    pwsh -File .\setup-wvenv.ps1 -Force -Pattern "requirements*.txt"
#>
[CmdletBinding()]
param(
    [string]$Root = $PWD.Path,
    [string]$Pattern = 'requirements*.txt',
    [switch]$Force
)

$Root = (Resolve-Path -LiteralPath $Root).Path
$venvPath = Join-Path $Root '.wvenv'
$venvPython = Join-Path $venvPath 'Scripts\python.exe'
$excludeDirs = @('.wvenv', '.venv', 'node_modules', '.git')

# --- 1. Create the virtual environment -------------------------------------
if ((Test-Path $venvPath) -and $Force) {
    Write-Host "Removing existing venv at $venvPath ..." -ForegroundColor Yellow
    Remove-Item -LiteralPath $venvPath -Recurse -Force
}

if (Test-Path $venvPython) {
    Write-Host "Using existing venv at $venvPath" -ForegroundColor Cyan
}
else {
    # Pick a Python launcher: prefer the Windows 'py -3' launcher, fall back to 'python'.
    $createArgs = $null
    if (Get-Command 'py' -ErrorAction SilentlyContinue) {
        $createArgs = @('py', '-3')
    }
    elseif (Get-Command 'python' -ErrorAction SilentlyContinue) {
        $createArgs = @('python')
    }
    else {
        Write-Error "No Python interpreter found on PATH (tried 'py' and 'python')."
        exit 1
    }

    Write-Host "Creating virtual environment at $venvPath ..." -ForegroundColor Cyan
    & $createArgs[0] @($createArgs[1..($createArgs.Count - 1)]) -m venv $venvPath
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPython)) {
        Write-Error "Failed to create virtual environment."
        exit 1
    }
}

# --- 2. Upgrade pip ---------------------------------------------------------
Write-Host "Upgrading pip ..." -ForegroundColor Cyan
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    Write-Warning "pip upgrade reported a non-zero exit code; continuing anyway."
}

# --- 3. Discover requirements files ----------------------------------------
$reqFiles = Get-ChildItem -LiteralPath $Root -Recurse -File -Filter $Pattern |
    Where-Object {
        $parts = $_.FullName.Substring($Root.Length).Split([IO.Path]::DirectorySeparatorChar, [StringSplitOptions]::RemoveEmptyEntries)
        -not ($parts | Where-Object { $excludeDirs -contains $_ })
    } |
    Sort-Object FullName

if (-not $reqFiles) {
    Write-Host "No requirements files matching '$Pattern' found under $Root." -ForegroundColor Yellow
    Write-Host "Venv ready at $venvPath" -ForegroundColor Green
    exit 0
}

Write-Host ""
Write-Host "Found $($reqFiles.Count) requirements file(s):" -ForegroundColor Cyan
$reqFiles | ForEach-Object { Write-Host "  - $($_.FullName)" }
Write-Host ""

# --- 4. Install each requirements file -------------------------------------
$failed = @()
$index = 0
foreach ($file in $reqFiles) {
    $index++
    Write-Host "[$index/$($reqFiles.Count)] Installing $($file.FullName) ..." -ForegroundColor Cyan
    # Install from within the file's own directory so that relative requirements
    # (e.g. worker files using "-e ../worker-sdk") resolve against the requirements
    # file location rather than the script's working directory.
    Push-Location -LiteralPath $file.DirectoryName
    try {
        & $venvPython -m pip install -r $file.Name
        if ($LASTEXITCODE -ne 0) {
            throw "pip exited with code $LASTEXITCODE"
        }
        Write-Host "    OK" -ForegroundColor Green
    }
    catch {
        Write-Warning "    FAILED: $($file.FullName) -- $_"
        $failed += $file.FullName
    }
    finally {
        Pop-Location
    }
}

# --- 5. Summary -------------------------------------------------------------
Write-Host ""
Write-Host "==================== Summary ====================" -ForegroundColor Cyan
Write-Host "Venv:               $venvPath"
Write-Host "Requirements files: $($reqFiles.Count)"
Write-Host "Failed:             $($failed.Count)"
if ($failed.Count -gt 0) {
    $failed | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red }
}
Write-Host "Activate with:      .\.wvenv\Scripts\Activate.ps1" -ForegroundColor Green
Write-Host "================================================="

if ($failed.Count -gt 0) {
    exit 1
}
exit 0
