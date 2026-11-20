param(
    [Parameter(Mandatory = $true)][string]$WorkDirectory,
    [Parameter(Mandatory = $true)][string]$PackageDirectory
)

# Zero-submission desktop preflight.
# Restores the v2 checkpoint, then records how far an unattended host can travel:
# boot, interactive session, DSH launch, workspace identification and composer focus.
# It never types into the composer and never invokes the send button.
# Cleanup, shutdown and the closing restore run even when a stage throws, so a failed
# pass cannot leave a task, a scratch directory or a DSH process behind in the guest.

$ErrorActionPreference = 'Stop'
$request = Get-Content -LiteralPath (Join-Path $WorkDirectory 'request.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$reportPath = Join-Path $WorkDirectory 'preflight-report.json'
$guestRoot = $request.WorkingRoot
$guestProbe = Join-Path $guestRoot 'probe'
$localProbe = Join-Path $WorkDirectory 'probe'
$keepRunning = [bool]$request.KeepRunning
New-Item -ItemType Directory -Path $localProbe -Force | Out-Null

$report = [ordered]@{
    RunId                 = $request.RunId
    Activity              = 'interface_preflight'
    CaseVersion           = 'desktop automation reachability v1'
    Mode                  = $request.Mode
    KeepRunning           = $keepRunning
    ProductVersion        = $request.ProductVersion
    Model                 = 'not applicable'
    Skills                = 'not applicable'
    Tools                 = 'Hyper-V PowerShell, PowerShell Direct, Task Scheduler, UI Automation'
    Permissions           = 'elevated host with saved VM credential'
    StartedAt             = (Get-Date).ToString('o')
    FinishedAt            = $null
    EvidenceLevel         = 'E2'
    Verdict               = 'incomplete'
    Stage                 = 'start'
    Error                 = $null
    HostAdminConfirmed    = $true
    VmId                  = $request.VmId
    CheckpointId          = $request.CheckpointId
    InitialVmState        = $null
    ShutdownRequired      = $null
    BootStage             = $null
    SessionStage          = $null
    StaleResidue          = $null
    LaunchStage           = $null
    ProbeVerdict          = $null
    WorkspaceMethod       = $null
    ComposerStage         = $null
    ProbeReportSha256     = $null
    ProbeDiagnostics      = $null
    Cleanup               = $null
    CleanupError          = $null
    FormalSubmissions     = 0
    DiagnosticSubmissions = 0
    ManualSteps           = @()
    LastSuccessfulStep    = $null
    BlockingStep          = $null
    FinalRestore          = $null
}

function Save-PreflightReport {
    $temporary = "$reportPath.tmp"
    [IO.File]::WriteAllText($temporary, ($report | ConvertTo-Json -Depth 10), [Text.UTF8Encoding]::new($false))
    if (Test-Path -LiteralPath $reportPath) { [IO.File]::Delete($reportPath) }
    [IO.File]::Move($temporary, $reportPath)
}

function Invoke-RestoreStage([string]$phase, $cleanupContext) {
    $restoreReport = Join-Path (Join-Path $WorkDirectory 'restore') "$phase.json"
    $source = [IO.File]::ReadAllText((Join-Path $PackageDirectory 'restore_dsh_baseline.ps1'))
    $arguments = @{
        VmId           = $request.VmId
        CheckpointId   = $request.CheckpointId
        CheckpointName = $request.CheckpointName
        ReportPath     = $restoreReport
        BootScriptPath = (Join-Path $PackageDirectory 'read_dsh_boot_event.ps1')
    }
    if ($cleanupContext) { $arguments.CleanupContextJson = ($cleanupContext | ConvertTo-Json -Depth 5 -Compress) }
    & ([scriptblock]::Create($source)) @arguments
}

function Connect-PreflightGuest {
    $credential = Get-HarnessGuestCredential -RequireSaved
    $wait = [System.Diagnostics.Stopwatch]::StartNew()
    while ($true) {
        try { return New-PSSession -VMId ([guid]$request.VmId) -Credential $credential -ErrorAction Stop }
        catch {
            if ($wait.Elapsed.TotalSeconds -ge 120) { throw 'PowerShell Direct could not connect to the restored guest.' }
            Start-Sleep -Seconds 2
        }
    }
}

function Get-InteractiveFacts($session) {
    Invoke-Command -Session $session -ScriptBlock {
        $ErrorActionPreference = 'Stop'
        $queryUser = try { (@(& "$env:SystemRoot\System32\query.exe" user 2>&1) -join ' | ') } catch { "unavailable: $($_.Exception.Message)" }
        [pscustomobject]@{
            UserProfile     = $env:USERPROFILE
            UserName        = $env:USERNAME
            ExplorerDesktop = @(Get-Process explorer -ErrorAction SilentlyContinue | Where-Object SessionId -ne 0 |
                Select-Object Id, SessionId)
            LogonUiSessions = @(Get-Process LogonUI -ErrorAction SilentlyContinue | Select-Object -ExpandProperty SessionId)
            DshCount        = @(Get-Process -Name 'DeepSeek Harness' -ErrorAction SilentlyContinue).Count
            QueryUser       = $queryUser
        }
    }
}

function Wait-DesktopSession($session, [int]$seconds) {
    $timer = [System.Diagnostics.Stopwatch]::StartNew()
    $facts = $null
    do {
        $facts = Get-InteractiveFacts $session
        if (@($facts.ExplorerDesktop).Count -ge 1) { return $facts }
        Start-Sleep -Seconds 3
    } while ($timer.Elapsed.TotalSeconds -lt $seconds)
    $facts
}

function Stop-GuestProcesses($session) {
    Invoke-Command -Session $session -ScriptBlock {
        # A process can exit between enumeration and the kill, and taskkill reports that
        # on stderr; keep it from becoming a terminating error in this block.
        $ErrorActionPreference = 'Continue'
        foreach ($process in @(Get-CimInstance Win32_Process -Filter "Name = 'DeepSeek Harness.exe'" -ErrorAction SilentlyContinue)) {
            try { & taskkill.exe /PID $process.ProcessId /T /F *> $null } catch { }
        }
        Start-Sleep -Milliseconds 800
        @(Get-Process -Name 'DeepSeek Harness' -ErrorAction SilentlyContinue).Count
    }
}

function Request-GuestShutdown($session) {
    Invoke-Command -Session $session -ScriptBlock {
        Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\shutdown.exe') `
            -ArgumentList @('/s', '/t', '0') -WindowStyle Hidden
    }
}

function Wait-VmOff([int]$seconds) {
    $wait = [System.Diagnostics.Stopwatch]::StartNew()
    while ((Get-VM -Id ([guid]$request.VmId)).State -ne 'Off') {
        if ($wait.Elapsed.TotalSeconds -ge $seconds) { throw "The guest did not shut down normally within $seconds seconds." }
        Start-Sleep -Seconds 2
    }
}

# A failed earlier pass can leave its task, its scratch tree and a DSH process behind.
# Only residue that carries this package's own marker file is removed.
function Clear-StaleResidue($session, [string]$keepRoot) {
    Invoke-Command -Session $session -ArgumentList $keepRoot -ScriptBlock {
        param($keep)
        $ErrorActionPreference = 'Stop'
        $removed = @()
        foreach ($task in @(Get-ScheduledTask -TaskName 'Harness-Preflight-*' -ErrorAction SilentlyContinue)) {
            try {
                Unregister-ScheduledTask -TaskName $task.TaskName -Confirm:$false -ErrorAction Stop
                $removed += "task:$($task.TaskName)"
            } catch {
                $removed += "task-failed:$($task.TaskName)"
            }
        }
        $temp = [IO.Path]::GetTempPath()
        foreach ($dir in @(Get-ChildItem -LiteralPath $temp -Directory -Filter 'PRE-DESKTOP-*' -ErrorAction SilentlyContinue)) {
            if ($dir.FullName -eq $keep) { continue }
            if (-not (Test-Path -LiteralPath (Join-Path $dir.FullName 'probe\probe-request.json'))) { continue }
            try {
                Remove-Item -LiteralPath $dir.FullName -Recurse -Force -ErrorAction Stop
                $removed += "dir:$($dir.Name)"
            } catch {
                $removed += "dir-failed:$($dir.Name)"
            }
        }
        @($removed)
    }
}

$guestSession = $null
$staged = $null
$first = $null
$taskName = $null
$probeEvidence = $null
$failure = $null

try {
    # ---------------------------------------------------------------- machine check
    $report.Stage = 'check_machine'
    Save-PreflightReport
    $vm = Get-VM -Id ([guid]$request.VmId)
    if ($vm.Name -ne $request.VmName) { throw 'The registered virtual machine name changed.' }
    $report.InitialVmState = [string]$vm.State
    $snapshots = @(Get-VMSnapshot -VMName $vm.Name)
    $selected = @($snapshots | Where-Object Id -eq ([guid]$request.CheckpointId))
    if ($selected.Count -ne 1 -or $selected[0].Name -ne $request.CheckpointName) {
        throw 'The registered checkpoint is missing or was replaced.'
    }

    # ------------------------------------------------------------- optional shutdown
    $report.Stage = 'normal_shutdown'
    Save-PreflightReport
    if ($request.Mode -eq 'post_login') {
        # The operator signed in to the guest desktop by hand; this pass continues from
        # that running session instead of restoring the checkpoint again.
        if ($vm.State -ne 'Running') {
            throw 'The post-login preflight needs the guest already running with the operator signed in.'
        }
        $report.ShutdownRequired = $false
        $report.ManualSteps += 'Sign in to the guest desktop: the operator signed in before this pass.'
        $guestSession = Connect-PreflightGuest
        $bootSource = [IO.File]::ReadAllText((Join-Path $PackageDirectory 'read_dsh_boot_event.ps1'))
        $boot = Invoke-Command -Session $guestSession -ScriptBlock ([scriptblock]::Create($bootSource))
        $report.BootStage = [pscustomobject]@{
            Verdict         = 'reused_running_vm'
            BootEvent       = $boot
            BootTime        = $null
            AutoAdminLogon  = $null
            DefaultUserName = $null
            Heartbeat       = [string]$vm.Heartbeat
        }
        $report.LastSuccessfulStep = 'reuse_running_vm'
        Save-PreflightReport
        $first = [pscustomobject]@{ Guest = [pscustomobject]@{ BootEvent = $boot } }
    } else {
        if ($vm.State -ne 'Off') {
            $report.ShutdownRequired = $true
            $guestSession = Connect-PreflightGuest
            $activity = Get-InteractiveFacts $guestSession
            if ($activity.DshCount -ne 0) {
                throw 'A DeepSeek Harness process is running, so the host will not shut the guest down automatically.'
            }
            Request-GuestShutdown $guestSession
            Remove-PSSession -Session $guestSession
            $guestSession = $null
            Wait-VmOff 120
        } else {
            $report.ShutdownRequired = $false
        }
        $report.LastSuccessfulStep = 'normal_shutdown'
        Save-PreflightReport

        # --------------------------------------------------- first baseline restore
        $baseline = ($request.CheckpointName -split '-')[-1]
        $report.Stage = "restore_$baseline"
        Save-PreflightReport
        $first = Invoke-RestoreStage 'first' $null
        if ($first.Verdict -ne 'pass') { throw "The first baseline restore failed: $($first.Error)" }
        $report.BootStage = [pscustomobject]@{
            Verdict         = 'pass'
            BootEvent       = $first.Guest.BootEvent
            BootTime        = $first.Guest.BootTime
            AutoAdminLogon  = $first.Guest.AutoAdminLogon
            DefaultUserName = $first.Guest.DefaultUserName
            Heartbeat       = $first.Heartbeat
        }
        $report.LastSuccessfulStep = "restore_${baseline}_boot"
        Save-PreflightReport
    }

    # --------------------------------------------------------- interactive session
    $report.Stage = 'wait_interactive_session'
    Save-PreflightReport
    $guestSession = Connect-PreflightGuest
    $desktop = Wait-DesktopSession $guestSession 120
    $report.SessionStage = [pscustomobject]@{
        ExplorerDesktopCount = @($desktop.ExplorerDesktop).Count
        ExplorerSessions     = @($desktop.ExplorerDesktop | ForEach-Object { $_.SessionId })
        LogonUiSessions      = @($desktop.LogonUiSessions)
        QueryUser            = $desktop.QueryUser
        AutoAdminLogon       = $first.Guest.AutoAdminLogon
    }
    if (@($desktop.ExplorerDesktop).Count -eq 0) {
        $report.Verdict = 'needs_login'
        $report.BlockingStep = 'interactive_session'
        $report.ManualSteps += 'Sign in to the guest desktop: no interactive Explorer session appeared within 120 seconds of the restore.'
        throw 'No interactive desktop session appeared after the restore.'
    }
    $report.LastSuccessfulStep = 'interactive_session'
    Save-PreflightReport

    # ---------------------------------------------------------- clear stale residue
    $report.Stage = 'clear_stale_residue'
    Save-PreflightReport
    $removed = Clear-StaleResidue $guestSession $guestRoot
    $report.StaleResidue = @($removed)
    Save-PreflightReport

    # --------------------------------------------------------------- stage the task
    $report.Stage = 'stage_desktop_task'
    Save-PreflightReport
    $null = Invoke-Command -Session $guestSession -ArgumentList $guestRoot, $guestProbe, $request.ExpectedProfile -ScriptBlock {
        param($root, $probe, $expectedProfile)
        $ErrorActionPreference = 'Stop'
        if ($env:USERPROFILE -ne $expectedProfile) { throw 'The guest profile differs from the registered test account.' }
        if (Test-Path -LiteralPath $root) { throw 'The preflight working root already exists.' }
        New-Item -ItemType Directory -Path $probe -Force | Out-Null
        foreach ($path in @($root, $probe)) {
            $item = Get-Item -LiteralPath $path -Force
            if (-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                throw 'The preflight working directory is not a plain directory.'
            }
        }
    }
    Copy-Item -LiteralPath (Join-Path $PackageDirectory 'guest_desktop_probe.ps1') -Destination $guestProbe -ToSession $guestSession -Force
    $staged = Invoke-Command -Session $guestSession -ArgumentList $request, $guestRoot, $guestProbe -ScriptBlock {
        param($record, $root, $probe)
        $ErrorActionPreference = 'Stop'
        $desktop = [Environment]::GetFolderPath('DesktopDirectory')
        $shortcut = Join-Path $desktop $record.ShortcutName
        if (-not (Test-Path -LiteralPath $shortcut -PathType Leaf)) { throw 'The DSH accessibility shortcut is missing from the desktop.' }
        $link = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcut)
        if (-not (Test-Path -LiteralPath $link.TargetPath -PathType Leaf)) { throw 'The DSH executable behind the shortcut is missing.' }
        $workspace = Join-Path $root ('WS-' + $record.RunId.Substring($record.RunId.Length - 8))
        New-Item -ItemType Directory -Path (Join-Path $workspace 'input') -Force | Out-Null
        New-Item -ItemType Directory -Path (Join-Path $workspace 'output') -Force | Out-Null
        $probeRequest = [ordered]@{
            RunId                 = $record.RunId
            Workspace             = $workspace
            WorkspaceLabel        = Split-Path -Leaf $workspace
            Shortcut              = $shortcut
            Executable            = $link.TargetPath
            AccessibilityArgument = $record.AccessibilityArgument
            ProductVersion        = (Get-Item -LiteralPath $link.TargetPath).VersionInfo.ProductVersion
        }
        [IO.File]::WriteAllText((Join-Path $probe 'probe-request.json'),
            ($probeRequest | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
        [pscustomobject]@{
            Workspace      = $workspace
            Shortcut       = $shortcut
            Executable     = $link.TargetPath
            Arguments      = $link.Arguments
            ShortcutSha256 = (Get-FileHash -LiteralPath $shortcut -Algorithm SHA256).Hash
            ProductVersion = $probeRequest.ProductVersion
        }
    }

    # Validate the probe script inside the guest before the interactive task runs it,
    # so a start-up fault is reported as its own message instead of a bare exit code.
    # The guest blocks .ps1 files by execution policy, so the probe is always invoked
    # as a script block built from the file's text.
    $selfTest = Invoke-Command -Session $guestSession -ArgumentList (Join-Path $guestProbe 'guest_desktop_probe.ps1'), (Join-Path $guestProbe 'probe-request.json'), $guestProbe -ScriptBlock {
        param($script, $requestFile, $probeHome)
        $policy = (@(Get-ExecutionPolicy -List | ForEach-Object { "$($_.Scope)=$($_.ExecutionPolicy)" }) -join '; ')
        try {
            $source = [IO.File]::ReadAllText($script)
            & ([scriptblock]::Create($source)) -RequestPath $requestFile -ProbeHome $probeHome -SelfTest
            [pscustomobject]@{ Ok = $true; Error = $null; Policy = $policy }
        } catch {
            [pscustomobject]@{ Ok = $false; Error = ($_ | Out-String); Policy = $policy }
        }
    }

    $taskName = "Harness-Preflight-$($request.RunId)"
    $launched = Invoke-Command -Session $guestSession -ArgumentList $taskName, $guestProbe -ScriptBlock {
        param($name, $probe)
        $ErrorActionPreference = 'Stop'
        Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false
        $script = Join-Path $probe 'guest_desktop_probe.ps1'
        $requestFile = Join-Path $probe 'probe-request.json'
        # Execution policy on this guest rejects .ps1 files, so the task loads the file
        # text and invokes it as a script block instead of using -File.
        $argument = "-NoLogo -NoProfile -ExecutionPolicy Bypass -MTA -Command `"& { `$source = [IO.File]::ReadAllText('$script'); & ([scriptblock]::Create(`$source)) -RequestPath '$requestFile' -ProbeHome '$probe' }`""
        $action = New-ScheduledTaskAction -Execute (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') `
            -Argument $argument -WorkingDirectory $probe
        $userId = '{0}\{1}' -f $env:COMPUTERNAME, $env:USERNAME
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew
        $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Highest
        try {
            Register-ScheduledTask -TaskName $name -Action $action -Principal $principal -Settings $settings -Force | Out-Null
        } catch {
            $principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
            Register-ScheduledTask -TaskName $name -Action $action -Principal $principal -Settings $settings -Force | Out-Null
        }
        Start-ScheduledTask -TaskName $name
        Start-Sleep -Seconds 3
        [pscustomobject]@{
            TaskName  = $name
            State     = [string](Get-ScheduledTask -TaskName $name).State
            Principal = $principal.UserId
            RunLevel  = [string]$principal.RunLevel
            Argument  = $argument
        }
    }
    $report.LaunchStage = [pscustomobject]@{
        TaskRegistered = $launched.TaskName
        StateAtStart   = $launched.State
        RunLevel       = $launched.RunLevel
        Argument       = $launched.Argument
        Executable     = $staged.Executable
        ShortcutSha256 = $staged.ShortcutSha256
        Arguments      = $staged.Arguments
        Workspace      = $staged.Workspace
        SelfTest       = $selfTest
    }
    $report.LastSuccessfulStep = 'stage_desktop_task'
    Save-PreflightReport

    # ------------------------------------------------------------- collect the probe
    $report.Stage = 'collect_probe_report'
    Save-PreflightReport
    $guestReportPath = Join-Path (Join-Path $guestProbe 'probe') 'report.json'
    $wait = [System.Diagnostics.Stopwatch]::StartNew()
    $ready = $false
    while ($wait.Elapsed.TotalSeconds -lt 300) {
        # The probe writes the report after every step, so the file appearing proves
        # nothing. FinishedAt is written once, in the probe's finally block.
        $ready = Invoke-Command -Session $guestSession -ArgumentList $guestReportPath -ScriptBlock {
            param($path)
            if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $false }
            try { $current = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json } catch { return $false }
            return [bool]$current.FinishedAt
        }
        if ($ready) { break }
        Start-Sleep -Seconds 2
    }
    if (-not $ready) {
        $state = Invoke-Command -Session $guestSession -ArgumentList $taskName, $guestProbe -ScriptBlock {
            param($name, $probe)
            $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
            $failureFile = Join-Path $probe 'probe-failure.txt'
            [pscustomobject]@{
                State       = [string]$task.State
                LastResult  = (Get-ScheduledTaskInfo -TaskName $name -ErrorAction SilentlyContinue).LastTaskResult
                FailureText = $(if (Test-Path -LiteralPath $failureFile) { Get-Content -LiteralPath $failureFile -Raw } else { $null })
            }
        }
        $report.LaunchStage | Add-Member -NotePropertyName TaskStateWithoutReport -NotePropertyValue $state -Force
        $diagnostics = [ordered]@{}
        foreach ($name in @('probe-boot.txt', 'probe-selftest.txt', 'probe-trap.txt', 'probe-failure.txt', 'probe-transcript.txt')) {
            $guestFile = Join-Path $guestProbe $name
            $exists = Invoke-Command -Session $guestSession -ArgumentList $guestFile -ScriptBlock {
                param($path) Test-Path -LiteralPath $path -PathType Leaf
            }
            if (-not $exists) { continue }
            $localFile = Join-Path $localProbe $name
            Copy-Item -LiteralPath $guestFile -Destination $localFile -FromSession $guestSession -Force
            $diagnostics[$name] = Get-Content -LiteralPath $localFile -Raw -Encoding UTF8
        }
        $report.ProbeDiagnostics = $diagnostics
        if (-not $report.BlockingStep) { $report.BlockingStep = 'scheduled_task_start' }
        throw 'The desktop probe produced no report within 300 seconds.'
    }
    $localReport = Join-Path $localProbe 'guest-report.json'
    Copy-Item -LiteralPath $guestReportPath -Destination $localReport -FromSession $guestSession -Force
    $probeEvidence = Get-Content -LiteralPath $localReport -Raw -Encoding UTF8 | ConvertFrom-Json
    $report.ProbeReportSha256 = (Get-FileHash -LiteralPath $localReport -Algorithm SHA256).Hash
    $report.ProbeVerdict = $probeEvidence.Verdict
    $report.WorkspaceMethod = $probeEvidence.Method
    $report.ComposerStage = $probeEvidence.Focus
    foreach ($name in @('after-working-directory', 'after-path-argument',
            'after-workspace-picker', 'after-add-workspace', 'after-add-workspace-click',
            'after-add-workspace-sidebar', 'after-path-typed', 'after-path-navigate', 'after-workspace-selection', 'after-focus')) {
        $guestPng = Join-Path (Join-Path $guestProbe 'probe') "$name.png"
        $exists = Invoke-Command -Session $guestSession -ArgumentList $guestPng -ScriptBlock {
            param($path) Test-Path -LiteralPath $path -PathType Leaf
        }
        if ($exists) {
            Copy-Item -LiteralPath $guestPng -Destination (Join-Path $localProbe "$name.png") -FromSession $guestSession -Force
        }
    }
    $report.LastSuccessfulStep = 'collect_probe_report'

    if ($probeEvidence.Verdict -eq 'identified') {
        $report.Verdict = 'pass'
        $report.Stage = 'complete'
    } else {
        $report.Verdict = [string]$probeEvidence.Verdict
        $report.BlockingStep = 'workspace_selection'
        $report.ManualSteps += 'Select the run workspace inside DSH by hand; no unattended method was confirmed.'
        $report.Stage = 'complete'
    }
    Save-PreflightReport
} catch {
    $failure = $_.Exception.Message
    $report.Error = $failure
    if (-not $report.BlockingStep -and $report.Verdict -eq 'incomplete') { $report.Verdict = 'blocked' }
}

