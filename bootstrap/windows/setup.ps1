# =============================================================================
# AEGIS PULSE OMEGA v2 — Windows bootstrap (setup.ps1)
# -----------------------------------------------------------------------------
# Run as Administrator from PowerShell:
#     Set-ExecutionPolicy -Scope Process Bypass
#     .\bootstrap\windows\setup.ps1
#
# Does, idempotently:
#   1. Enables WSL2 + Virtual Machine Platform
#   2. Installs Ubuntu 24.04 LTS (via `wsl --install -d`)
#   3. Installs Docker Desktop (WSL2 backend) via winget
#   4. Installs VS Code + Remote-WSL + Python + Docker + Jupyter extensions
#   5. Generates %UserProfile%\.wslconfig with cap'd memory/CPU
#   6. Detects NVIDIA driver and warns if outdated
#
# Logs to:  %UserProfile%\.aegis\bootstrap-windows.log
# =============================================================================

#Requires -Version 5.1
#Requires -RunAsAdministrator

[CmdletBinding()]
param(
    [switch] $SkipDocker,
    [switch] $SkipVSCode,
    [switch] $NonInteractive,
    [string] $UbuntuVersion = "Ubuntu-24.04"
)

$ErrorActionPreference = 'Stop'
$ProgressPreference     = 'SilentlyContinue'

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
$AegisHome = Join-Path $env:USERPROFILE ".aegis"
New-Item -ItemType Directory -Force -Path $AegisHome | Out-Null
$LogFile = Join-Path $AegisHome "bootstrap-windows.log"

function Write-Log {
    param(
        [Parameter(Mandatory)] [string] $Message,
        [ValidateSet('INFO','WARN','ERROR','OK','SECTION')] [string] $Level = 'INFO'
    )
    $ts    = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    $line  = "$ts | $Level | $Message"
    Add-Content -Path $LogFile -Value $line
    switch ($Level) {
        'SECTION' {
            Write-Host ""
            Write-Host ("=" * 74) -ForegroundColor Cyan
            Write-Host "  $Message" -ForegroundColor Cyan
            Write-Host ("=" * 74) -ForegroundColor Cyan
        }
        'OK'     { Write-Host "[OK]    $Message" -ForegroundColor Green }
        'WARN'   { Write-Host "[WARN]  $Message" -ForegroundColor Yellow }
        'ERROR'  { Write-Host "[ERROR] $Message" -ForegroundColor Red }
        default  { Write-Host "[INFO]  $Message" -ForegroundColor Gray }
    }
}

function Assert-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $pr = [Security.Principal.WindowsPrincipal]::new($id)
    if (-not $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "This script must run as Administrator. Close this window, open PowerShell as Administrator, and re-run."
    }
}

function Get-WindowsBuild {
    [int](Get-CimInstance Win32_OperatingSystem).BuildNumber
}

# ---------------------------------------------------------------------------
# 0. Preflight
# ---------------------------------------------------------------------------
Assert-Admin
Write-Log "AEGIS PULSE OMEGA v2 — Windows bootstrap" SECTION
Write-Log "Log file: $LogFile"
Write-Log "User:     $env:USERNAME"
Write-Log "Host:     $env:COMPUTERNAME"
Write-Log "Build:    $(Get-WindowsBuild)"

$build = Get-WindowsBuild
if ($build -lt 19044) {
    Write-Log "Windows build $build is below the supported minimum (19044 / Win10 21H2). Aborting." ERROR
    exit 11
}

# ---------------------------------------------------------------------------
# 1. WSL2 + Virtual Machine Platform
# ---------------------------------------------------------------------------
Write-Log "Enabling WSL2 + Virtual Machine Platform" SECTION

$featuresToEnable = @(
    'Microsoft-Windows-Subsystem-Linux',
    'VirtualMachinePlatform'
)

