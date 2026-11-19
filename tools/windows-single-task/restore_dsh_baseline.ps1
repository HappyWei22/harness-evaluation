param(
    [Parameter(Mandatory = $true)][string]$VmId,
    [Parameter(Mandatory = $true)][string]$CheckpointId,
    [Parameter(Mandatory = $true)][string]$CheckpointName,
    [Parameter(Mandatory = $true)][string]$ReportPath,
    [Parameter(Mandatory = $true)][string]$BootScriptPath,
    [string]$CleanupContextJson,
    [string]$ExpectedProfile = 'C:\Users\alex_',
    [int]$HeartbeatTimeoutSeconds = 90,
    [int]$GuestTimeoutSeconds = 120
)

# Restore one registered checkpoint, start the guest, and verify the result.
# The caller must leave the virtual machine in the Off state and hold the host startup lock.
# Residue checks run inside the guest only when a cleanup context is supplied.

$ErrorActionPreference = 'Stop'
$vmGuid = [guid]$VmId
$checkpointGuid = [guid]$CheckpointId
$reportDirectory = Split-Path -Parent $ReportPath
New-Item -ItemType Directory -Path $reportDirectory -Force | Out-Null
$startedAt = (Get-Date).ToString('o')

$report = [ordered]@{
    RunId             = $null
    Activity          = 'interface_preflight'
    CaseVersion       = "restore $CheckpointName"
    ProductVersion    = 'not queried'
    Model             = 'not applicable'
    Skills            = 'not applicable'
    Tools             = 'Hyper-V PowerShell and PowerShell Direct'
    Permissions       = 'elevated host with saved VM credential'
    StartedAt         = $startedAt
    FinishedAt        = $null
    EvidenceLevel     = 'E2'
    Verdict           = 'incomplete'
    Stage             = 'check_baseline'
    Error             = $null
    VmId              = $null
    VmName            = $null
    InitialVmState    = $null
    CheckpointName    = $CheckpointName
    CheckpointId      = $checkpointGuid.ToString()
    RestoreAppliedAt  = $null
    StartReturnedAt   = $null
    Heartbeat         = $null
    Guest             = $null
}
$cleanup = $null
if ($CleanupContextJson) {
    $cleanup = $CleanupContextJson | ConvertFrom-Json
    $report.RunId = $cleanup.AttemptId
    $report.Activity = 'product_run'
    $report.CaseVersion = "restore $CheckpointName after $($cleanup.AttemptId)"
}