# ---------------------------------------------------------------- residue cleanup
# Runs on the success and the failure path alike, so a throwing stage cannot leave a
# task, a scratch tree or a DSH process behind.
if ($guestSession) {
    $report.Stage = 'cleanup'
    Save-PreflightReport
    try {
        if ($taskName) {
            $null = Invoke-Command -Session $guestSession -ArgumentList $taskName -ScriptBlock {
                param($name)
                Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false
            }
        }
        $null = Stop-GuestProcesses $guestSession
        $verified = Invoke-Command -Session $guestSession -ArgumentList $guestRoot, $request.ExpectedProfile, $taskName -ScriptBlock {
            param($root, $expectedProfile, $name)
            $ErrorActionPreference = 'Stop'
            if ($env:USERPROFILE -ne $expectedProfile) { throw 'The cleanup ran under a different Windows profile.' }
            if (Test-Path -LiteralPath $root) {
                $item = Get-Item -LiteralPath $root -Force
                if (-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                    throw 'The preflight working root is not a plain directory.'
                }
                Remove-Item -LiteralPath $root -Recurse -Force
            }
            [pscustomobject]@{
                TaskRemains       = $(if ($name) { @(Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue).Count } else { 0 })
                WorkingRootExists = Test-Path -LiteralPath $root
                DshRemains        = @(Get-Process -Name 'DeepSeek Harness' -ErrorAction SilentlyContinue).Count
            }
        }
        $report.Cleanup = $verified
        if ($verified.TaskRemains -ne 0 -or $verified.WorkingRootExists -or $verified.DshRemains -ne 0) {
            throw 'The preflight residue is still present in the guest.'
        }
        if (-not $failure) { $report.LastSuccessfulStep = 'cleanup' }
    } catch {
        $report.CleanupError = $_.Exception.Message
        if (-not $failure) {
            $failure = $_.Exception.Message
            $report.Error = $failure
            $report.Verdict = 'blocked'
            $report.BlockingStep = 'cleanup'
        }
    }
    Save-PreflightReport
}

