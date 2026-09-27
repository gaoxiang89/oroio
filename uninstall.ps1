<#
.SYNOPSIS
    Uninstall dk (droid key manager) for Windows
.DESCRIPTION
    Removes dk.ps1 from %LOCALAPPDATA%\oroio\bin
    Optionally removes data directory and PATH entry
.EXAMPLE
    irm https://raw.githubusercontent.com/gaoxiang89/oroio/main/uninstall.ps1 | iex
#>

param(
    [switch]$RemoveData,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$INSTALL_DIR = Join-Path $env:LOCALAPPDATA "oroio"
$OROIO_DIR = Join-Path $env:USERPROFILE ".oroio"
$BIN_DIR = Join-Path $INSTALL_DIR "bin"

function Write-Info {
    param([string]$Message)
    Write-Host $Message -ForegroundColor Cyan
}

function Write-Success {
    param([string]$Message)
    Write-Host $Message -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host $Message -ForegroundColor Yellow
}

# Confirm
if (-not $Force) {
    $confirm = Read-Host "Uninstall dk? (y/N)"
    if ($confirm -ne "y" -and $confirm -ne "Y") {
        Write-Host "Cancelled."
        exit 0
    }
}

# Remove dk.ps1
if (Test-Path $BIN_DIR) {
    Write-Info "Removing dk.ps1..."
    Remove-Item -Path $BIN_DIR -Recurse -Force
}

# Remove empty install dir
if ((Test-Path $INSTALL_DIR) -and (Get-ChildItem $INSTALL_DIR -ErrorAction SilentlyContinue).Count -eq 0) {
    Remove-Item -Path $INSTALL_DIR -Force
}

# Remove from PATH
$userPath = [Environment]::GetEnvironmentVariable("PATH", "User")
if ($userPath -like "*$BIN_DIR*") {
    Write-Info "Removing from PATH..."
    $newPath = ($userPath -split ";" | Where-Object { $_ -ne $BIN_DIR }) -join ";"
    [Environment]::SetEnvironmentVariable("PATH", $newPath, "User")
}

# Remove profile entries
$profilePath = $PROFILE.CurrentUserAllHosts
if (Test-Path $profilePath) {
    $content = Get-Content $profilePath -Raw
    if ($content -like "*# dk (droid key manager)*") {
        Write-Info "Removing PowerShell profile entries..."
        # 容忍在文件开头/结尾且无换行的情况
        $newContent = $content -replace "(?s)# dk \(droid key manager\).*?# end dk\r?\n?", ""
        Set-Content -Path $profilePath -Value $newContent -NoNewline
    }
}

# Remove data directory
if ($RemoveData) {
    if (Test-Path $OROIO_DIR) {
        Write-Warn "Removing data directory ($OROIO_DIR)..."
        Remove-Item -Path $OROIO_DIR -Recurse -Force
    }
}
else {
    if (Test-Path $OROIO_DIR) {
        # 与 macOS 卸载行为对齐：默认保留 keys.enc，清理其他缓存/配置文件
        Get-ChildItem -Path $OROIO_DIR -Recurse -File | Where-Object { $_.Name -ne "keys.enc" } | ForEach-Object {
            Remove-Item -Path $_.FullName -Force -ErrorAction SilentlyContinue
        }
        # 删除可能空的子目录
        Get-ChildItem -Path $OROIO_DIR -Recurse -Directory | Where-Object { ($_ | Get-ChildItem -Force | Measure-Object).Count -eq 0 } | ForEach-Object {
            Remove-Item -Path $_.FullName -Force -ErrorAction SilentlyContinue
        }
        Write-Warn "Data directory preserved (keys.enc 保留): $OROIO_DIR"
        Write-Host "  Use -RemoveData to delete it."
    }
}

Write-Host ""
Write-Success "Uninstall complete!"
Write-Host ""
Write-Host "Restart your terminal for changes to take effect."
Write-Host ""
