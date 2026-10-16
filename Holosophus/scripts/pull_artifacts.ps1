<#
.SYNOPSIS
  Pull Holosophus run artifacts off the VPS into a local folder.

.DESCRIPTION
  Streams the run's briefs, journal, result files and paper sources down in a
  single tar-over-ssh pass (no per-file scp round trips), then extracts them
  locally and writes a MANIFEST.txt describing what landed.

  Layout produced under -Dest:

      briefs/    iteration*_prompt.md, warm_restart_prompt.md, launch_*.sh
      logs/      iteration*.log, warm_restart_*.log, step10_paper_*.log
      results/   mle/results/*            (filtered by -Iteration)
      journal/   mle/log.jsonl
      paper/     main.tex, review.md, references.bib (+ pdf/figures on request)
      MANIFEST.txt

.EXAMPLE
  .\pull_artifacts.ps1
  Pull iteration-5 results plus all briefs, logs, journal and paper sources.

.EXAMPLE
  .\pull_artifacts.ps1 -Iteration all -IncludePdf
  Pull every iteration's results and the compiled PDF as well.

.EXAMPLE
  .\pull_artifacts.ps1 -Iteration iter3,iter4,iter5 -Dest D:\snapshots\run-aug18
#>
[CmdletBinding()]
param(
    [string]   $RemoteHost = 'admin@ai-researcher.tech',
    [string]   $RemoteRoot = '/opt/prod/ai_scientists/Holosophus',
    [string]   $Session    = 'workdir/_role_you_are_an_llm_agents_causal_decision-making__20260811_1805',

    # Which mle/results files to take: one or more iteration prefixes, or 'all'.
    [string[]] $Iteration  = @('iter5'),

    [string]   $Dest,

    [switch]   $IncludePdf,
    [switch]   $IncludeFigures,

    [int]      $ConnectTimeout = 20
)

$ErrorActionPreference = 'Stop'

if (-not $Dest) { $Dest = Join-Path $PSScriptRoot '..\workdir\pulled' }
$Dest = [IO.Path]::GetFullPath($Dest)

Write-Host "Host   : $RemoteHost"
Write-Host "Session: $Session"
Write-Host "Results: $($Iteration -join ', ')"
Write-Host "Dest   : $Dest"
Write-Host ''

# --- build the remote file-list expression --------------------------------

if ($Iteration -contains 'all') {
    $resultsGlob = 'ls -1 mle/results/* 2>/dev/null'
} else {
    $parts = $Iteration | ForEach-Object { "ls -1 mle/results/${_}_* 2>/dev/null" }
    $resultsGlob = $parts -join '; '
}

$paperFiles = @('paper/main.tex', 'paper/review.md', 'paper/references.bib')
if ($IncludePdf) { $paperFiles += 'paper/main.pdf' }
$paperList = ($paperFiles | ForEach-Object { "ls -1 $_ 2>/dev/null" }) -join '; '

$figuresCmd = if ($IncludeFigures) { 'find paper/figures -type f 2>/dev/null' } else { 'true' }

# Remote script. Backtick-escaped $ stays literal for the remote shell.
$remote = @"
set -u
cd '$RemoteRoot' || exit 1
SESS='$Session'

# stream 1: briefs + logs, rooted at the repo root
{
  ls -1 workdir/*_prompt.md      2>/dev/null
  ls -1 workdir/launch_*.sh      2>/dev/null
  ls -1 workdir/iteration*.log   2>/dev/null
  ls -1 workdir/warm_restart_*.log 2>/dev/null
  ls -1 workdir/step10_paper_*.log 2>/dev/null
} | tar czf /tmp/_holo_a.tgz -T - 2>/dev/null

# stream 2: run artifacts, rooted at the session dir
cd "`$SESS" || exit 1
{
  $resultsGlob
  ls -1 mle/log.jsonl 2>/dev/null
  $paperList
  $figuresCmd
} | tar czf /tmp/_holo_b.tgz -T - 2>/dev/null

# emit both, length-prefixed, then clean up
stat -c %s /tmp/_holo_a.tgz
stat -c %s /tmp/_holo_b.tgz
"@

Write-Host 'Packing on remote...' -NoNewline

# Ship the script as base64 so neither PowerShell's stdout encoding (BOM) nor
# CRLF line endings can corrupt it in transit.
$remoteLf = $remote -replace "`r`n", "`n"
$b64      = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($remoteLf))

$sizes = ssh -o BatchMode=yes -o ConnectTimeout=$ConnectTimeout $RemoteHost "echo $b64 | base64 -d | bash -s"
if ($LASTEXITCODE -ne 0) { throw "remote pack failed (exit $LASTEXITCODE)" }
Write-Host " done ($($sizes -join ' / ') bytes)."

$tmp = Join-Path ([IO.Path]::GetTempPath()) ("holo_pull_" + [Guid]::NewGuid().ToString('N').Substring(0,8))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null

try {
    foreach ($pair in @(@('_holo_a.tgz', 'briefs-logs'), @('_holo_b.tgz', 'run'))) {
        $name, $tag = $pair
        Write-Host "Fetching $tag..." -NoNewline
        $local = Join-Path $tmp $name
        scp -o BatchMode=yes -o ConnectTimeout=$ConnectTimeout -q "${RemoteHost}:/tmp/$name" $local
        if ($LASTEXITCODE -ne 0) { throw "scp of $name failed" }
        Write-Host (' {0:N0} KB' -f ((Get-Item $local).Length / 1KB))
    }

    New-Item -ItemType Directory -Force -Path $Dest | Out-Null
    $staging = Join-Path $tmp 'x'
    New-Item -ItemType Directory -Force -Path $staging | Out-Null

    tar -xzf (Join-Path $tmp '_holo_a.tgz') -C $staging
    tar -xzf (Join-Path $tmp '_holo_b.tgz') -C $staging

    # --- reshape into the documented layout -------------------------------

    $map = @{
        'briefs'  = { Get-ChildItem (Join-Path $staging 'workdir') -File -EA SilentlyContinue |
                        Where-Object { $_.Name -match '_prompt\.md$|^launch_.*\.sh$' } }
        'logs'    = { Get-ChildItem (Join-Path $staging 'workdir') -File -EA SilentlyContinue |
                        Where-Object { $_.Extension -eq '.log' } }
        'results' = { Get-ChildItem (Join-Path $staging 'mle\results') -File -EA SilentlyContinue }
        'journal' = { Get-ChildItem (Join-Path $staging 'mle') -Filter 'log.jsonl' -File -EA SilentlyContinue }
    }

    $counts = [ordered]@{}
    foreach ($k in $map.Keys) {
        $target = Join-Path $Dest $k
        New-Item -ItemType Directory -Force -Path $target | Out-Null
        $files = & $map[$k]
        foreach ($f in $files) { Copy-Item $f.FullName $target -Force }
        $counts[$k] = ($files | Measure-Object).Count
    }

    # paper keeps its own subtree (figures/ may come along)
    $paperSrc = Join-Path $staging 'paper'
    if (Test-Path $paperSrc) {
        $paperDst = Join-Path $Dest 'paper'
        New-Item -ItemType Directory -Force -Path $paperDst | Out-Null
        Copy-Item (Join-Path $paperSrc '*') $paperDst -Recurse -Force
        $counts['paper'] = (Get-ChildItem $paperDst -Recurse -File | Measure-Object).Count
    } else {
        $counts['paper'] = 0
    }

    # --- manifest ---------------------------------------------------------

    $stamp = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    $lines = @(
        "Holosophus artifact pull"
        "pulled_at_utc : $stamp"
        "remote_host   : $RemoteHost"
        "remote_root   : $RemoteRoot"
        "session       : $Session"
        "iterations    : $($Iteration -join ', ')"
        "include_pdf   : $([bool]$IncludePdf)"
        "include_figs  : $([bool]$IncludeFigures)"
        ""
        "counts:"
    )
    foreach ($k in $counts.Keys) { $lines += ("  {0,-8} {1}" -f $k, $counts[$k]) }
    $lines += ''
    $lines += 'files:'
    Get-ChildItem $Dest -Recurse -File |
        Where-Object { $_.Name -ne 'MANIFEST.txt' } |
        Sort-Object FullName |
        ForEach-Object {
            $lines += ("  {0,9}  {1}" -f $_.Length, $_.FullName.Substring($Dest.Length).TrimStart('\'))
        }

    $manifestPath = Join-Path $Dest 'MANIFEST.txt'
    [IO.File]::WriteAllLines($manifestPath, $lines)

    Write-Host ''
    foreach ($k in $counts.Keys) { Write-Host ("  {0,-8} {1,4} files" -f $k, $counts[$k]) }
    Write-Host ''
    Write-Host "Manifest: $manifestPath"
}
finally {
    ssh -o BatchMode=yes -o ConnectTimeout=$ConnectTimeout $RemoteHost 'rm -f /tmp/_holo_a.tgz /tmp/_holo_b.tgz' 2>$null | Out-Null
    Remove-Item $tmp -Recurse -Force -EA SilentlyContinue
}