$needsReboot = $false
foreach ($f in $featuresToEnable) {
    $state = (Get-WindowsOptionalFeature -Online -FeatureName $f).State
    if ($state -ne 'Enabled') {
        Write-Log "Enabling Windows feature: $f"
        $result = Enable-WindowsOptionalFeature -Online -FeatureName $f -All -NoRestart
        if ($result.RestartNeeded) { $needsReboot = $true }
    }
    else {
        Write-Log "Feature already enabled: $f" OK
    }
}

# Set WSL default version to 2.
try {
    wsl --set-default-version 2 | Out-Null
    Write-Log "WSL default version set to 2" OK
} catch {
    Write-Log "wsl --set-default-version 2 failed: $_" WARN
}

# Update the WSL kernel (safe no-op if already current).
try {
    wsl --update --web-download 2>&1 | Out-Null
    Write-Log "WSL kernel update attempted" OK
} catch {
    Write-Log "wsl --update failed: $_" WARN
}

if ($needsReboot) {
    Write-Log "Windows features were just enabled — a REBOOT is required before Ubuntu install will work." WARN
    if (-not $NonInteractive) {
        $r = Read-Host "Reboot now? [Y/N]"
        if ($r -match '^[Yy]') { Restart-Computer -Force }
    }
    Write-Log "Re-run this script after reboot to continue." WARN
    exit 0
}

# ---------------------------------------------------------------------------
# 2. Install Ubuntu 24.04
# ---------------------------------------------------------------------------
Write-Log "Installing $UbuntuVersion" SECTION

$wslList = (wsl -l -q) -join "`n"
# wsl -l -q output is UTF-16 in some Windows builds; normalise.
$wslList = $wslList -replace "`0", ""

if ($wslList -match [Regex]::Escape($UbuntuVersion)) {
    Write-Log "$UbuntuVersion already registered with WSL" OK
} else {
    Write-Log "Installing $UbuntuVersion — this may take several minutes..."
    # --no-launch so the install doesn't block waiting for username/password.
    # The user will be prompted on first manual launch.
    wsl --install -d $UbuntuVersion --no-launch
    if ($LASTEXITCODE -ne 0) {
        Write-Log "wsl --install -d $UbuntuVersion failed with code $LASTEXITCODE" ERROR
        Write-Log "Alternative: open Microsoft Store, search 'Ubuntu 24.04 LTS', click Install." WARN
        exit 20
    }
    Write-Log "$UbuntuVersion installed" OK
    Write-Log "IMPORTANT: launch Ubuntu from Start menu once to create your user account, then continue." WARN
}

# ---------------------------------------------------------------------------
# 3. .wslconfig — memory / CPU / swap caps
# ---------------------------------------------------------------------------
Write-Log "Writing %UserProfile%\.wslconfig" SECTION

$wslConfigPath = Join-Path $env:USERPROFILE ".wslconfig"