function Save-RestoreReport {
    $temporary = "$ReportPath.tmp"
    [IO.File]::WriteAllText($temporary, ($report | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
    if (Test-Path -LiteralPath $ReportPath) { [IO.File]::Delete($ReportPath) }
    [IO.File]::Move($temporary, $ReportPath)
}

try {
    $vm = Get-VM -Id $vmGuid
    $report.VmId = $vm.Id.ToString()
    $report.VmName = $vm.Name
    $report.InitialVmState = [string]$vm.State
    if ($vm.Id -ne $vmGuid) { throw 'The virtual machine identity changed.' }
    if ($vm.State -ne 'Off') {
        throw "Shut down $($vm.Name) normally before restoring the checkpoint. Current state: $($vm.State)"
    }
    $selected = @(Get-VMSnapshot -VMName $vm.Name | Where-Object Id -eq $checkpointGuid)
    if ($selected.Count -ne 1 -or $selected[0].Name -ne $CheckpointName) {
        throw 'The registered baseline checkpoint was not found.'
    }

    $report.Stage = 'get_guest_credential'
    $guestCredential = Get-HarnessGuestCredential -RequireSaved

    $report.Stage = 'restore_checkpoint'
    Save-RestoreReport
    Restore-VMSnapshot -VMSnapshot $selected[0] -Confirm:$false
    $report.RestoreAppliedAt = (Get-Date).ToString('o')

    $report.Stage = 'start_guest'
    Save-RestoreReport
    Start-VM -VM (Get-VM -Id $vmGuid) | Out-Null
    $report.StartReturnedAt = (Get-Date).ToString('o')

    $report.Stage = 'wait_heartbeat'
    Save-RestoreReport
    $heartbeatWait = [System.Diagnostics.Stopwatch]::StartNew()
    do {
        $current = Get-VM -Id $vmGuid
        $report.Heartbeat = [string]$current.Heartbeat
        if ($report.Heartbeat -like 'OkApplications*') { break }
        Start-Sleep -Seconds 2
    } while ($heartbeatWait.Elapsed.TotalSeconds -lt $HeartbeatTimeoutSeconds)
    if ($report.Heartbeat -notlike 'OkApplications*') {
        throw "The guest heartbeat did not become healthy within $HeartbeatTimeoutSeconds seconds: $($report.Heartbeat)"
    }

    $report.Stage = 'connect_guest'
    Save-RestoreReport
    $session = $null
    $connectWait = [System.Diagnostics.Stopwatch]::StartNew()
    while ($null -eq $session) {
        try {
            $session = New-PSSession -VMId $vmGuid -Credential $guestCredential -ErrorAction Stop
        } catch {
            if ($connectWait.Elapsed.TotalSeconds -ge $GuestTimeoutSeconds) {
                throw 'PowerShell Direct could not connect within the allowed time.'
            }
            Start-Sleep -Seconds 2
        }
    }

    try {
        $report.Stage = 'verify_guest'
        Save-RestoreReport
        $bootSource = [IO.File]::ReadAllText($BootScriptPath)
        $facts = Invoke-Command -Session $session -ArgumentList $bootSource, $ExpectedProfile, ([bool]$cleanup), $CleanupContextJson -ScriptBlock {
            param([string]$bootScript, [string]$expectedProfile, [bool]$hasCleanup, [string]$cleanupJson)
            $ErrorActionPreference = 'Stop'
            $event = & ([scriptblock]::Create($bootScript))
            $os = Get-CimInstance Win32_OperatingSystem
            $winlogon = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
            $result = [ordered]@{
                ProfileMatches    = ($env:USERPROFILE -eq $expectedProfile)
                Profile           = $env:USERPROFILE
                Caption           = $os.Caption
                BuildNumber       = $os.BuildNumber
                BootTime          = $os.LastBootUpTime.ToUniversalTime().ToString('o')
                BootEvent         = [pscustomobject]@{ RecordId = $event.RecordId; CreatedUtc = $event.CreatedUtc }
                AutoAdminLogon    = [string]$winlogon.AutoAdminLogon
                DefaultUserName   = [string]$winlogon.DefaultUserName
                ExplorerSessions  = @(Get-Process explorer -ErrorAction SilentlyContinue | Select-Object -ExpandProperty SessionId)
                LogonUiSessions   = @(Get-Process LogonUI -ErrorAction SilentlyContinue | Select-Object -ExpandProperty SessionId)
                DshProcesses      = @(Get-Process -Name 'DeepSeek Harness' -ErrorAction SilentlyContinue | Select-Object Id, SessionId)
                HarnessTasks      = @(Get-ScheduledTask -TaskName 'Harness-*' -ErrorAction SilentlyContinue | Select-Object TaskName, State)
            }
            $result.RunResidue = $null
            if ($hasCleanup) {
                $context = $cleanupJson | ConvertFrom-Json
                $dshHome = if ($env:DSH_HOME) { $env:DSH_HOME } else { Join-Path $env:USERPROFILE '.dsh' }
                $projectKey = '--' + ($context.Workspace -replace '[:\\/]+', '-') + '--'
                $launcher = Join-Path (Join-Path $env:USERPROFILE 'Downloads') ('DSH-OneShot-' + $context.AttemptId)
                $result.RunResidue = [pscustomobject]@{
                    RunWorkspaceExists = Test-Path -LiteralPath $context.Workspace
                    RunLauncherExists  = Test-Path -LiteralPath $launcher
                    RunSessionExists   = if ($context.SessionId) {
                        Test-Path -LiteralPath (Join-Path (Join-Path (Join-Path $dshHome 'sessions') $projectKey) $context.SessionId)
                    } else { $null }
                    PreflightTaskRemains = @(Get-ScheduledTask -TaskName 'Harness-Preflight-*' -ErrorAction SilentlyContinue).Count
                }
            }
            [pscustomobject]$result
        }
        $report.Guest = $facts
    } finally {
        Remove-PSSession -Session $session -ErrorAction SilentlyContinue
    }

    if (-not $report.Guest.ProfileMatches) {
        throw 'The guest query used a different Windows profile than the registered test account.'
    }
    if (@($report.Guest.DshProcesses).Count -ne 0) {
        throw 'A DeepSeek Harness process is still running after the restore.'
    }
    if ($cleanup) {
        $residue = $report.Guest.RunResidue
        # v2 rolls the event log back too, so a fresh start can reuse its record number.
        $sameOrOlder = if ($cleanup.OriginalBootCreatedUtc) {
            [DateTimeOffset]$report.Guest.BootEvent.CreatedUtc -le [DateTimeOffset]$cleanup.OriginalBootCreatedUtc
        } else {
            $report.Guest.BootEvent.RecordId -eq $cleanup.OriginalBootRecordId
        }
        if ($sameOrOlder) {
            throw 'The restored guest reports the startup event that preceded the restore.'
        }
        if ($residue.RunWorkspaceExists -or $residue.RunLauncherExists -or
            ($null -ne $residue.RunSessionExists -and $residue.RunSessionExists) -or $residue.PreflightTaskRemains -ne 0) {
            throw 'The restored guest still holds residue from the finished run.'
        }
    }

    $report.Stage = 'complete'
    $report.Verdict = 'pass'
} catch {
    $report.Verdict = 'fail'
    $report.Error = $_.Exception.Message
} finally {
    $report.FinishedAt = (Get-Date).ToString('o')
    Save-RestoreReport
}

[pscustomobject]$report