# ------------------------------------------------------- shutdown and re-restore
$vmOff = $false
if ($guestSession -and -not $keepRunning) {
    $report.Stage = 'shutdown_before_final_restore'
    Save-PreflightReport
    try {
        Request-GuestShutdown $guestSession
        Start-Sleep -Seconds 2
        Remove-PSSession -Session $guestSession -ErrorAction SilentlyContinue
        $guestSession = $null
        Wait-VmOff 120
        $vmOff = $true
    } catch {
        $report.FinalRestore = [pscustomobject]@{ Verdict = 'skipped'; Error = $_.Exception.Message }
        if (-not $failure) {
            $failure = $_.Exception.Message
            $report.Error = $failure
            $report.Verdict = 'blocked'
            $report.BlockingStep = 'guest_shutdown'
        }
    }
} elseif ($guestSession -and $keepRunning) {
    Remove-PSSession -Session $guestSession -ErrorAction SilentlyContinue
    $guestSession = $null
    $report.FinalRestore = [pscustomobject]@{ Verdict = 'skipped_by_request'; Error = $null }
}

if ($vmOff) {
    $report.Stage = 'final_restore'
    Save-PreflightReport
    try {
        $context = [ordered]@{
            AttemptId            = $request.RunId
            Workspace            = $(if ($staged) { $staged.Workspace } else { $null })
            SessionId            = $null
            OriginalBootRecordId = $(if ($first) { $first.Guest.BootEvent.RecordId } else { $null })
            OriginalBootCreatedUtc = $(if ($first) { $first.Guest.BootEvent.CreatedUtc } else { $null })
        }
        $final = Invoke-RestoreStage 'final' $context
        $report.FinalRestore = [pscustomobject]@{
            Verdict   = $final.Verdict
            Error     = $final.Error
            BootEvent = $final.Guest.BootEvent
            BootTime  = $final.Guest.BootTime
            Heartbeat = $final.Heartbeat
            Residue   = $final.Guest.RunResidue
        }
        if ($final.Verdict -ne 'pass') { throw "The final baseline restore failed: $($final.Error)" }
        if (-not $failure) { $report.LastSuccessfulStep = 'final_restore' }
    } catch {
        if (-not $failure) {
            $failure = $_.Exception.Message
            $report.Error = $failure
            $report.Verdict = 'blocked'
            $report.BlockingStep = 'final_restore'
        }
    }
}

if (-not $failure) { $report.Stage = 'complete' }
if ($guestSession) { Remove-PSSession -Session $guestSession -ErrorAction SilentlyContinue }
$report.FinishedAt = (Get-Date).ToString('o')
Save-PreflightReport
Write-Host "Preflight verdict: $($report.Verdict) at stage $($report.Stage)"
Write-Host "Evidence: $reportPath"