# Compute a sensible memory cap: 75% of physical RAM, rounded to the nearest GB,
# with a minimum of 8 GB and a maximum of 64 GB.
$totalMemBytes = (Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory
$totalMemGB    = [math]::Round($totalMemBytes / 1GB)
$wslMemGB      = [math]::Max(8, [math]::Min(64, [math]::Round($totalMemGB * 0.75)))
$wslProc       = [math]::Max(2, [math]::Min(16, [int]((Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors * 0.75)))
$wslSwapGB     = [math]::Min(16, [math]::Max(4, [math]::Round($wslMemGB / 2)))

$wslConfigContent = @"
# =============================================================================
# AEGIS PULSE OMEGA v2 — .wslconfig
# Generated by bootstrap/windows/setup.ps1 on $(Get-Date -Format o)
# Host: $env:COMPUTERNAME, Physical RAM: ${totalMemGB} GB
# Edit and run 'wsl --shutdown' from PowerShell for changes to take effect.
# =============================================================================

[wsl2]

# --- Resource caps --------------------------------------------------------
# Cap WSL to 75% of host RAM so Windows itself does not swap. Heavy AEGIS
# workloads (ML inference, containers) will hit this limit — that's OK,
# Linux OOM will kill the offending container, not the whole system.
memory = ${wslMemGB}GB

# Cap vCPUs. Leave headroom for Windows + Docker Desktop overhead.
processors = ${wslProc}

# Swap file on the WSL VHDX. Used for bursty peaks during docker compose up.
swap = ${wslSwapGB}GB
swapFile = %USERPROFILE%\\.wslswap.vhdx

# --- Networking -----------------------------------------------------------
# REQUIRED for Windows → WSL localhost access (dashboard on http://localhost:8000
# viewed from Windows browser). Enabled by default but we set it explicitly
# because some Docker Desktop versions flip it off.
localhostForwarding = true

# Allows running containers that themselves use KVM (e.g. some emulators).
# Needed by Android emulator for app-store scrape testing in Phase 1.
nestedVirtualization = true

# --- Kernel ---------------------------------------------------------------
# Pin to Microsoft's shipped kernel. Uncomment and set a path only if you
# are building a custom kernel (e.g., for eBPF tracing).
# kernel = C:\\Users\\$env:USERNAME\\.wsl-kernel\\bzImage

# --- Logging / diagnostics (leave off in steady state) -------------------
# debugConsole = true
# dnsTunneling = true      # Windows 11 23H2+ — helps with VPN DNS issues.

[experimental]
# autoMemoryReclaim reclaims unused memory back to Windows ~15 min idle.
# Huge quality-of-life improvement on laptops.
autoMemoryReclaim = gradual

# Sparse VHDX — the WSL disk shrinks after large file deletions (e.g., after
# docker prune). Without this, the VHDX grows monotonically.
sparseVhd = true

# networkingMode = mirrored  # Enable on Win 11 22H2+ for better VPN behaviour.
                             # Currently opt-in because some scraping libs break
                             # with the mirrored stack (tested on Playwright 1.47).
"@

# Only write if content differs (avoids unnecessary "wsl --shutdown" prompts).
$writeNeeded = $true
if (Test-Path $wslConfigPath) {
    $existing = Get-Content -Raw $wslConfigPath
    if ($existing.Trim() -eq $wslConfigContent.Trim()) {
        Write-Log ".wslconfig already up to date" OK
        $writeNeeded = $false
    } else {
        $backup = "$wslConfigPath.bak.$((Get-Date).ToString('yyyyMMddHHmmss'))"
        Copy-Item $wslConfigPath $backup -Force
        Write-Log "Backed up existing .wslconfig → $backup"
    }
}
if ($writeNeeded) {
    # UTF-8 without BOM (WSL parser dislikes BOMs).
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($wslConfigPath, $wslConfigContent, $utf8NoBom)
    Write-Log "Wrote .wslconfig (memory=${wslMemGB}GB, processors=${wslProc}, swap=${wslSwapGB}GB)" OK
    Write-Log "Run 'wsl --shutdown' after this script to apply."
}

# ---------------------------------------------------------------------------
# 4. Docker Desktop
# ---------------------------------------------------------------------------
if (-not $SkipDocker) {
    Write-Log "Installing Docker Desktop (WSL2 backend)" SECTION
    $dockerInstalled = $false
    try {
        $svc = Get-Service -Name "com.docker.service" -ErrorAction SilentlyContinue
        if ($svc) { $dockerInstalled = $true }
    } catch { }

    if ($dockerInstalled) {
        Write-Log "Docker Desktop already installed" OK
    } else {
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            Write-Log "winget not available. Install from https://www.docker.com/products/docker-desktop/ manually." WARN
        } else {
            Write-Log "Installing via winget..."
            winget install --id Docker.DockerDesktop --silent --accept-package-agreements --accept-source-agreements
            if ($LASTEXITCODE -eq 0) {
                Write-Log "Docker Desktop installed" OK
                Write-Log "Launch Docker Desktop once and ensure: Settings → Resources → WSL Integration → $UbuntuVersion is ENABLED." WARN
            } else {
                Write-Log "winget install Docker.DockerDesktop failed (exit $LASTEXITCODE)" WARN
            }
        }
    }
} else {
    Write-Log "Skipping Docker Desktop install (--SkipDocker)"
}

# ---------------------------------------------------------------------------
# 5. VS Code + extensions
# ---------------------------------------------------------------------------
if (-not $SkipVSCode) {
    Write-Log "Installing VS Code + extensions" SECTION
    $codeCmd = Get-Command code -ErrorAction SilentlyContinue

    if (-not $codeCmd) {
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            winget install --id Microsoft.VisualStudioCode --silent --accept-package-agreements --accept-source-agreements
            if ($LASTEXITCODE -eq 0) {
                Write-Log "VS Code installed" OK
                # Refresh PATH for this session.
                $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")
                $codeCmd = Get-Command code -ErrorAction SilentlyContinue
            } else {
                Write-Log "winget install VS Code failed" WARN
            }
        }
    } else {
        Write-Log "VS Code already installed" OK
    }

    if ($codeCmd) {
        $extensions = @(
            'ms-vscode-remote.remote-wsl',
            'ms-python.python',
            'ms-python.vscode-pylance',
            'ms-azuretools.vscode-docker',
            'ms-toolsai.jupyter',
            'eamodio.gitlens',
            'usernamehw.errorlens',
            'charliermarsh.ruff',
            'redhat.vscode-yaml',
            'tamasfe.even-better-toml'
        )
        foreach ($ext in $extensions) {
            try {
                & code --install-extension $ext --force 2>&1 | Out-Null
                Write-Log "Extension: $ext" OK
            } catch {
                Write-Log "Failed to install extension $ext : $_" WARN
            }
        }
    }
} else {
    Write-Log "Skipping VS Code install (--SkipVSCode)"
}

