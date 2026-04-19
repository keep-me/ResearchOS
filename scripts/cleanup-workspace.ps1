[CmdletBinding()]
param(
  [switch]$Execute,
  [switch]$PruneGit,
  [switch]$RemoveBackups
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Split-Path -Parent $PSScriptRoot)).Path
Set-Location $repoRoot

function Resolve-WorkspacePath {
  param(
    [Parameter(Mandatory = $true)]
    [string]$RelativePath
  )

  $combined = Join-Path $repoRoot $RelativePath
  $fullPath = [System.IO.Path]::GetFullPath($combined)
  $normalizedRoot = [System.IO.Path]::GetFullPath($repoRoot).TrimEnd("\", "/") + [System.IO.Path]::DirectorySeparatorChar
  $normalizedPath = $fullPath.TrimEnd("\", "/")
  if (-not $normalizedPath.StartsWith($normalizedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to operate outside workspace: $RelativePath -> $fullPath"
  }
  return $fullPath
}

function Get-PathSizeBytes {
  param(
    [Parameter(Mandatory = $true)]
    [string]$LiteralPath
  )

  if (-not (Test-Path -LiteralPath $LiteralPath)) {
    return [int64]0
  }

  $item = Get-Item -LiteralPath $LiteralPath -Force
  if (-not $item.PSIsContainer) {
    return [int64]$item.Length
  }

  $sum = (Get-ChildItem -LiteralPath $LiteralPath -Force -Recurse -ErrorAction SilentlyContinue |
      Measure-Object -Property Length -Sum).Sum
  if ($null -eq $sum) {
    return [int64]0
  }
  return [int64]$sum
}

function Format-Bytes {
  param(
    [Parameter(Mandatory = $true)]
    [Int64]$Bytes
  )

  if ($Bytes -ge 1GB) {
    return "{0:N2} GB" -f ($Bytes / 1GB)
  }
  if ($Bytes -ge 1MB) {
    return "{0:N2} MB" -f ($Bytes / 1MB)
  }
  if ($Bytes -ge 1KB) {
    return "{0:N2} KB" -f ($Bytes / 1KB)
  }
  return "$Bytes B"
}

function Remove-WorkspacePath {
  param(
    [Parameter(Mandatory = $true)]
    [string]$RelativePath
  )

  $absolutePath = Resolve-WorkspacePath -RelativePath $RelativePath
  if (-not (Test-Path -LiteralPath $absolutePath)) {
    return [PSCustomObject]@{
      Status = "missing"
      Error  = $null
    }
  }

  $item = Get-Item -LiteralPath $absolutePath -Force
  try {
    Remove-Item -LiteralPath $absolutePath -Recurse -Force -ErrorAction Stop
    return [PSCustomObject]@{
      Status = "removed"
      Error  = $null
    }
  }
  catch {
    if (-not $item.PSIsContainer) {
      return [PSCustomObject]@{
        Status = "partial"
        Error  = $_.Exception.Message
      }
    }

    Get-ChildItem -LiteralPath $absolutePath -Force -ErrorAction SilentlyContinue |
      Sort-Object -Property PSIsContainer |
      ForEach-Object {
        try {
          Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction Stop
        }
        catch {
          Write-Warning ("Skipping locked or inaccessible path: {0} ({1})" -f $_.FullName, $_.Exception.Message)
        }
      }

    try {
      Remove-Item -LiteralPath $absolutePath -Recurse -Force -ErrorAction Stop
      return [PSCustomObject]@{
        Status = "removed"
        Error  = $null
      }
    }
    catch {
      return [PSCustomObject]@{
        Status = "partial"
        Error  = $_.Exception.Message
      }
    }
  }
}

function Get-SnapshotWorktree {
  param(
    [Parameter(Mandatory = $true)]
    [string]$ConfigPath
  )

  $line = Select-String -LiteralPath $ConfigPath -Pattern '^\s*worktree\s*=\s*(.+)$' | Select-Object -First 1
  if (-not $line) {
    return $null
  }
  return $line.Matches[0].Groups[1].Value.Trim()
}

function Test-PathUnderRoot {
  param(
    [Parameter(Mandatory = $true)]
    [string]$CandidatePath,
    [Parameter(Mandatory = $true)]
    [string]$RootPath
  )

  try {
    $candidateFull = [System.IO.Path]::GetFullPath($CandidatePath).TrimEnd("\", "/")
    $rootFull = [System.IO.Path]::GetFullPath($RootPath).TrimEnd("\", "/") + [System.IO.Path]::DirectorySeparatorChar
    return $candidateFull.StartsWith($rootFull, [System.StringComparison]::OrdinalIgnoreCase)
  }
  catch {
    return $false
  }
}

function Get-StaleSnapshotRepos {
  $snapshotRoot = Resolve-WorkspacePath -RelativePath "data\snapshots"
  if (-not (Test-Path -LiteralPath $snapshotRoot)) {
    return @()
  }

  $generatedRoots = @(
    (Resolve-WorkspacePath -RelativePath "tmp"),
    (Resolve-WorkspacePath -RelativePath "build"),
    (Resolve-WorkspacePath -RelativePath "src-tauri\target")
  )

  $candidates = New-Object System.Collections.Generic.List[object]
  foreach ($repo in Get-ChildItem -LiteralPath $snapshotRoot -Directory -Force) {
    $configPath = Join-Path $repo.FullName "config"
    if (-not (Test-Path -LiteralPath $configPath)) {
      continue
    }

    $worktree = Get-SnapshotWorktree -ConfigPath $configPath
    if ([string]::IsNullOrWhiteSpace($worktree)) {
      continue
    }

    $isMissing = -not (Test-Path -LiteralPath $worktree)
    $isTempWorkspace = $worktree -match 'pytest-of-|AppData/Local/Temp|/Temp/'
    $isGeneratedWorkspace = $false
    foreach ($generatedRoot in $generatedRoots) {
      if (Test-PathUnderRoot -CandidatePath $worktree -RootPath $generatedRoot) {
        $isGeneratedWorkspace = $true
        break
      }
    }
    $isWorkspaceScratch = $false
    if (Test-PathUnderRoot -CandidatePath $worktree -RootPath $repoRoot) {
      try {
        $leafName = Split-Path -Leaf ([System.IO.Path]::GetFullPath($worktree))
        $isWorkspaceScratch = $leafName.StartsWith(".tmp_", [System.StringComparison]::OrdinalIgnoreCase)
      }
      catch {
        $isWorkspaceScratch = $false
      }
    }

    if ($isTempWorkspace -or $isGeneratedWorkspace -or $isWorkspaceScratch) {
      $reason = if ($isGeneratedWorkspace) {
        "generated-workspace snapshot"
      }
      elseif ($isWorkspaceScratch) {
        "workspace scratch snapshot"
      }
      elseif ($isMissing) {
        "missing temp pytest snapshot"
      }
      else {
        "temp workspace snapshot"
      }

      $candidates.Add([PSCustomObject]@{
          RepoPath  = $repo.FullName
          Worktree  = $worktree
          Reason    = $reason
          SizeBytes = Get-PathSizeBytes -LiteralPath $repo.FullName
        })
    }
  }

  return $candidates
}

function Get-DirectoryAuditRows {
  $rows = New-Object System.Collections.Generic.List[object]
  $targets = @(
    @{ Path = ".pytest_cache"; Reason = "pytest cache" },
    @{ Path = ".ruff_cache"; Reason = "ruff cache" },
    @{ Path = ".mypy_cache"; Reason = "mypy cache" },
    @{ Path = ".sandbox-home"; Reason = "codex sandbox home" },
    @{ Path = ".sandbox-tmp"; Reason = "codex sandbox temp" },
    @{ Path = "tmp"; Reason = "runtime temp workspaces and logs" },
    @{ Path = "build\researchos-server"; Reason = "PyInstaller intermediate build output" },
    @{ Path = "src-tauri\target"; Reason = "Tauri build output" },
    @{ Path = "src-tauri\binaries"; Reason = "staged Tauri sidecar binaries" },
    @{ Path = "frontend\dist"; Reason = "frontend production build" }
  )

  if ($RemoveBackups) {
    $targets += @{ Path = "backups"; Reason = "user-requested backup cleanup" }
  }

  foreach ($target in $targets) {
    $absolutePath = Resolve-WorkspacePath -RelativePath $target.Path
    if (-not (Test-Path -LiteralPath $absolutePath)) {
      continue
    }
    $rows.Add([PSCustomObject]@{
        Type      = "Path"
        Path      = $target.Path
        Reason    = $target.Reason
        SizeBytes = Get-PathSizeBytes -LiteralPath $absolutePath
      })
  }

  $tmpDockerIp = Resolve-WorkspacePath -RelativePath "tmp_docker_ip.txt"
  if (Test-Path -LiteralPath $tmpDockerIp) {
    $rows.Add([PSCustomObject]@{
        Type      = "File"
        Path      = "tmp_docker_ip.txt"
        Reason    = "empty temp marker file"
        SizeBytes = Get-PathSizeBytes -LiteralPath $tmpDockerIp
      })
  }

  return $rows
}

$directoryRows = Get-DirectoryAuditRows
$snapshotRows = @(Get-StaleSnapshotRepos)
$snapshotBytes = (($snapshotRows | Measure-Object -Property SizeBytes -Sum).Sum)
if ($null -eq $snapshotBytes) {
  $snapshotBytes = [int64]0
}
$directoryBytes = (($directoryRows | Measure-Object -Property SizeBytes -Sum).Sum)
if ($null -eq $directoryBytes) {
  $directoryBytes = [int64]0
}

Write-Host ""
Write-Host "Workspace cleanup audit"
Write-Host "Root: $repoRoot"
Write-Host ""

if ($directoryRows.Count -gt 0) {
  $directoryRows |
    Select-Object Type, Path, Reason, @{ Name = "Size"; Expression = { Format-Bytes -Bytes $_.SizeBytes } } |
    Format-Table -AutoSize
}
else {
  Write-Host "No rebuildable path targets found."
}

Write-Host ""
Write-Host ("Disposable snapshot repos: {0} ({1})" -f $snapshotRows.Count, (Format-Bytes -Bytes $snapshotBytes))
if ($snapshotRows.Count -gt 0) {
  $snapshotRows |
    Select-Object -First 8 Worktree, Reason, @{ Name = "Size"; Expression = { Format-Bytes -Bytes $_.SizeBytes } } |
    Format-Table -AutoSize
}

$estimatedBytes = [int64]$directoryBytes + [int64]$snapshotBytes

Write-Host ""
Write-Host ("Estimated reclaimable space: {0}" -f (Format-Bytes -Bytes $estimatedBytes))

if (-not $Execute) {
  Write-Host ""
  Write-Host "Audit only. Re-run with -Execute to apply cleanup."
  Write-Host "Optional flags: -PruneGit to run git gc, -RemoveBackups to delete backups/."
  exit 0
}

Write-Host ""
Write-Host "Applying cleanup..."

foreach ($row in $directoryRows) {
  $result = Remove-WorkspacePath -RelativePath $row.Path
  if ($result.Status -eq "removed") {
    Write-Host ("Removed {0} ({1})" -f $row.Path, (Format-Bytes -Bytes $row.SizeBytes))
  }
  elseif ($result.Status -eq "partial") {
    Write-Warning ("Partially cleaned {0}: {1}" -f $row.Path, $result.Error)
  }
}

$snapshotDeleted = 0
foreach ($row in $snapshotRows) {
  if (Test-Path -LiteralPath $row.RepoPath) {
    Remove-Item -LiteralPath $row.RepoPath -Recurse -Force
    $snapshotDeleted += 1
  }
}
if ($snapshotDeleted -gt 0) {
  Write-Host ("Removed {0} disposable snapshot repos ({1})" -f $snapshotDeleted, (Format-Bytes -Bytes $snapshotBytes))
}

if ($PruneGit) {
  Write-Host ""
  Write-Host "Running git gc --prune=now ..."
  git gc --prune=now
}

Write-Host ""
Write-Host "Cleanup complete."
