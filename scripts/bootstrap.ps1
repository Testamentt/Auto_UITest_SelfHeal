# AutoAiSelfHeal bootstrap (Windows PowerShell 5.1+ / PowerShell 7+).
# ASCII-only on purpose: Windows PowerShell 5.1 reads BOM-less files as ANSI,
# which garbles non-ASCII text (see the encoding pitfall recorded in docs/archive/TODO.md).
# Chinese explanation lives in README.md instead.
<#
.SYNOPSIS
    One-command environment bootstrap (idempotent).

.DESCRIPTION
    1. Create .venv (Python 3.10+, prefer 3.12)
    2. pip install -e ".[dev,llm]"
    3. playwright install chromium (skip with -SkipBrowser when system Chrome is enough)
    4. Copy config/settings.example.yaml -> config/settings.yaml when missing
    5. git config core.hooksPath .githooks

    On Windows, pytest may fail to clean its temp dir (PermissionError) and exit 1
    even when all tests pass; run local tests with --basetemp=.cache/pytest-tmp.

.PARAMETER SkipDeps
    Skip pip install.

.PARAMETER SkipBrowser
    Skip playwright install.

.PARAMETER SkipHooks
    Skip git hooks configuration.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts/bootstrap.ps1
    powershell -ExecutionPolicy Bypass -File scripts/bootstrap.ps1 -SkipBrowser -SkipDeps
#>
[CmdletBinding()]
param(
    [switch]$SkipDeps,
    [switch]$SkipBrowser,
    [switch]$SkipHooks
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

function Write-Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Warn([string]$Message) { Write-Host "!!  $Message" -ForegroundColor Yellow }

# --- 1. Pick interpreter ---------------------------------------------------
Write-Step "Looking for Python 3.10+"
$python = $null
$candidates = @(
    @("py", "-3.12"),
    @("py", "-3"),
    @("python")
)
foreach ($candidate in $candidates) {
    $exe = $candidate[0]
    $exeArgs = @()
    if ($candidate.Length -gt 1) { $exeArgs = $candidate[1..($candidate.Length - 1)] }
    try {
        $output = & $exe @exeArgs --version 2>&1
        if ($LASTEXITCODE -eq 0 -and "$output" -match "Python 3\.(\d+)") {
            if ([int]$Matches[1] -ge 10) { $python = $candidate; break }
        }
    } catch {
        continue
    }
}
if (-not $python) {
    throw "Python 3.10+ not found (tried: py -3.12 / py -3 / python). Install Python and add it to PATH."
}
$pyExe = $python[0]
$pyArgs = @()
if ($python.Length -gt 1) { $pyArgs = $python[1..($python.Length - 1)] }
Write-Host "    using: $pyExe $($pyArgs -join ' ')"

# --- 2. Create venv --------------------------------------------------------
$venvPython = Join-Path $repo ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    Write-Step ".venv already exists, skip creation"
} else {
    Write-Step "Creating .venv"
    & $pyExe @pyArgs -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Failed to create .venv" }
}
if (-not (Test-Path $venvPython)) { throw "Missing $venvPython after venv creation" }

# --- 3. Install dependencies ----------------------------------------------
if ($SkipDeps) {
    Write-Warn "-SkipDeps: skipping pip install"
} else {
    Write-Step "Installing dependencies (framework + dev + llm extras)"
    & $venvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }
    & $venvPython -m pip install -e ".[dev,llm]"
    if ($LASTEXITCODE -ne 0) { throw "Dependency install failed (try .[dev] first, then .[llm])" }
}

# --- 4. Playwright browser -------------------------------------------------
if ($SkipBrowser) {
    Write-Warn "-SkipBrowser: skip playwright install (default channel=chrome needs system Chrome)"
} elseif (-not $SkipDeps) {
    Write-Step "Installing Playwright Chromium"
    & $venvPython -m playwright install chromium
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "playwright install failed; system Chrome (channel=chrome) may still work"
    }
}

# --- 5. Runtime config -----------------------------------------------------
$example = Join-Path $repo "config\settings.example.yaml"
$settings = Join-Path $repo "config\settings.yaml"
if (Test-Path $settings) {
    Write-Step "config/settings.yaml exists, keep it untouched"
} else {
    Write-Step "Copying config/settings.example.yaml -> config/settings.yaml"
    Copy-Item $example $settings
    Write-Warn "Edit config/settings.yaml and .env as needed (secrets stay in .env only)"
}

# --- 6. Commit hooks -------------------------------------------------------
if ($SkipHooks) {
    Write-Warn "-SkipHooks: skip git hooks configuration"
} else {
    Write-Step "Configuring commit-msg hook: core.hooksPath = .githooks"
    & git config core.hooksPath .githooks
    if ($LASTEXITCODE -ne 0) { Write-Warn "git config failed (not inside a git repo?)" }
}

Write-Host ""
Write-Host "Done. Suggested self-check:" -ForegroundColor Green
Write-Host "  .\.venv\Scripts\python.exe -m pytest -m unit --basetemp=.cache/pytest-tmp"
Write-Host "  .\.venv\Scripts\python.exe -m pytest -m `"e2e and not booking and not live`" --basetemp=.cache/pytest-tmp"
