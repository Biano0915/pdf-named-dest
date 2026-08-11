<#
.SYNOPSIS
    Run the converter over several input folders in one go.

.DESCRIPTION
    The tool already handles one folder at a time (--input pointed at a
    directory) and a whole tree (--recursive). This wrapper is for the case
    those do not cover: folders scattered in different places, each needing its
    own output folder.

    Every folder is processed independently. One failing folder does not stop
    the rest, and the exit code is non-zero if anything failed anywhere.

.PARAMETER InputFolders
    The folders to process. Use this when the folders are not under one root.

.PARAMETER InputRoot
    A single root holding sub-folders. Every immediate sub-folder containing
    PDFs is processed as its own job.

.PARAMETER OutputRoot
    Where results go. Each input folder gets a sub-folder named after it.

.PARAMETER Config
    Optional YAML config passed through to the tool. Use it to keep the naming
    parameters in one audited place; per-folder paths are still overridden here.

.EXAMPLE
    .\run_batch.ps1 -InputRoot "D:\studies" -OutputRoot "D:\out"

.EXAMPLE
    .\run_batch.ps1 -InputFolders "D:\project_a","E:\project_b\pdfs" -OutputRoot "D:\out"

.EXAMPLE
    .\run_batch.ps1 -InputRoot "D:\studies" -OutputRoot "D:\out" -SkipExisting
#>
[CmdletBinding(DefaultParameterSetName = 'Root')]
param(
    [Parameter(Mandatory = $true, ParameterSetName = 'List')]
    [string[]]$InputFolders,

    [Parameter(Mandatory = $true, ParameterSetName = 'Root')]
    [string]$InputRoot,

    [Parameter(Mandatory = $true)]
    [string]$OutputRoot,

    [string]$Config,

    [ValidateSet('inspect', 'convert', 'split', 'convert+split')]
    [string]$Mode = 'convert+split',

    # These three decide every generated name. Keep them stable across runs:
    # changing one changes every name, and parts made with different settings
    # will not link up when merged.
    [string]$NamePrefix = 'PXD_',
    [int]$NamePadWidth = 6,
    [int]$MaxPages = 6999,

    [ValidateSet('pages', 'outline')]
    [string]$Align = 'pages',

    [ValidateSet('first', 'own', 'all', 'none')]
    [string]$Outlines = 'first',

    # Skip folders whose output already exists, to resume an interrupted run.
    [switch]$SkipExisting,

    # Also pick up PDFs in sub-folders of each input folder.
    [switch]$Recursive,

    [string]$Python = '.\.venv\Scripts\python.exe'
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path $Python)) {
    Write-Error "Python not found at $Python. Run this from the project folder, or pass -Python."
}

# --- work out the list of jobs ---------------------------------------------
if ($PSCmdlet.ParameterSetName -eq 'Root') {
    if (-not (Test-Path $InputRoot)) { Write-Error "InputRoot not found: $InputRoot" }
    $folders = Get-ChildItem $InputRoot -Directory | Where-Object {
        (Get-ChildItem $_.FullName -Filter *.pdf -File -Recurse:$Recursive |
            Select-Object -First 1) -ne $null
    } | Select-Object -ExpandProperty FullName

    if (-not $folders) {
        # No sub-folders with PDFs; maybe the root itself holds them.
        if (Get-ChildItem $InputRoot -Filter *.pdf -File | Select-Object -First 1) {
            $folders = @((Resolve-Path $InputRoot).Path)
        }
    }
} else {
    $folders = @()
    foreach ($f in $InputFolders) {
        if (Test-Path $f) {
            $folders += (Resolve-Path $f).Path
        } else {
            Write-Warning "skipping missing folder: $f"
        }
    }
}

if (-not $folders) {
    Write-Error 'No input folders with PDFs found.'
}

Write-Host ''
Write-Host ('=' * 72)
Write-Host '  MULTI-FOLDER RUN'
Write-Host ('=' * 72)
Write-Host "  mode           : $Mode"
Write-Host "  folders        : $($folders.Count)"
Write-Host "  output root    : $OutputRoot"
Write-Host "  name prefix    : $NamePrefix"
Write-Host "  pad width      : $NamePadWidth"
Write-Host "  max pages      : $MaxPages"
Write-Host "  align          : $Align"
Write-Host "  outlines       : $Outlines"
if ($Config) { Write-Host "  config         : $Config" }
Write-Host ''

# --- run each folder --------------------------------------------------------
$results = @()
$n = 0
foreach ($folder in $folders) {
    $n++
    $leaf = Split-Path $folder -Leaf
    $outDir = Join-Path $OutputRoot $leaf
    $reportDir = Join-Path $outDir 'reports'

    Write-Host ('-' * 72)
    Write-Host "  [$n/$($folders.Count)] $leaf"
    Write-Host "           in  : $folder"
    Write-Host "           out : $outDir"
    Write-Host ('-' * 72)

    $cliArgs = @(
        '-m', 'pdf_named_dest.cli',
        '--mode', $Mode,
        '--input', $folder,
        '--output', $outDir,
        '--report', $reportDir,
        '--name-prefix', $NamePrefix,
        '--name-pad-width', $NamePadWidth,
        '--split-max-pages', $MaxPages,
        '--split-align', $Align,
        '--split-outlines', $Outlines
    )
    if ($Config) { $cliArgs += @('--config', $Config) }
    if ($SkipExisting) { $cliArgs += '--skip-existing' }
    if ($Recursive) { $cliArgs += '--recursive' }

    & $Python @cliArgs
    $code = $LASTEXITCODE

    # The per-folder summary carries the real counts; read it back if present.
    $files = 0; $ok = 0; $failed = 0; $skipped = 0
    $summaryPath = Join-Path $reportDir '_batch_summary.json'
    if (Test-Path $summaryPath) {
        $s = Get-Content $summaryPath -Raw | ConvertFrom-Json
        $files = $s.n_files; $ok = $s.n_ok
        $failed = $s.n_failed; $skipped = $s.n_skipped
    }

    if ($code -eq 0) { $status = 'ok' } elseif ($code -eq 1) { $status = 'partial' } else { $status = 'ERROR' }

    $results += [pscustomobject]@{
        Folder  = $leaf
        Files   = $files
        Ok      = $ok
        Skipped = $skipped
        Failed  = $failed
        Exit    = $code
        Status  = $status
        Output  = $outDir
    }
    Write-Host ''
}

# --- overall summary --------------------------------------------------------
Write-Host ('=' * 72)
Write-Host '  OVERALL'
Write-Host ('=' * 72)
$results | Format-Table Folder, Files, Ok, Skipped, Failed, Status -AutoSize

$totalFailed = ($results | Measure-Object -Property Failed -Sum).Sum
$badFolders = @($results | Where-Object { $_.Status -ne 'ok' })

$summaryFile = Join-Path $OutputRoot '_run_summary.json'
New-Item -ItemType Directory -Force -Path $OutputRoot | Out-Null
$results | ConvertTo-Json -Depth 4 | Out-File $summaryFile -Encoding utf8
Write-Host "  summary written: $summaryFile"

if ($badFolders.Count -gt 0) {
    Write-Host ''
    Write-Host "  $($badFolders.Count) folder(s) need attention:"
    foreach ($b in $badFolders) {
        Write-Host "    $($b.Folder)  (exit $($b.Exit), $($b.Failed) failed file(s))"
        Write-Host "      see $(Join-Path $b.Output 'reports\_batch_summary.txt')"
    }
    exit 1
}

Write-Host ''
Write-Host "  all folders completed, $totalFailed failed file(s)"
exit 0