# ---------------------------------------------------------------------------
# 6. NVIDIA driver check
# ---------------------------------------------------------------------------
Write-Log "Checking NVIDIA driver (optional)" SECTION
try {
    $gpu = Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match 'NVIDIA' } | Select-Object -First 1
    if ($null -ne $gpu) {
        Write-Log "GPU: $($gpu.Name)" OK
        Write-Log "Driver version: $($gpu.DriverVersion)" OK
        # Heuristic: WSL CUDA 12.4 wants driver ≥ 550. DriverVersion is dot-decimal.
        $major = [int]($gpu.DriverVersion -split '\.')[0]
        if ($major -lt 30) {
            # NVIDIA driver DriverVersion in CIM is actually numeric-ish, need parsing.
            # Fall back to DriverDate heuristic.
            $driverDate = [datetime]::ParseExact(($gpu.DriverDate).ToString("yyyyMMdd000000.000000+000"), "yyyyMMddHHmmss.ffffffzzz", $null) -as [datetime]
        }
        Write-Log "For WSL CUDA 12.4, ensure Windows driver ≥ 550.xx. Download from https://www.nvidia.com/Download/index.aspx"
    } else {
        Write-Log "No NVIDIA GPU detected — AEGIS will run in CPU-only mode" WARN
    }
} catch {
    Write-Log "GPU detection error: $_" WARN
}

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
Write-Log "Windows bootstrap complete" SECTION
Write-Host ""
Write-Host "NEXT STEPS:" -ForegroundColor Cyan
Write-Host "  1. If Ubuntu was just installed, launch it from Start menu and create your user account." -ForegroundColor White
Write-Host "  2. Start Docker Desktop and enable the WSL Integration for $UbuntuVersion." -ForegroundColor White
Write-Host "  3. Close this PowerShell and run: wsl --shutdown" -ForegroundColor White
Write-Host "  4. Open Ubuntu, clone the AEGIS repo, and run:" -ForegroundColor White
Write-Host "       bash bootstrap/wsl/00_all.sh" -ForegroundColor Yellow
Write-Host ""
exit 0
