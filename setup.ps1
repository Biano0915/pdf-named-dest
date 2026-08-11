<#
.SYNOPSIS
    First-time setup on a new machine.

.DESCRIPTION
    Creates the virtual environment and installs the pinned dependencies, then
    runs a self test so you know it works before pointing it at real files.

    The .venv folder is deliberately NOT something you copy between machines:
    it stores absolute paths, so a copied one breaks as soon as the location or
    the user changes. Always create it here.

.EXAMPLE
    .\setup.ps1

.EXAMPLE
    .\setup.ps1 -PythonExe "C:\Program Files\Python312\python.exe"
#>
param(
    # Which Python to build the environment from. The default asks the Windows
    # launcher for 3.12.
    [string]$PythonExe,

    # Skip the self test.
    [switch]$NoTest
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

Write-Host ''
Write-Host ('=' * 68)
Write-Host '  SETUP'
Write-Host ('=' * 68)

# --- locate a Python 3.12 ---------------------------------------------------
if (-not $PythonExe) {
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        $PythonExe = 'py'
        $pyArgs = @('-3.12')
    } else {
        $fallback = Get-Command python -ErrorAction SilentlyContinue
        if (-not $fallback) {
            Write-Error "No Python found. Install Python 3.12 from python.org, then re-run this script."
        }
        $PythonExe = $fallback.Source
        $pyArgs = @()
    }
} else {
    if (-not (Test-Path $PythonExe)) { Write-Error "Not found: $PythonExe" }
    $pyArgs = @()
}

$versionArgs = $pyArgs + @('-V')
$version = & $PythonExe @versionArgs
Write-Host "  base python    : $version"

if ($version -notmatch '3\.1[2-9]|3\.[2-9][0-9]') {
    Write-Warning "Built and verified on Python 3.12. $version may work, but is untested."
}

# --- create the environment -------------------------------------------------
$venvPython = Join-Path $here '.venv\Scripts\python.exe'

if (Test-Path $venvPython) {
    Write-Host '  .venv          : already exists, reusing it'
} else {
    Write-Host '  .venv          : creating'
    $venvArgs = $pyArgs + @('-m', 'venv', '.venv')
    & $PythonExe @venvArgs
    if (-not (Test-Path $venvPython)) { Write-Error 'Failed to create .venv' }
}

Write-Host '  dependencies   : installing (this takes a minute)'
& $venvPython -m pip install --quiet --upgrade pip
& $venvPython -m pip install --quiet -r requirements.txt
if ($LASTEXITCODE -ne 0) { Write-Error 'pip install failed' }

Write-Host ''
Write-Host '  installed:'
& $venvPython -m pip list --format=freeze | Where-Object { $_ -match '^(pikepdf|pypdf|PyYAML|reportlab)=' } | ForEach-Object { Write-Host "    $_" }

# --- prove it works ---------------------------------------------------------
if (-not $NoTest) {
    Write-Host ''
    Write-Host ('-' * 68)
    Write-Host '  SELF TEST'
    Write-Host ('-' * 68)

    $tmp = Join-Path $env:TEMP "pdf_named_dest_setup_$PID"
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    try {
        # Build the awkward-cases fixture, convert it, and check that every
        # destination still lands in exactly the same place.
        & $venvPython tests\make_fixtures.py | Out-Null
        & $venvPython -m pdf_named_dest.cli --mode convert `
            --input 'tests\fixtures\tricky.pdf' `
            --output (Join-Path $tmp 'converted.pdf') `
            --name-prefix 'PXD_' --name-pad-width 6 `
            --report (Join-Path $tmp 'report') | Out-Null
        $convertOk = ($LASTEXITCODE -eq 0)

        & $venvPython tests\verify_equivalence.py 'tests\fixtures\tricky.pdf' (Join-Path $tmp 'converted.pdf') | Out-Null
        $verifyOk = ($LASTEXITCODE -eq 0)

        if ($convertOk -and $verifyOk) {
            Write-Host '  [PASS] convert and destination equivalence'
        } else {
            Write-Host "  [FAIL] convert=$convertOk verify=$verifyOk"
            Write-Error 'Self test failed. Do not use this installation until it passes.'
        }
    } finally {
        Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item 'tests\fixtures' -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Write-Host ''
Write-Host ('=' * 68)
Write-Host '  READY'
Write-Host ('=' * 68)
Write-Host '  Put the PDFs to process in input_files\ and run:'
Write-Host ''
Write-Host '    .\.venv\Scripts\python.exe -m pdf_named_dest.cli --mode convert+split `'
Write-Host '        --input "input_files" --output "output_files" `'
Write-Host '        --name-prefix PXD_ --name-pad-width 6 `'
Write-Host '        --report "output_files\reports" --split-max-pages 6999'
Write-Host ''
Write-Host '  Read RUNBOOK.md before processing anything real.'
Write-Host ''
